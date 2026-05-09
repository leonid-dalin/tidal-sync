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
import time
import threading
import concurrent.futures
from functools import wraps
from pathlib import Path
from dataclasses import dataclass
from typing import TypeVar, Any, cast, Callable
import tidalapi
from loguru import logger

from requests.exceptions import HTTPError
from tidalapi.exceptions import TooManyRequests, ObjectNotFound
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console
from pydantic import BaseModel, ValidationError

from .domain.models import TrackRow, AlbumRow, ArtistRow
from .domain.enums import ClearTarget
from .domain.protocols import TidalUser, CHUNK_SIZE
from .domain.logger import setup_audit_logging

console = Console()
T = TypeVar('T', bound=BaseModel)
SHORT_DELAY = 0.1
MEDIUM_DELAY = 0.2
MAX_WORKERS = 8


@dataclass
class ImportStats:
    """
    Thread-safe counter for the final terminal summary.

    Tracks the number of skipped, failed, and added items during an
    import session across multiple concurrent threads.
    """
    skipped: int = 0
    failed: int = 0
    added: int = 0
    lock: threading.Lock = threading.Lock()

    def add_skipped(self) -> None:
        with self.lock: self.skipped += 1
    def add_failed(self) -> None:
        with self.lock: self.failed += 1
    def add_added(self, count: int = 1) -> None:
        with self.lock: self.added += count


def retry_on_429(max_retries: int = 5, backoff_factor: float = 1.5) -> Callable:
    """
    Handles Tidal API rate limits (HTTP 429) automatically via exponential backoff.

    If the API returns a 'retry_after' value, it waits exactly that long.
    Otherwise, it falls back to multiplying the delay by the backoff factor.

    Args:
        max_retries (int): Maximum number of retry attempts. Defaults to 5.
        backoff_factor (float): Multiplier for the delay time. Defaults to 1.5.

    Returns:
        Callable: The decorated function.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except TooManyRequests as e:
                    retry_after = getattr(e, 'retry_after', -1)
                    sleep_time = retry_after if retry_after > 0 else (backoff_factor ** retries)
                    logger.warning("Rate limited (429)", retry_after=sleep_time, attempt=retries+1)
                    time.sleep(sleep_time)
                    retries += 1
                except Exception as e:
                    # Fallback catch for generic 429s parsed as standard HTTP errors
                    if "429" in str(e):
                        time.sleep(backoff_factor ** retries)
                        retries += 1
                    else:
                        raise
            return func(*args, **kwargs)  # Final attempt
        return wrapper
    return decorator


def _fetch_all(api_method: Any, **kwargs: Any) -> list[Any]:
    """
    Exhaustively fetches paginated items from a Tidal API endpoint.

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
    Reads and validates a CSV file into Pydantic models.

    Args:
        file_path (Path): The location of the CSV file.
        model_class (type[T]): The Pydantic model to validate the rows against.

    Returns:
        list[T]: A list of validated row objects. Malformed rows are skipped and logged.
    """
    items = []
    # We use 'utf-8-sig' to safely strip the Byte Order Mark (BOM) often injected by Windows/Excel exports
    with open(file_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row.pop(None, None)
            try:
                items.append(model_class(**row))
            except ValidationError as e:
                logger.error("CSV Validation Error", file=file_path.name, error=str(e))
    return items


def export_playlists(session: tidalapi.Session, output_dir: Path) -> None:
    """
    Downloads a user's entire Tidal library to local CSV files.

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

    playlists = _fetch_all(user.playlists)

    @retry_on_429()
    def _export_single_playlist(playlist: Any) -> None:
        safe_name = "".join(c for c in playlist.name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        out_file = playlists_dir / f"{safe_name}.csv"

        # Offload the paginated fetching to the individual thread
        playlist_tracks = _fetch_all(playlist.tracks)

        with open(out_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC", "Tidal - id"])
            for t in playlist_tracks:
                writer.writerow([
                    t.name,
                    t.artist.name if getattr(t, 'artist', None) else "",
                    t.album.name if getattr(t, 'album', None) else "",
                    playlist.name, "Playlist", getattr(t, 'isrc', ""), t.id
                ])

    with Progress(console=console) as progress:
        task = progress.add_task("Exporting playlists...", total=len(playlists))
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_export_single_playlist, p) for p in playlists]
            for future in concurrent.futures.as_completed(futures):
                future.result()  # <--- Add this line to raise swallowed exceptions
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


