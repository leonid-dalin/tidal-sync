# tidal-sync: A high-performance tool for backing up and cloning Tidal libraries.
# Copyright (C) 2026 Leonid Dalin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: infoLeonid@protonMail.com
"""
Core synchronisation logic for Tidal libraries.

This module handles extracting and restoring Tidal playlists, albums,
tracks, and artists. It includes built-in rate limiting, array chunking
to prevent HTTP 413 errors, and an audit reporting engine to track
missing or duplicated items during imports.

Example:
    >>> from sync import export_playlists
    >>> export_playlists(session, Path("./my_backup"))
"""
import csv
import asyncio
from pathlib import Path
from typing import Any, cast, Callable
import tidalapi
from loguru import logger

from requests.exceptions import HTTPError
from tidalapi.exceptions import ObjectNotFound
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console

from .domain.models import TrackRow, AlbumRow, ArtistRow
from .domain.enums import ClearTarget
from .domain.protocols import TidalUser, CHUNK_SIZE
from .domain.logger import setup_audit_logging

from .engine.network import execute_network, fetch_all_async, fetch_blocked_artists
from .engine.workers import (
    ImportStats,
    handle_match_result_async,
    run_matching_tasks_async,
    run_headless_tasks_async
)
from .engine.parser import parse_csv

console = Console()

MEDIUM_DELAY = 0.2


