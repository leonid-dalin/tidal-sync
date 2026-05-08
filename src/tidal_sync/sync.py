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

This module handles the extraction and restoration of Tidal playlists,
albums, tracks, and artists. It includes built-in rate limiting, array
chunking to prevent HTTP 413 errors, and an audit reporting engine to
track missing or duplicated items during imports.

Example:
    Exporting a user's entire library:

    >>> from sync import export_playlists
    >>> export_playlists(session, Path("./my_backup"))
"""

import csv
import time
from datetime import datetime
import tidalapi
from pathlib import Path
from typing import TypeVar, Any, cast
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console
from pydantic import BaseModel, ValidationError

from .models import TrackRow, AlbumRow, ArtistRow

console = Console()
T = TypeVar('T', bound=BaseModel)


def _fetch_all(api_method: Any, **kwargs: Any) -> list[Any]:
    """
    Exhaustively fetch paginated items from a Tidal API endpoint.

    Tidal limits responses to 50 items and occasionally drops region-locked
    tracks from the count. This helper bypasses those limits by manually
    advancing the offset until the server returns no new items.

    Args:
        api_method (Any): The Tidal API function to call (e.g., session.user.playlists).
        **kwargs: Additional arguments to pass to the API method.

    Returns:
        list[Any]: A complete list of all items from the endpoint.
    """
    items = []
    offset = 0
    limit = 50
    last_chunk_ids = []

    while True:
        try:
            chunk = api_method(limit=limit, offset=offset, **kwargs)
        except TypeError:
            res = api_method(**kwargs)
            return res if isinstance(res, list) else list(res)

        if not chunk:
            break

        current_chunk_ids = [getattr(item, 'id', id(item)) for item in chunk]

        # Infinite loop guard: detects if the API ignores the offset and repeats pages
        if offset > 0 and current_chunk_ids == last_chunk_ids:
            break

        items.extend(chunk)
        last_chunk_ids = current_chunk_ids
        offset += limit

    return items


def parse_csv(file_path: Path, model_class: type[T]) -> list[T]:
    """
    Read and validate a CSV file into Pydantic models.

    Args:
        file_path (Path): The location of the CSV file.
        model_class (type[T]): The Pydantic model to validate the rows against.

    Returns:
        list[T]: A list of validated row objects. Malformed rows are skipped and logged.
    """
    items = []
    # Using utf-8-sig to safely handle Byte Order Marks (BOM) from Windows/Excel exports
    with open(file_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                items.append(model_class(**row))
            except ValidationError as e:
                console.print(f"[red]Skipping invalid row in {file_path.name}:[/red]\n{e}")
    return items


def export_playlists(session: tidalapi.Session, output_dir: Path) -> None:
    """
    Download a user's entire Tidal library to local CSV files.

    Generates a folder structure containing all custom playlists, liked songs,
    saved albums, and followed artists.

    Args:
        session (tidalapi.Session): The active Tidal connection.
        output_dir (Path): The directory where the backup folders will be created.
    """
    user = cast(Any, session.user)
    if not user or not hasattr(user, 'favorites'):
        console.print("[red]Error: Could not access Tidal user profile.[/red]")
        return

    playlists_dir = output_dir / "Playlists"
    favorites_dir = output_dir / "Favorites"
    playlists_dir.mkdir(parents=True, exist_ok=True)
    favorites_dir.mkdir(parents=True, exist_ok=True)

    playlists = _fetch_all(user.playlists)
    with Progress(console=console) as progress:
        task = progress.add_task("Exporting playlists...", total=len(playlists))
        for playlist in playlists:
            safe_name = "".join(c for c in playlist.name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            out_file = playlists_dir / f"{safe_name}.csv"

            with open(out_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC", "Tidal - id"])
                playlist_tracks = _fetch_all(playlist.tracks)
                for t in playlist_tracks:
                    writer.writerow([
                        t.name,
                        t.artist.name if getattr(t, 'artist', None) else "",
                        t.album.name if getattr(t, 'album', None) else "",
                        playlist.name, "Playlist", getattr(t, 'isrc', ""), t.id
                    ])
            progress.advance(task)

    console.print("\n[cyan]Fetching Liked Songs (Paginated)...[/cyan]")
    fav_tracks = _fetch_all(user.favorites.tracks)
    with open(favorites_dir / "Liked Songs.csv", 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC", "Tidal - id"])
        for t in fav_tracks:
            writer.writerow([
                t.name,
                t.artist.name if getattr(t, 'artist', None) else "",
                t.album.name if getattr(t, 'album', None) else "",
                "Liked Songs", "Track", getattr(t, 'isrc', ""), t.id
            ])

    console.print("[cyan]Fetching Liked Albums (Paginated)...[/cyan]")
    fav_albums = _fetch_all(user.favorites.albums)
    with open(favorites_dir / "Liked Albums.csv", 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Album name", "Artist name", "Type", "Tidal - id"])
        for a in fav_albums:
            writer.writerow([a.name, a.artist.name if getattr(a, 'artist', None) else "", "Album", a.id])

    console.print("[cyan]Fetching Followed Artists (Paginated)...[/cyan]")
    fav_artists = _fetch_all(user.favorites.artists)
    with open(favorites_dir / "Followed Artists.csv", 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Artist name", "Type", "Tidal - id"])
        for art in fav_artists:
            writer.writerow([art.name, "Artist", art.id])

    console.print(f"\n[green]Successfully exported {len(playlists)} playlists and all favourites![/green]")


# --- IMPORT ARCHITECTURE ---

def import_target(session: tidalapi.Session, target_path: Path, target_playlist_name: str | None = None) -> None:
    """
    Route a file or directory into the Tidal library.

    Scans the provided path, triggers the correct import functions based on
    filenames, and generates an audit report detailing any tracks that were
    skipped or failed to match.

    Args:
        session (tidalapi.Session): The active Tidal connection.
        target_path (Path): A specific CSV file or a directory containing multiple CSVs.
        target_playlist_name (str | None): Override name for single-file playlist imports.
    """
    report_records: list[dict[str, str]] = []

    if target_path.is_file():
        if target_path.suffix.lower() == '.csv':
            _route_and_import(session, target_path, target_playlist_name, report_records)
        else:
            console.print(f"[red]Skipping non-CSV file:[/red] {target_path}")
    elif target_path.is_dir():
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {target_path}")
        csv_files = list(target_path.rglob("*.csv"))
        for file_path in csv_files:
            _route_and_import(session, file_path, None, report_records)
    else:
        console.print(f"[red]Path not found:[/red] {target_path}")

    # Generate Audit Report if there were any failures or skips
    if report_records:
        report_dir = Path("./import_reports")
        report_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = report_dir / f"import_report_{timestamp}.csv"

        with open(report_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Status", "Type", "Item Name", "Artist", "Source File", "Details"])
            for r in report_records:
                writer.writerow([r['status'], r['type'], r['name'], r['artist'], r['source_file'], r['details']])

        failed_count = sum(1 for r in report_records if "Failed" in r['status'])
        skipped_count = sum(1 for r in report_records if "Skipped" in r['status'])

        console.print(f"\n[bold yellow]Audit Report Generated:[/bold yellow]")
        console.print(f"  • {skipped_count} items skipped (already owned/duplicates)")
        console.print(f"  • {failed_count} items failed (could not be found on Tidal)")
        console.print(f"  • Report saved to: [underline]{report_file}[/underline]")


def _route_and_import(session: tidalapi.Session, file_path: Path, fallback_name: str | None, report_records: list[dict]) -> None:
    """Internal router directing specific CSV files to their respective import handlers."""
    filename = file_path.name
    if filename == "Liked Albums.csv":
        _import_albums(session, file_path, report_records)
    elif filename == "Followed Artists.csv":
        _import_artists(session, file_path, report_records)
    elif filename == "Liked Songs.csv":
        _import_tracks(session, file_path, report_records, is_favorites=True)
    else:
        p_name = fallback_name or file_path.stem
        _import_tracks(session, file_path, report_records, is_favorites=False, playlist_name=p_name)


def _import_tracks(session: tidalapi.Session, file_path: Path, report_records: list[dict], is_favorites: bool = False,
                   playlist_name: str | None = None):
    """Parse a track CSV, match entries against the Tidal database, and add them to the library."""
    tracks = parse_csv(file_path, TrackRow)
    if not tracks: return

    user = cast(Any, session.user)
    dest_name = "Liked Songs" if is_favorites else playlist_name
    console.print(f"\n[cyan]Importing Tracks to:[/cyan] {dest_name}")

    existing_track_ids = set()
    playlist = None

    if is_favorites and hasattr(user, 'favorites'):
        existing_track_ids = {str(t.id) for t in _fetch_all(user.favorites.tracks)}
    elif not is_favorites and playlist_name:
        existing_playlists = _fetch_all(user.playlists)
        playlist = next((p for p in existing_playlists if p.name == playlist_name), None)
        if playlist:
            existing_track_ids = {str(t.id) for t in _fetch_all(playlist.tracks)}
        else:
            playlist = user.create_playlist(playlist_name, "Imported via tidal-sync")

    track_ids_to_add = []
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(),
                  TaskProgressColumn(), console=console) as progress:
        task = progress.add_task(f"Matching {len(tracks)} tracks...", total=len(tracks))

        for track in tracks:
            matched_id = track.tidal_id
            if not matched_id and track.isrc:
                results = session.search(f"isrc:{str(track.isrc)}")
                res_tracks = getattr(results, 'tracks', [])
                if res_tracks: matched_id = str(res_tracks[0].id)

            if not matched_id:
                results = session.search(str(track.search_query))
                res_tracks = getattr(results, 'tracks', [])
                if res_tracks: matched_id = str(res_tracks[0].id)

            if matched_id:
                if matched_id not in existing_track_ids:
                    track_ids_to_add.append(matched_id)
                    existing_track_ids.add(matched_id)
                else:
                    report_records.append({"status": "Skipped (Duplicate)", "type": "Track", "name": track.track_name,
                                           "artist": track.artist_name, "source_file": file_path.name,
                                           "details": f"Already in {dest_name}"})
            else:
                report_records.append({"status": "Failed (Not Found)", "type": "Track", "name": track.track_name,
                                       "artist": track.artist_name, "source_file": file_path.name,
                                       "details": "Search yielded 0 results"})

            time.sleep(0.5)  # Polite delay between API hits
            progress.advance(task)

    if track_ids_to_add:
        if is_favorites and hasattr(user, 'favorites'):
            for tid in track_ids_to_add:
                user.favorites.add_track(tid)
                time.sleep(0.1)
        elif playlist:
            # Array Chunking for large playlists to prevent HTTP 413 Payload Too Large
            CHUNK_SIZE = 50
            for i in range(0, len(track_ids_to_add), CHUNK_SIZE):
                chunk = track_ids_to_add[i:i + CHUNK_SIZE]
                playlist.add(chunk)
                time.sleep(0.5)

        console.print(f"[green]Added {len(track_ids_to_add)} NEW tracks![/green]")


def _import_albums(session: tidalapi.Session, file_path: Path, report_records: list[dict]):
    """Parse an album CSV, match entries against the Tidal database, and add them to favourites."""
    albums = parse_csv(file_path, AlbumRow)
    if not albums: return
    user = cast(Any, session.user)

    console.print("\n[cyan]Importing Liked Albums...[/cyan]")
    existing_album_ids = {str(a.id) for a in _fetch_all(user.favorites.albums)} if hasattr(user, 'favorites') else set()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(),
                  TaskProgressColumn(), console=console) as progress:
        task = progress.add_task(f"Matching {len(albums)} albums...", total=len(albums))
        for album in albums:
            matched_id = str(album.tidal_id) if album.tidal_id else None
            if not matched_id:
                results = session.search(f"{album.album_name} {album.artist_name}")
                res_albums = getattr(results, 'albums', [])
                if res_albums: matched_id = str(res_albums[0].id)

            if matched_id:
                if matched_id not in existing_album_ids:
                    if hasattr(user, 'favorites'): user.favorites.add_album(matched_id)
                    existing_album_ids.add(matched_id)
                else:
                    report_records.append({"status": "Skipped (Duplicate)", "type": "Album", "name": album.album_name,
                                           "artist": album.artist_name, "source_file": file_path.name,
                                           "details": "Already in Liked Albums"})
            else:
                report_records.append({"status": "Failed (Not Found)", "type": "Album", "name": album.album_name,
                                       "artist": album.artist_name, "source_file": file_path.name,
                                       "details": "Search yielded 0 results"})

            time.sleep(0.5)
            progress.advance(task)


def _import_artists(session: tidalapi.Session, file_path: Path, report_records: list[dict]):
    """Parse an artist CSV, match entries against the Tidal database, and add them to favourites."""
    artists = parse_csv(file_path, ArtistRow)
    if not artists: return
    user = cast(Any, session.user)

    console.print("\n[cyan]Importing Followed Artists...[/cyan]")
    existing_artist_ids = {str(a.id) for a in _fetch_all(user.favorites.artists)} if hasattr(user,
                                                                                             'favorites') else set()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(),
                  TaskProgressColumn(), console=console) as progress:
        task = progress.add_task(f"Matching {len(artists)} artists...", total=len(artists))
        for artist in artists:
            matched_id = str(artist.tidal_id) if artist.tidal_id else None
            if not matched_id:
                results = session.search(str(artist.artist_name))
                res_artists = getattr(results, 'artists', [])
                if res_artists: matched_id = str(res_artists[0].id)

            if matched_id:
                if matched_id not in existing_artist_ids:
                    if hasattr(user, 'favorites'): user.favorites.add_artist(matched_id)
                    existing_artist_ids.add(matched_id)
                else:
                    report_records.append(
                        {"status": "Skipped (Duplicate)", "type": "Artist", "name": artist.artist_name, "artist": "N/A",
                         "source_file": file_path.name, "details": "Already in Followed Artists"})
            else:
                report_records.append(
                    {"status": "Failed (Not Found)", "type": "Artist", "name": artist.artist_name, "artist": "N/A",
                     "source_file": file_path.name, "details": "Search yielded 0 results"})

            time.sleep(0.5)
            progress.advance(task)


def clear_library(session: tidalapi.Session, target: str):
    """
    Destructively remove items from the user's library.

    Args:
        session (tidalapi.Session): The active Tidal connection.
        target (str): The category to clear. Options: 'all', 'playlists', 'tracks', 'albums', 'artists'.
    """
    user = cast(Any, session.user)
    if not hasattr(user, 'favorites'): return

    if target in ["all", "playlists"]:
        playlists = _fetch_all(user.playlists)
        console.print(f"[cyan]Deleting {len(playlists)} playlists...[/cyan]")
        for p in playlists:
            p.delete()
            time.sleep(0.1)

    if target in ["all", "tracks"]:
        tracks = _fetch_all(user.favorites.tracks)
        console.print(f"[cyan]Removing {len(tracks)} liked songs...[/cyan]")
        for t in tracks:
            user.favorites.remove_track(t.id)
            time.sleep(0.1)

    if target in ["all", "albums"]:
        albums = _fetch_all(user.favorites.albums)
        console.print(f"[cyan]Removing {len(albums)} liked albums...[/cyan]")
        for a in albums:
            user.favorites.remove_album(a.id)
            time.sleep(0.1)

    if target in ["all", "artists"]:
        artists = _fetch_all(user.favorites.artists)
        console.print(f"[cyan]Unfollowing {len(artists)} artists...[/cyan]")
        for art in artists:
            user.favorites.remove_artist(art.id)
            time.sleep(0.1)

    console.print(f"[bold green]Successfully cleared '{target}' from library.[/bold green]")