def import_target(session: tidalapi.Session, target_path: Path, target_playlist_name: str | None = None) -> None:
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
            _route_and_import(session, target_path, target_playlist_name, stats)
        else:
            logger.error("Skipped non-CSV file", path=str(target_path))
    elif target_path.is_dir():
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {target_path}")
        csv_files = list(target_path.rglob("*.csv"))
        for file_path in csv_files:
            _route_and_import(session, file_path, None, stats)
    else:
        logger.error("Path not found", path=str(target_path))

    console.print(f"\n[bold yellow]Audit Report Generated:[/bold yellow]")
    console.print(f"  • {stats.skipped} items skipped (already owned/duplicates)")
    console.print(f"  • {stats.failed} items failed (could not be found on Tidal)")
    console.print(f"  • Detailed machine-readable log: [underline]{log_file}[/underline]")


def _route_and_import(session: tidalapi.Session, file_path: Path, fallback_name: str | None, stats: ImportStats) -> None:
    """
    Directs specific CSV files to their respective import handlers based on filenames.
    """
    filename = file_path.name
    if filename == "Liked Albums.csv":
        _import_albums(session, file_path, stats)
    elif filename == "Followed Artists.csv":
        _import_artists(session, file_path, stats)
    elif filename == "Liked Songs.csv":
        _import_tracks(session, file_path, stats, is_favorites=True)
    else:
        p_name = fallback_name or file_path.stem
        _import_tracks(session, file_path, stats, is_favorites=False, playlist_name=p_name)


def _handle_match_result(
    matched_id: str | None,
    item_type: str,
    item_name: str,
    artist_name: str,
    source_file: str,
    dest_name: str,
    existing_ids: set[str],
    stats: ImportStats,
    lock: threading.Lock,
    add_method: Callable[[str], Any] | None = None,
    ids_to_add: list[str] | None = None
) -> None:
    """
    Safely logs and updates statistics for a matched item using a thread lock.

    Prevents race conditions when multiple threads attempt to add tracks to
    the same playlist or update the global counter simultaneously.
    """
    with lock:
        if matched_id:
            if matched_id not in existing_ids:
                existing_ids.add(matched_id)
                if add_method: add_method(matched_id)
                if ids_to_add is not None: ids_to_add.append(matched_id)
                logger.bind(audit=True).debug("Item Staged", type=item_type, name=item_name, dest=dest_name)
            else:
                stats.add_skipped()
                logger.bind(audit=True).info("Skipped (Duplicate)", type=item_type, name=item_name, artist=artist_name,
                                             dest=dest_name)
        else:
            stats.add_failed()
            logger.bind(audit=True).warning("Failed (Not Found)", type=item_type, name=item_name, artist=artist_name,
                                            source=source_file)


def _run_matching_tasks(task_desc: str, items: list[Any], match_func: Callable[[Any], Any]) -> None:
    """
    Runs matching functions concurrently while displaying a progress bar.
    """
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), console=console) as progress:
        task = progress.add_task(task_desc, total=len(items))
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(match_func, item) for item in items]
            for future in concurrent.futures.as_completed(futures):
                future.result()  # <--- Add this line
                progress.advance(task)


