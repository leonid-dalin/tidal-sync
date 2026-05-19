"""
Data ingestion and metadata translation engine.

This module coordinates the bulk import of local CSV backups into a user's
Tidal library. It evaluates file paths to categorise the incoming data,
translates local text metadata into exact Tidal UUIDs using ISRC and text
fallbacks, and orchestrates the upload queues.
"""

import asyncio
from pathlib import Path
from typing import cast
import tidalapi
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from ..domain.models import TrackRow
from ..domain.protocols import TidalUser, CHUNK_SIZE
from ..domain.logger import setup_audit_logging

from .network import execute_network, fetch_all_async
from .parser import parse_csv
from .workers import ImportStats, handle_match_result_async, run_matching_tasks_async
from .folders import ensure_v2_folder_exists, assign_playlist_to_v2_folder
from .bisection import upload_batch_with_bisection_recovery

console = Console()


async def import_collection_from_disk(
        session: tidalapi.Session,
        target_path: Path,
        target_playlist_name: str | None = None
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
        if target_path.suffix.lower() == '.csv':
            await resolve_and_import_playlist(session, target_path, target_playlist_name, stats)
        else:
            logger.error("Skipped non-CSV file", path=str(target_path))

    elif target_path.is_dir():
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {target_path}\n")
        for file_path in target_path.rglob("*.csv"):
            await resolve_and_import_playlist(session, file_path, None, stats)
    else:
        logger.error("Path not found", path=str(target_path))

    console.print(f"\n[bold yellow]Audit Report Generated:[/bold yellow]")
    console.print(f"  • {stats.added} items successfully imported")
    console.print(f"  • {stats.skipped} items skipped (already owned/duplicates)")
    console.print(f"  • {stats.failed} items failed (could not be found on Tidal)")
    console.print(f"  • Detailed machine-readable log: [underline]{log_file}[/underline]")


async def resolve_and_import_playlist(
        session: tidalapi.Session,
        file_path: Path,
        fallback_name: str | None,
        stats: ImportStats
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
    else:
        p_name = fallback_name or file_path.stem
        await import_tracks_category_async(
            session, file_path, stats,
            is_favorites=False, playlist_name=p_name, folder_name=folder_name
        )


async def import_tracks_category_async(
        session: tidalapi.Session,
        file_path: Path,
        stats: ImportStats,
        is_favorites: bool = False,
        playlist_name: str | None = None,
        folder_name: str | None = None
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
        if is_favorites and hasattr(user, 'favorites'):
            existing_track_ids = {str(t.id) for t in await fetch_all_async(user.favorites.tracks)}
        elif not is_favorites and playlist_name:
            existing_playlists = await fetch_all_async(user.playlists)
            playlist = next((p for p in existing_playlists if p.name == playlist_name), None)

            if playlist:
                existing_track_ids = {str(t.id) for t in await fetch_all_async(playlist.tracks)}
            else:
                playlist = await execute_network(user.create_playlist, playlist_name, "Imported via tidal-sync <3")

            if folder_name and playlist:
                folder_id = await ensure_v2_folder_exists(session, folder_name)
                if folder_id:
                    await assign_playlist_to_v2_folder(session, playlist.id, folder_id)

    track_ids_to_add: list[str] = []
    staged_tracks_map: dict[str, TrackRow] = {}

    # 2. Metadata translation
    async def _resolve_track_metadata_to_id(track: TrackRow) -> None:
        matched_id = str(track.tidal_id) if track.tidal_id else None

        if not matched_id and track.isrc:
            results = await execute_network(session.search, f"isrc:{str(track.isrc)}")
            res_tracks = getattr(results, 'tracks', [])
            if res_tracks: matched_id = str(res_tracks[0].id)

        if not matched_id:
            results = await execute_network(session.search, str(track.search_query))
            res_tracks = getattr(results, 'tracks', [])
            if res_tracks: matched_id = str(res_tracks[0].id)

        failure_reason = "ISRC mismatch & Text fallback failed" if track.isrc else "Text search failed"
        if matched_id:
            staged_tracks_map[matched_id] = track

        await handle_match_result_async(
            matched_id, "Track", track.track_name, track.artist_name,
            file_path.name, str(dest_name), existing_track_ids, stats,
            ids_to_add=track_ids_to_add, failure_reason=failure_reason
        )

    await run_matching_tasks_async(f"Matching {len(tracks)} tracks...", tracks, _resolve_track_metadata_to_id)

    # 3. Fault-tolerant batch uploading
    if track_ids_to_add:
        console.print(f"[cyan]Uploading {len(track_ids_to_add)} tracks to '{dest_name}'...[/cyan]")

        async def _upload_chunk_async(batch: list[str]) -> None:
            nonlocal playlist
            if is_favorites and hasattr(user, 'favorites'):
                await execute_network(user.favorites.add_track, batch)
            elif playlist:
                playlist = await execute_network(session.playlist, playlist.id)  # ETag refresh
                await execute_network(playlist.add, batch)

        with Progress(
                SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                BarColumn(), TaskProgressColumn(), console=console
        ) as progress_ui:
            add_task = progress_ui.add_task("Uploading...", total=len(track_ids_to_add))

            for i in range(0, len(track_ids_to_add), CHUNK_SIZE):
                chunk = track_ids_to_add[i:i + CHUNK_SIZE]

                await upload_batch_with_bisection_recovery(
                    chunk, _upload_chunk_async, stats, staged_tracks_map,
                    str(dest_name), progress_ui, add_task
                )

    console.print(
        f"[green]✓ '{dest_name}' complete:[/green] "
        f"{stats.added - initial_added} uploaded | "
        f"{stats.skipped - initial_skipped} skipped | "
        f"{stats.failed - initial_failed} failed "
        f"[dim](Session Total: {stats.added})[/dim]\n"
    )



