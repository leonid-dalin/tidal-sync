"""
Data ingestion and metadata translation engine.

This module coordinates the bulk import of local CSV backups into a user's
Tidal library. It evaluates file paths to categorise the incoming data,
translates local text metadata into exact Tidal UUIDs using ISRC and text
fallbacks, and orchestrates the upload queues.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import tidalapi
from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..domain.logger import setup_audit_logging
from ..domain.models import AlbumRow, TrackRow
from ..domain.protocols import CHUNK_SIZE, TidalUser
from .folders import assign_playlist_to_v2_folder, ensure_v2_folder_exists
from .match_policy import decide
from .network import execute_network, fetch_all_async
from .parser import parse_csv
from .upload_recovery import upload_batch_with_recovery
from .workers import ImportStats, run_matching_tasks_async

console = Console()


async def import_collection_from_disk(
    session: tidalapi.Session, target_path: Path, target_playlist_name: str | None = None
) -> None:
    """
    Coordinates the bulk ingestion of metadata into the authenticated account.

    Evaluates whether the user has provided a single file or a directory,
    establishes the background telemetry audit sink, and prints the final
    statistical report upon completion.

    Args:
        session (tidalapi.Session): The authenticated Tidal session.
        target_path (Path): The file or directory path containing source CSVs.
        target_playlist_name (str | None): An optional override for the destination name.
    """
    log_file = setup_audit_logging(Path("./import_reports"))
    stats = ImportStats()
    logger.bind(audit=True).info("Import Job Started", target=str(target_path))

    if target_path.is_file():
        if target_path.suffix.lower() == ".csv":
            await resolve_and_import_playlist(session, target_path, target_playlist_name, stats)
        else:
            logger.error("Skipped non-CSV file", path=str(target_path))

    elif target_path.is_dir():
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {target_path}\n")
        failed_files: list[str] = []

        for file_path in sorted(target_path.rglob("*.csv")):
            try:
                await resolve_and_import_playlist(session, file_path, None, stats)
            except Exception as e:
                failed_files.append(f"{file_path.name}: {e}")
                logger.error("Import failed for {file}", file=file_path.name, error=repr(e))

        if failed_files:
            console.print(f"\n[bold red]{len(failed_files)} file(s) failed:[/bold red]")
            for detail in failed_files[:20]:
                console.print(f"  [dim]{detail}[/dim]")
    else:
        logger.error("Path not found", path=str(target_path))

    console.print("\n[bold yellow]Audit Report Generated:[/bold yellow]")
    console.print(f"  • {stats.added} items successfully imported")
    console.print(f"  • {stats.skipped} items skipped (already owned/duplicates)")
    console.print(f"  • {stats.failed} items failed (could not be found on Tidal)")
    console.print(f"  • Detailed machine-readable log: [underline]{log_file}[/underline]")


async def resolve_and_import_playlist(
    session: tidalapi.Session, file_path: Path, fallback_name: str | None, stats: ImportStats
) -> None:
    """
    Routes a specific CSV file to its appropriate category importer.

    Inspects the file's name to categorise the payload (e.g., routing
    'Liked Songs.csv' to the favourites importer). If the file does not
    match a known system category, it defaults to standard track processing
    and attempts to deduce its V2 folder location from the directory tree.

    Args:
        session (tidalapi.Session): The authenticated Tidal session.
        file_path (Path): The exact CSV file to process.
        fallback_name (str | None): The user-provided playlist name override, if any.
        stats (ImportStats): The shared session statistics counter.
    """
    filename = file_path.name
    parent_name = file_path.parent.name
    folder_name = None

    if parent_name in ["Mixes & Radios", "Mixes and Radios"]:
        folder_name = "Mixes & Radios"
    elif file_path.parent.parent.name == "Playlists":
        folder_name = parent_name

    # Routing logic (assuming other importers are defined here)
    if filename == "Liked Songs.csv":
        await import_tracks_category_async(session, file_path, stats, is_favorites=True)
    elif filename == "Liked Albums.csv":
        await import_albums_async(session, file_path, stats)
    else:
        p_name = fallback_name or file_path.stem
        await import_tracks_category_async(
            session,
            file_path,
            stats,
            is_favorites=False,
            playlist_name=p_name,
            folder_name=folder_name,
        )


async def resolve_track_to_id(
    session: Any,
    track_name: str,
    artist_name: str,
    tidal_id: str | None = None,
    isrc: str | None = None,
) -> str | None:
    """Resolves one track to a Tidal id, or None when nothing matches.

    Session.search() returns a SearchResults TypedDict, which is a plain
    dict at runtime, so results must be read by key. Reading them with
    getattr() always fell through to the default and every track without
    a direct Tidal id was reported as not found.
    """
    if tidal_id:
        return str(tidal_id)

    if isrc:
        results = await execute_network(session.search, f"isrc:{isrc}")
        tracks = results.get("tracks") or []
        if tracks:
            return str(tracks[0].id)

    primary_artist = artist_name.split(", ")[0].strip() if artist_name else ""
    query = f"{track_name} {primary_artist}".strip()
    results = await execute_network(session.search, query)
    tracks = results.get("tracks") or []
    if tracks:
        return str(tracks[0].id)

    return None


@dataclass
class UploadOutcome:
    """What a batch upload actually achieved.

    Tidal answers 200 and silently skips tracks it will not accept, so the
    accepted and rejected ids are reported separately rather than inferred
    from the absence of an exception.
    """

    applied: list[str]
    rejected: list[str]


def build_playlist_uploader(playlist: Any):
    """Builds a batch uploader that reports accepted and rejected ids.

    UserPlaylist.add() sends onArtifactNotFound=SKIP, so Tidal drops
    unavailable tracks server-side and returns a shortened addedItemIds
    list. Comparing that against the request is the only way to detect a
    region lock. allow_duplicates=True flips onDupes to ADD so a track
    already in the playlist is not mistaken for a refusal; the pre-scan
    owns dedup. add() also calls _reparse() internally, so no separate
    ETag refresh is needed here.
    """

    async def upload(batch: list[str]) -> UploadOutcome:
        added = await execute_network(playlist.add, batch, allow_duplicates=True)
        added_ids = {str(tid) for tid in (added or [])}
        applied = [tid for tid in batch if str(tid) in added_ids]
        rejected = [tid for tid in batch if str(tid) not in added_ids]
        return UploadOutcome(applied=applied, rejected=rejected)

    return upload


def _build_favorites_uploader(favorites: Any):
    """Builds a favorites uploader that stops at the first rejected track.

    Favorites are added one at a time and return no per-item result, so a
    failure means everything before it landed and nothing after it was
    attempted. Stopping there lets the caller resume from the failure
    instead of re-sending tracks that are already on the server.
    """

    async def upload(batch: list[str]) -> UploadOutcome:
        applied: list[str] = []
        for tid in batch:
            try:
                await execute_network(favorites.add_track, tid)
            except Exception as e:
                logger.warning("Favorites add failed for {id}: {error}", id=tid, error=str(e))
                return UploadOutcome(applied=applied, rejected=[tid])
            applied.append(tid)
        return UploadOutcome(applied=applied, rejected=[])

    return upload


async def import_tracks_category_async(
    session: tidalapi.Session,
    file_path: Path,
    stats: ImportStats,
    is_favorites: bool = False,
    playlist_name: str | None = None,
    folder_name: str | None = None,
) -> None:
    """
    Translates local track metadata into UUIDs and orchestrates batch uploads.

    Cross-references the incoming tracks against the user's existing library
    to prevent duplication. It then translates the local metadata into exact
    Tidal UUIDs, groups them into safe payload sizes, and hands them off to
    the fault recovery engine to manage the network uploads.
    """
    tracks = await asyncio.to_thread(parse_csv, file_path, TrackRow)
    if not tracks:
        return

    initial_added = stats.added
    initial_skipped = stats.skipped
    initial_failed = stats.failed
    user = cast(TidalUser, cast(object, session.user))

    dest_name = "Liked Songs" if is_favorites else playlist_name
    console.print(f"\n[cyan]Importing Tracks to:[/cyan] {dest_name}")

    existing_track_ids = set()
    playlist = None

    # 1. Deduplication and target acquisition
    with console.status(f"[cyan]Scanning existing items in '{dest_name}'...[/cyan]"):
        if is_favorites and hasattr(user, "favorites"):
            existing_track_ids = {str(t.id) for t in await fetch_all_async(user.favorites.tracks)}
        elif not is_favorites and playlist_name:
            existing_playlists = await fetch_all_async(user.playlists)
            playlist = next((p for p in existing_playlists if p.name == playlist_name), None)

            if playlist:
                existing_track_ids = {str(t.id) for t in await fetch_all_async(playlist.tracks)}
            else:
                playlist = await execute_network(
                    user.create_playlist, playlist_name, "Imported via tidal-sync <3"
                )

            if folder_name and playlist:
                folder_id = await ensure_v2_folder_exists(session, folder_name)
                if folder_id:
                    await assign_playlist_to_v2_folder(session, playlist.id, folder_id)

    track_ids_to_add: list[str] = []
    staged_tracks_map: dict[str, TrackRow] = {}

    # 2. Metadata translation
    async def _resolve_track_metadata_to_id(track: TrackRow) -> None:
        matched_id = await resolve_track_to_id(
            session,
            track_name=track.track_name,
            artist_name=track.artist_name,
            tidal_id=str(track.tidal_id) if track.tidal_id else None,
            isrc=str(track.isrc) if track.isrc else None,
        )

        failure_reason = (
            "ISRC mismatch & Text fallback failed" if track.isrc else "Text search failed"
        )
        if matched_id:
            staged_tracks_map[matched_id] = track

        await decide(
            matched_id,
            "Track",
            track.track_name,
            track.artist_name,
            file_path.name,
            str(dest_name),
            existing_track_ids,
            stats,
            ids_to_add=track_ids_to_add,
            failure_reason=failure_reason,
        )

    await run_matching_tasks_async(
        f"Matching {len(tracks)} tracks...", tracks, _resolve_track_metadata_to_id
    )

    # 3. Fault-tolerant batch uploading
    if track_ids_to_add:
        console.print(f"[cyan]Uploading {len(track_ids_to_add)} tracks to '{dest_name}'...[/cyan]")

        upload_chunk = (
            _build_favorites_uploader(user.favorites)
            if is_favorites and hasattr(user, "favorites")
            else build_playlist_uploader(playlist)
            if playlist
            else None
        )

        if upload_chunk is None:
            console.print(f"[yellow]No destination for '{dest_name}'; nothing uploaded.[/yellow]")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress_ui:
            add_task = progress_ui.add_task("Uploading...", total=len(track_ids_to_add))

            for i in range(0, len(track_ids_to_add), CHUNK_SIZE):
                chunk = track_ids_to_add[i : i + CHUNK_SIZE]

                await upload_batch_with_recovery(
                    chunk,
                    upload_chunk,
                    stats,
                    staged_tracks_map,
                    str(dest_name),
                    progress_ui,
                    add_task,
                )

    console.print(
        f"[green]✓ '{dest_name}' complete:[/green] "
        f"{stats.added - initial_added} uploaded | "
        f"{stats.skipped - initial_skipped} skipped | "
        f"{stats.failed - initial_failed} failed "
        f"[dim](Session Total: {stats.added})[/dim]\n"
    )


async def import_albums_async(
    session: tidalapi.Session, file_path: Path, stats: ImportStats
) -> None:
    """
    Matches and saves albums to the user's 'Liked Albums' collection.

    Parses the source file and cross-references it against the user's
    existing liked albums to prevent duplicate network calls. Executes
    the search and addition operations concurrently.
    """
    albums = await asyncio.to_thread(parse_csv, file_path, AlbumRow)
    if not albums:
        return

    user = cast(TidalUser, cast(object, session.user))

    # Guard clause to ensure favorites exists
    if not hasattr(user, "favorites"):
        logger.error("User profile does not support favorites.")
        return

    console.print("[cyan]Importing Liked Albums...[/cyan]")
    with console.status("[cyan]Scanning existing albums...[/cyan]"):
        existing_albums = await fetch_all_async(user.favorites.albums)
        existing_album_ids = {str(a.id) for a in existing_albums}

    async def _match_and_add_album_async(album: AlbumRow) -> None:
        matched_id = str(album.tidal_id) if album.tidal_id else None

        if not matched_id:
            results = await execute_network(
                session.search, f"{album.album_name} {album.artist_name}"
            )
            res_albums = results.get("albums") or []
            if res_albums:
                matched_id = str(res_albums[0].id)

        async def _async_add(a_id: str) -> None:
            # execute_network handles the async.to_thread wrapping internally
            await execute_network(user.favorites.add_album, a_id)

        failure_reason = "Text search failed" if not matched_id else "N/A"

        await decide(
            matched_id,
            "Album",
            album.album_name,
            album.artist_name,
            file_path.name,
            "Liked Albums",
            existing_album_ids,
            stats,
            add_method=_async_add,
            failure_reason=failure_reason,
        )

    await run_matching_tasks_async(
        f"Matching & Adding {len(albums)} albums...", albums, _match_and_add_album_async
    )