def _import_tracks(session: tidalapi.Session, file_path: Path, stats: ImportStats, is_favorites: bool = False, playlist_name: str | None = None) -> None:
    """
    Parses a track CSV, matches entries against Tidal, and adds them to the library.

    Includes a bisection fallback to handle region-locked tracks that cause
    entire batch uploads to fail.
    """
    tracks = parse_csv(file_path, TrackRow)
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
            existing_track_ids = {str(t.id) for t in _fetch_all(user.favorites.tracks)}
        elif not is_favorites and playlist_name:
            existing_playlists = _fetch_all(user.playlists)
            playlist = next((p for p in existing_playlists if p.name == playlist_name), None)
            if playlist:
                existing_track_ids = {str(t.id) for t in _fetch_all(playlist.tracks)}
            else:
                playlist = user.create_playlist(playlist_name, "Imported via tidal-sync <3")

    track_ids_to_add: list[str] = []
    lock = threading.Lock()

    @retry_on_429()
    def _match_single_track(track: TrackRow) -> tuple[TrackRow, str | None]:
        matched_id = track.tidal_id
        if not matched_id and track.isrc:
            results = session.search(f"isrc:{str(track.isrc)}")
            res_tracks = getattr(results, 'tracks', [])
            if res_tracks: matched_id = str(res_tracks[0].id)

        if not matched_id:
            results = session.search(str(track.search_query))
            res_tracks = getattr(results, 'tracks', [])
            if res_tracks: matched_id = str(res_tracks[0].id)

        return track, matched_id

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(),
                  TaskProgressColumn(), console=console) as progress:
        task = progress.add_task(f"Matching {len(tracks)} tracks...", total=len(tracks))

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(_match_single_track, track): track for track in tracks}
            for future in concurrent.futures.as_completed(futures):
                track, matched_id = future.result()

                _handle_match_result(
                    matched_id, "Track", track.track_name, track.artist_name,
                    file_path.name, str(dest_name), existing_track_ids, stats,
                    lock, ids_to_add=track_ids_to_add
                )
                progress.advance(task)

    if track_ids_to_add:
        console.print(f"[cyan]Uploading {len(track_ids_to_add)} tracks to '{dest_name}'...[/cyan]")

        # Apply the rate-limit shield to the upload action specifically
        @retry_on_429()
        def _upload_chunk(batch: list[str]) -> None:
            if is_favorites and hasattr(user, 'favorites'):
                user.favorites.add_track(batch)
            elif playlist:
                playlist.add(batch)

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(),
                      TaskProgressColumn(), console=console) as progress:
            add_task = progress.add_task("Uploading...", total=len(track_ids_to_add))

            for i in range(0, len(track_ids_to_add), CHUNK_SIZE):
                chunk = track_ids_to_add[i:i + CHUNK_SIZE]
                try:
                    _upload_chunk(chunk)
                    stats.add_added(len(chunk))
                    for tid in chunk:
                        logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)
                except (HTTPError, ObjectNotFound) as e:
                    logger.bind(audit=True).error("Chunk rejected, initiating bisection", chunk_size=len(chunk),
                                                  error=str(e))

                    # Batch uploads fail entirely if even one track is region-locked.
                    # This recursive bisection isolates and drops the poison track.
                    def _bisect_upload(sub_chunk: list[str]) -> None:
                        if not sub_chunk: return
                        try:
                            _upload_chunk(sub_chunk)
                            stats.add_added(len(sub_chunk))
                            for tid in sub_chunk:
                                logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)
                        except (HTTPError, ObjectNotFound) as _:
                            if len(sub_chunk) == 1:
                                poison_id = sub_chunk[0]
                                logger.bind(audit=True).error("Dropped Track (Region Locked)", track_id=poison_id)
                                console.print(f"  [red]❌ Dropped Track ID {poison_id} (Region-locked)[/red]")
                                stats.add_failed()
                            else:
                                time.sleep(MEDIUM_DELAY*2)
                                mid = len(sub_chunk) // 2
                                _bisect_upload(sub_chunk[:mid])
                                _bisect_upload(sub_chunk[mid:])

                    _bisect_upload(chunk)

                progress.advance(add_task, advance=len(chunk))
                time.sleep(MEDIUM_DELAY*2)

        local_added = stats.added - initial_added
        local_skipped = stats.skipped - initial_skipped
        local_failed = stats.failed - initial_failed

        console.print(
            f"[green]✓ '{dest_name}' complete:[/green] "
            f"{local_added} uploaded | {local_skipped} skipped | {local_failed} failed "
            f"[dim](Session Total: {stats.added})[/dim]\n"
        )