async def export_playlists_async(session: tidalapi.Session, output_dir: Path) -> None:
    """
    Downloads a user's entire Tidal library to local CSV files concurrently

    Generates a folder structure containing all custom playlists, liked songs,
    saved albums, and followed artists.

    Args:
        session (tidalapi.Session): The active Tidal connection.
        output_dir (Path): The directory where the backup folders will go.
    """
    user = cast(TidalUser, cast(object, session.user))
    if not user or not hasattr(user, 'favorites'):
        console.print("[red]Error: Could not access Tidal user profile.[/red]")
        return

    playlists_dir = output_dir / "Playlists"
    favorites_dir = output_dir / "Favorites"
    playlists_dir.mkdir(parents=True, exist_ok=True)
    favorites_dir.mkdir(parents=True, exist_ok=True)

    playlists = await fetch_all_async(user.playlists)


    async def _export_single_playlist_async(playlist: Any) -> None:
        safe_name = "".join(c for c in playlist.name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        out_file = playlists_dir / f"{safe_name}.csv"

        playlist_tracks = await fetch_all_async(playlist.tracks)

        # File I/O offloaded to thread to prevent blocking event loop during large writes
        def _write_csv():
            with open(out_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC", "Tidal - id"])
                for t in playlist_tracks:
                    writer.writerow([
                        t.name,
                        getattr(t.artist, 'name', ""),
                        getattr(t.album, 'name', ""),
                        playlist.name, "Playlist", getattr(t, 'isrc', ""), t.id
                    ])

        await asyncio.to_thread(_write_csv)

    await run_matching_tasks_async("Exporting playlists...", playlists, _export_single_playlist_async)

    console.print("\n[cyan]Fetching Liked Songs...[/cyan]")
    fav_tracks = await fetch_all_async(user.favorites.tracks)

    def _write_favorites():
        with open(favorites_dir / "Liked Songs.csv", 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC", "Tidal - id"])
            for t in fav_tracks:
                writer.writerow([
                    t.name, getattr(t.artist, 'name', ""),
                    getattr(t.album, 'name', ""),
                    "Liked Songs", "Track", getattr(t, 'isrc', ""), t.id
                ])

    await asyncio.to_thread(_write_favorites)

    console.print("[cyan]Fetching Liked Albums...[/cyan]")
    fav_albums = await fetch_all_async(user.favorites.albums)
    def _write_albums():
        with open(favorites_dir / "Liked Albums.csv", 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Album name", "Artist name", "Type", "Tidal - id"])
            for a in fav_albums:
                writer.writerow([a.name, getattr(a.artist, 'name', ""), "Album", a.id])
    await asyncio.to_thread(_write_albums)

    console.print("[cyan]Fetching Followed Artists...[/cyan]")
    fav_artists = await fetch_all_async(user.favorites.artists)
    def _write_artists():
        with open(favorites_dir / "Followed Artists.csv", 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Artist name", "Type", "Tidal - id"])
            for art in fav_artists:
                writer.writerow([art.name, "Artist", art.id])

    await asyncio.to_thread(_write_artists)

    console.print("[cyan]Fetching Blocked Artists...[/cyan]")
    blocked_artists = await execute_network(fetch_blocked_artists, session)
    if blocked_artists:
        def _write_blocked():
            with open(favorites_dir / "Blocked Artists.csv", 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Artist name", "Type", "Tidal - id"])
                for art in blocked_artists:
                    writer.writerow([art.name, "Artist", art.id])
        await asyncio.to_thread(_write_blocked)

    console.print(f"\n[green]Successfully exported {len(playlists)} playlists and all favourites![/green]")


async def import_target_async(session: tidalapi.Session, target_path: Path, target_playlist_name: str | None = None) -> None:
    """
    Routes a file or directory into the Tidal library.

    Scans the provided path, triggers the correct import functions based on
    filenames, and generates an audit report detailing any tracks that were
    skipped or failed to match.

    Args:
        session (tidalapi.Session): The active Tidal connection.
        target_path (Path): A specific CSV file or a directory containing multiple CSVs.
        target_playlist_name (str | None): Override name for single-file playlist imports.
    """
    log_file = setup_audit_logging(Path("./import_reports"))

    stats = ImportStats()
    logger.bind(audit=True).info("Import Job Started", target=str(target_path))

    if target_path.is_file():
        if target_path.suffix.lower() == '.csv':
            await _route_and_import_async(session, target_path, target_playlist_name, stats)
        else:
            logger.error("Skipped non-CSV file", path=str(target_path))
    elif target_path.is_dir():
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {target_path}")
        csv_files = list(target_path.rglob("*.csv"))
        for file_path in csv_files:
            await _route_and_import_async(session, file_path, None, stats)
    else:
        logger.error("Path not found", path=str(target_path))

    console.print(f"\n[bold yellow]Audit Report Generated:[/bold yellow]")
    console.print(f"  • {stats.added} items successfully imported")
    console.print(f"  • {stats.skipped} items skipped (already owned/duplicates)")
    console.print(f"  • {stats.failed} items failed (could not be found on Tidal)")
    console.print(f"  • Detailed machine-readable log: [underline]{log_file}[/underline]")

async def _route_and_import_async(session: tidalapi.Session, file_path: Path, fallback_name: str | None, stats: ImportStats) -> None:
    """
    Directs specific CSV files to their respective import handlers based on filenames.
    """
    filename = file_path.name
    if filename == "Liked Albums.csv":
        await _import_albums_async(session, file_path, stats)
    elif filename == "Followed Artists.csv":
        await _import_artists_async(session, file_path, stats)
    elif filename == "Blocked Artists.csv":
        await _import_blocked_artists_async(session, file_path, stats)
    elif filename == "Liked Songs.csv":
        await _import_tracks_async(session, file_path, stats, is_favorites=True)
    else:
        p_name = fallback_name or file_path.stem
        await _import_tracks_async(session, file_path, stats, is_favorites=False, playlist_name=p_name)


async def _import_tracks_async(session: tidalapi.Session, file_path: Path, stats: ImportStats, is_favorites: bool = False, playlist_name: str | None = None) -> None:
    """
    Parses a track CSV, matches entries against Tidal, and adds them to the library.

    Includes a bisection fallback to handle region-locked tracks that cause
    entire batch uploads to fail.
    """
    tracks = await asyncio.to_thread(parse_csv, file_path, TrackRow)
    if not tracks: return

    initial_added = stats.added
    initial_skipped = stats.skipped
    initial_failed = stats.failed

    user = cast(TidalUser, cast(object, session.user))
    dest_name = "Liked Songs" if is_favorites else playlist_name
    console.print(f"\n[cyan]Importing Tracks to:[/cyan] {dest_name}")

    existing_track_ids = set()
    playlist = None

    with console.status(f"[cyan]Scanning existing items in '{dest_name}'...[/cyan]"):
        if is_favorites and hasattr(user, 'favorites'):
            existing_track_ids = {str(t.id) for t in await fetch_all_async(user.favorites.tracks)}
        elif not is_favorites and playlist_name:
            existing_playlists = await fetch_all_async(user.playlists)
            playlist = next((p for p in existing_playlists if p.name == playlist_name), None)
            if playlist:
                existing_track_ids = {str(t.id) for t in await fetch_all_async(playlist.tracks)}
            else:
                playlist = await execute_network(user.create_playlist, playlist_name, "Imported via tidal-sync <3")

    track_ids_to_add: list[str] = []
    staged_tracks_map: dict[str, TrackRow] = {}

    async def _match_and_stage_track_async(track: TrackRow) -> None:
        matched_id = str(track.tidal_id) if track.tidal_id else None
        if not matched_id and track.isrc:
            results = await execute_network(session.search, f"isrc:{str(track.isrc)}")
            res_tracks = getattr(results, 'tracks', [])
            if res_tracks: matched_id = str(res_tracks[0].id)

        if not matched_id:
            results = await execute_network(session.search, str(track.search_query))
            res_tracks = getattr(results, 'tracks', [])
            if res_tracks: matched_id = str(res_tracks[0].id)

        failure_reason = "Not Found"
        if not matched_id:
            failure_reason = "ISRC mismatch & Text fallback failed" if track.isrc else "Text search failed (No ISRC provided)"
        else:
            staged_tracks_map[matched_id] = track

        await handle_match_result_async(
            matched_id, "Track", track.track_name, track.artist_name,
            file_path.name, str(dest_name), existing_track_ids, stats,
            ids_to_add=track_ids_to_add, failure_reason=failure_reason
        )

    await run_matching_tasks_async(f"Matching {len(tracks)} tracks...", tracks, _match_and_stage_track_async)

    if track_ids_to_add:
        console.print(f"[cyan]Uploading {len(track_ids_to_add)} tracks to '{dest_name}'...[/cyan]")

        # Apply the rate-limit shield to the upload action specifically
        async def _upload_chunk_async(batch: list[str]) -> None:
            nonlocal playlist
            if is_favorites and hasattr(user, 'favorites'):
                await execute_network(user.favorites.add_track, batch)
            elif playlist:
                playlist = await execute_network(session.playlist, playlist.id)
                await execute_network(playlist.add, batch)

        with Progress(SpinnerColumn(),
                      TextColumn("[progress.description]{task.description}"),
                      BarColumn(),
                      TaskProgressColumn(),
                      console=console) as progress:
            add_task = progress.add_task("Uploading...", total=len(track_ids_to_add))

            for i in range(0, len(track_ids_to_add), CHUNK_SIZE):
                chunk = track_ids_to_add[i:i + CHUNK_SIZE]
                try:
                    await _upload_chunk_async(chunk)
                    await stats.add_added(len(chunk))
                    for tid in chunk:
                        logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)
                except (HTTPError, ObjectNotFound) as e:
                    # GUARD: Do not bisect if the error is a 412 ETag mismatch
                    is_etag_error = isinstance(e, HTTPError) and getattr(e.response, 'status_code', None) == 412

                    if is_etag_error:
                        logger.bind(audit=True).warning("HTTP 412 (Stale ETag) detected. Retrying chunk...")
                        try:
                            await asyncio.sleep(1.0)
                            await _upload_chunk_async(chunk)
                            await stats.add_added(len(chunk))
                            for tid in chunk:
                                logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)
                            progress.advance(add_task, advance=len(chunk))
                            continue
                        except Exception as retry_e:
                            e = retry_e
                    logger.bind(audit=True).error("Chunk rejected, initiating bisection", chunk_size=len(chunk), error=str(e))

                    # Batch uploads fail entirely if even one track is region-locked.
                    # This recursive bisection isolates and drops the poison track.
                    async def _bisect_upload_async(sub_chunk: list[str]) -> None:
                        if not sub_chunk: return
                        try:
                            await _upload_chunk_async(sub_chunk)
                            await stats.add_added(len(sub_chunk))
                            for tid in sub_chunk:
                                logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)
                        except (HTTPError, ObjectNotFound) as _:
                            if len(sub_chunk) == 1:
                                poison_id = sub_chunk[0]
                                poison_track = staged_tracks_map.get(poison_id)

                                track_title = poison_track.track_name if poison_track else "Unknown"
                                track_artist = poison_track.artist_name if poison_track else "Unknown"

                                logger.bind(audit=True).error(
                                    "Dropped Track (Region Locked)",
                                    track_id=poison_id,
                                    name=track_title,
                                    artist=track_artist,
                                    dest=dest_name
                                )
                                console.print(
                                    f"  [red]❌ Dropped (Region-locked): {track_title} by {track_artist}[/red]")

                                await stats.add_failed()
                            else:
                                await asyncio.sleep(MEDIUM_DELAY * 2)
                                mid = len(sub_chunk) // 2
                                await _bisect_upload_async(sub_chunk[:mid])
                                await _bisect_upload_async(sub_chunk[mid:])

                    await _bisect_upload_async(chunk)

                progress.advance(add_task, advance=len(chunk))
                await asyncio.sleep(MEDIUM_DELAY*2)

        console.print(f"[green]✓ '{dest_name}' complete:[/green] "
                      f"{stats.added - initial_added} uploaded | "
                      f"{stats.skipped - initial_skipped} skipped | "
                      f"{stats.failed - initial_failed} failed "
                      f"[dim](Session Total: {stats.added})[/dim]\n")


async def _import_albums_async(session: tidalapi.Session, file_path: Path, stats: ImportStats) -> None:
    """
    Parses an album CSV, matches entries against Tidal, and adds them to favourites.
    """
    albums = await asyncio.to_thread(parse_csv, file_path, AlbumRow)
    if not albums: return
    user = cast(TidalUser, cast(object, session.user))

    console.print("[cyan]Importing Liked Albums...[/cyan]")
    with console.status("[cyan]Scanning existing albums...[/cyan]"):
        existing_album_ids = {str(a.id) for a in await fetch_all_async(user.favorites.albums)} if hasattr(user,
                                                                                                          'favorites') else set()

    def _sync_add_album(a_id: str):
        if hasattr(user, 'favorites'):
            user.favorites.add_album(a_id)

    
    async def _match_and_add_album_async(album: AlbumRow) -> None:
        matched_id = str(album.tidal_id) if album.tidal_id else None
        if not matched_id:
            results = await execute_network(session.search, f"{album.album_name} {album.artist_name}")
            res_albums = getattr(results, 'albums', [])
            if res_albums: matched_id = str(res_albums[0].id)

        async def _async_add(a_id: str):
            await execute_network(_sync_add_album, a_id)

        await handle_match_result_async(
            matched_id, "Album", album.album_name, album.artist_name,
            file_path.name, "Liked Albums", existing_album_ids, stats,
            add_method=_async_add if hasattr(user, 'favorites') else None,
            failure_reason="Text search failed" if not matched_id else "N/A"
        )

    await run_matching_tasks_async(f"Matching & Adding {len(albums)} albums...", albums, _match_and_add_album_async)


async def _import_artists_async(session: tidalapi.Session, file_path: Path, stats: ImportStats) -> None:
    """
    Parses an artist CSV, matches entries against Tidal, and follows them.
    """
    artists = await asyncio.to_thread(parse_csv, file_path, ArtistRow)
    if not artists: return
    user = cast(TidalUser, cast(object, session.user))

    console.print("\n[cyan]Importing Followed Artists...[/cyan]")
    with console.status("[cyan]Scanning existing followed artists...[/cyan]"):
        existing_artist_ids = {str(a.id) for a in await fetch_all_async(user.favorites.artists)} if hasattr(user, 'favorites') else set()

    def _sync_add_artist(art_id: str):
        if hasattr(user, 'favorites'):
            user.favorites.add_artist(art_id)

    
    async def _match_and_add_artist_async(artist: ArtistRow) -> None:
        matched_id = str(artist.tidal_id) if artist.tidal_id else None
        if not matched_id:
            results = await execute_network(session.search, str(artist.artist_name))
            res_artists = getattr(results, 'artists', [])
            if res_artists: matched_id = str(res_artists[0].id)

        async def _async_add(art_id: str):
            await execute_network(_sync_add_artist, art_id)

        await handle_match_result_async(
            matched_id, "Artist", artist.artist_name, "N/A",
            file_path.name, "Followed Artists", existing_artist_ids, stats,
            add_method=_async_add if hasattr(user, 'favorites') else None,
            failure_reason="Text search failed" if not matched_id else "N/A"
        )

    await run_matching_tasks_async(f"Matching & Adding {len(artists)} artists...", artists, _match_and_add_artist_async)


async def _import_blocked_artists_async(session: tidalapi.Session, file_path: Path, stats: ImportStats) -> None:
    """
    Parses an artist CSV, matches entries against Tidal, and blocks/mutes them.
    """
    artists = await asyncio.to_thread(parse_csv, file_path, ArtistRow)
    if not artists: return
    user = session.user

    console.print("\n[cyan]Importing Blocked Artists...[/cyan]")
    with console.status("[cyan]Scanning existing blocked artists...[/cyan]"):
        existing_blocked_ids = {str(a.id) for a in await execute_network(fetch_blocked_artists, session)}

    def _sync_execute_block(artist_id: str) -> bool:
        endpoint = f"users/{user.id}/blocks/artists"
        response = session.request.request("POST", endpoint, data={"artistId": artist_id})
        return response.ok

    
    async def _match_and_block_artist_async(artist: ArtistRow) -> None:
        matched_id = str(artist.tidal_id) if artist.tidal_id else None
        if not matched_id:
            results = await execute_network(session.search, str(artist.artist_name))
            res_artists = getattr(results, 'artists', [])
            if res_artists: matched_id = str(res_artists[0].id)

        async def _async_add(art_id: str):
             await execute_network(_sync_execute_block, art_id)

        await handle_match_result_async(
            matched_id, "Blocked Artist", artist.artist_name, "N/A",
            file_path.name, "Blocked Artists", existing_blocked_ids, stats,
            add_method=_async_add,
            failure_reason="Text search failed" if not matched_id else "N/A"
        )

    await run_matching_tasks_async(f"Matching & Blocking {len(artists)} artists...", artists, _match_and_block_artist_async)


async def clear_library_async(session: tidalapi.Session, target: ClearTarget) -> None:
    """
    Destructively removes items from the user's library.

    Args:
        session (tidalapi.Session): The active Tidal connection.
        target (ClearTarget): The category to clear.
    """
    user = cast(TidalUser, cast(object, session.user))
    if not hasattr(user, 'favorites'): return

    
    async def _execute_delete_async(task_func: Callable[[], Any]) -> None:
        await execute_network(task_func)

    async def _clear_category_async(items: list[Any], sync_action_factory: Callable[[Any], Callable[[], Any]], category_name: str) -> None:
        if not items: return
        console.print(f"[cyan]Removing {len(items)} {category_name}...[/cyan]")
        async def _async_wrapper(item: Any):
            await _execute_delete_async(sync_action_factory(item))

        await run_headless_tasks_async(items, _async_wrapper)

    if target in (ClearTarget.ALL, ClearTarget.PLAYLISTS):
        playlists = await fetch_all_async(user.playlists)
        await _clear_category_async(playlists, lambda p: p.delete, "playlists")

    if target in (ClearTarget.ALL, ClearTarget.TRACKS):
        tracks = await fetch_all_async(user.favorites.tracks)
        await _clear_category_async(tracks, lambda t: lambda: user.favorites.remove_track(str(t.id)), "liked songs")

    if target in (ClearTarget.ALL, ClearTarget.ALBUMS):
        albums = await fetch_all_async(user.favorites.albums)
        await _clear_category_async(albums, lambda a: lambda: user.favorites.remove_album(str(a.id)), "liked albums")

    if target in (ClearTarget.ALL, ClearTarget.ARTISTS):
        artists = await fetch_all_async(user.favorites.artists)
        await _clear_category_async(artists, lambda art: lambda: user.favorites.remove_artist(str(art.id)), "artists")

    console.print(f"[bold green]Successfully cleared '{target.value}' from library.[/bold green]")