def _import_albums(session: tidalapi.Session, file_path: Path, stats: ImportStats) -> None:
    """
    Parses an album CSV, matches entries against Tidal, and adds them to favourites.
    """
    albums = parse_csv(file_path, AlbumRow)
    if not albums: return
    user = cast(TidalUser, cast(object, session.user))

    console.print("[cyan]Importing Liked Albums...[/cyan]")
    with console.status("[cyan]Scanning existing albums...[/cyan]"):
        existing_album_ids = {str(a.id) for a in _fetch_all(user.favorites.albums)} if hasattr(user,
                                                                                               'favorites') else set()
    lock = threading.Lock()
    @retry_on_429()
    def _match_and_add_album(album: AlbumRow) -> None:
        matched_id = str(album.tidal_id) if album.tidal_id else None
        if not matched_id:
            results = session.search(f"{album.album_name} {album.artist_name}")
            res_albums = getattr(results, 'albums', [])
            if res_albums: matched_id = str(res_albums[0].id)

        add_func = user.favorites.add_album if hasattr(user, 'favorites') else None
        _handle_match_result(
            matched_id, "Album", album.album_name, album.artist_name,
            file_path.name, "Liked Albums", existing_album_ids, stats,
            lock, add_method=add_func
        )

    _run_matching_tasks(f"Matching & Adding {len(albums)} albums...", albums, _match_and_add_album)


def _import_artists(session: tidalapi.Session, file_path: Path, stats: ImportStats) -> None:
    """
    Parses an artist CSV, matches entries against Tidal, and follows them.
    """
    artists = parse_csv(file_path, ArtistRow)
    if not artists: return
    user = cast(TidalUser, cast(object, session.user))

    console.print("\n[cyan]Importing Followed Artists...[/cyan]")
    with console.status("[cyan]Scanning existing followed artists...[/cyan]"):
        existing_artist_ids = {str(a.id) for a in _fetch_all(user.favorites.artists)} if hasattr(user,
                                                                                                 'favorites') else set()

    lock = threading.Lock()

    @retry_on_429()
    def _match_and_add_artist(artist: ArtistRow) -> None:
        matched_id = str(artist.tidal_id) if artist.tidal_id else None
        if not matched_id:
            results = session.search(str(artist.artist_name))
            res_artists = getattr(results, 'artists', [])
            if res_artists: matched_id = str(res_artists[0].id)

        add_func = user.favorites.add_artist if hasattr(user, 'favorites') else None
        _handle_match_result(
            matched_id, "Artist", artist.artist_name, "N/A",
            file_path.name, "Followed Artists", existing_artist_ids, stats,
            lock, add_method=add_func
        )

    _run_matching_tasks(f"Matching & Adding {len(artists)} artists...", artists, _match_and_add_artist)


def clear_library(session: tidalapi.Session, target: ClearTarget) -> None:
    """
    Destructively removes items from the user's library.

    Args:
        session (tidalapi.Session): The active Tidal connection.
        target (ClearTarget): The category to clear.
    """
    user = cast(TidalUser, cast(object, session.user))
    if not hasattr(user, 'favorites'): return

    @retry_on_429()
    def _execute_delete(task_func: Callable[[], Any]) -> None:
        task_func()

    def _clear_category(items: list[Any], action: Callable[[Any], Any], category_name: str) -> None:
        if not items: return
        console.print(f"[cyan]Removing {len(items)} {category_name}...[/cyan]")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(action, item) for item in items]
            for future in concurrent.futures.as_completed(futures):  # Replaced .wait()
                future.result()

    if target in (ClearTarget.ALL, ClearTarget.PLAYLISTS):
        _clear_category(_fetch_all(user.playlists), lambda p: _execute_delete(p.delete), "playlists")

    if target in (ClearTarget.ALL, ClearTarget.TRACKS):
        _clear_category(_fetch_all(user.favorites.tracks),
                        lambda t: _execute_delete(lambda: user.favorites.remove_track(str(t.id))), "liked songs")

    if target in (ClearTarget.ALL, ClearTarget.ALBUMS):
        _clear_category(_fetch_all(user.favorites.albums),
                        lambda a: _execute_delete(lambda: user.favorites.remove_album(str(a.id))), "liked albums")

    if target in (ClearTarget.ALL, ClearTarget.ARTISTS):
        _clear_category(_fetch_all(user.favorites.artists),
                        lambda art: _execute_delete(lambda: user.favorites.remove_artist(str(art.id))), "artists")

    console.print(f"[bold green]Successfully cleared '{target.value}' from library.[/bold green]")