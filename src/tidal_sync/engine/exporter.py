"""
Data extraction and local backup engine.

This module orchestrates the extraction of a user's Tidal library and
serialises it into local CSV files. It manages point-in-time snapshots
of dynamic content (like algorithmic radios) and faithfully reconstructs
the user's cloud-based folder hierarchy on their local filesystem.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import tidalapi
from loguru import logger
from rich.console import Console

from ..domain.protocols import TidalUser
from .folders import build_playlist_folder_map
from .network import execute_network, fetch_all_async, fetch_blocked_artists
from .parser import (
    UniquePathAllocator,
    normalises_playlist_id,
    write_albums_csv_sync,
    write_artists_csv_sync,
    write_tracks_csv_sync,
)
from .workers import run_headless_tasks_async

console = Console()


async def fetch_and_serialise_tracks(
    name: str,
    target_dir: Path,
    fetch_items_coro: Any,
    log_type: str,
    allocator: "UniquePathAllocator",
) -> int:
    """
    Retrieves track metadata from the network and offloads file writing to the disk.

    To maintain high performance, this function resolves the network payload
    and immediately passes the blocking I/O write operation to a background
    thread, preventing the async event loop from freezing during large backups.

        allocator (UniquePathAllocator): Hands out non-colliding paths for
            this export run. Two collections sharing a name must not open
            the same file.
    Returns:
        int: The number of rows written.
    """
    tracks = await fetch_all_async(fetch_items_coro)
    if not tracks:
        return 0

    file_path = allocator.allocate(target_dir, name)
    rows = await asyncio.to_thread(write_tracks_csv_sync, file_path, tracks)
    logger.bind(audit=True).debug("Snapshot Saved", type=log_type, name=name, rows=rows)
    return rows


async def export_user_favourites_to_disk(session: tidalapi.Session, base_dir: Path) -> None:
    """
    Backs up the user's core static library components.

    Extracts Liked Songs, Liked Albums, Followed Artists, and the user's
    internal artist blocklist, saving them to the root of the backup directory.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
        base_dir (Path): The root output directory for the backup.
    """
    user = cast(TidalUser, cast(object, session.user))
    if not hasattr(user, "favorites"):
        return

    async def _export_songs():
        try:
            songs = await fetch_all_async(user.favorites.tracks)
            if songs:
                console.print(f"[cyan]Exporting {len(songs)} Liked Songs...[/cyan]")
                file_path = base_dir / "Liked Songs.csv"
                await asyncio.to_thread(write_tracks_csv_sync, file_path, songs)
        except Exception as e:
            logger.error("Failed to export Liked Songs", error=str(e))

    async def _export_albums():
        try:
            albums = await fetch_all_async(user.favorites.albums)
            if albums:
                console.print(f"[cyan]Exporting {len(albums)} Liked Albums...[/cyan]")
                file_path = base_dir / "Liked Albums.csv"
                await asyncio.to_thread(write_albums_csv_sync, file_path, albums)
        except Exception as e:
            logger.error("Failed to export Liked Albums", error=str(e))

    async def _export_artists():
        try:
            artists = await fetch_all_async(user.favorites.artists)
            if artists:
                console.print(f"[cyan]Exporting {len(artists)} Followed Artists...[/cyan]")
                file_path = base_dir / "Followed Artists.csv"
                await asyncio.to_thread(write_artists_csv_sync, file_path, artists)
        except Exception as e:
            logger.error("Failed to export Followed Artists", error=str(e))

    async def _export_blocked():
        try:
            blocked = await execute_network(fetch_blocked_artists, session)
            if blocked:
                console.print(f"[cyan]Exporting {len(blocked)} Blocked Artists...[/cyan]")
                file_path = base_dir / "Blocked Artists.csv"
                await asyncio.to_thread(write_artists_csv_sync, file_path, blocked)
        except Exception as e:
            logger.error("Failed to export Blocked Artists", error=str(e))

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_export_songs())
        tg.create_task(_export_albums())
        tg.create_task(_export_artists())
        tg.create_task(_export_blocked())


async def export_user_playlists_to_disk(session: tidalapi.Session, base_dir: Path) -> None:
    """
    Backs up user-created playlists and maps them into physical directories.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
        base_dir (Path): The root output directory for the backup.
    """
    try:
        playlists = await fetch_all_async(session.user.playlists)

        # Deduplicate V1 playlists (Tidal API yields folder contents twice)
        unique_playlists = {}
        for pl in playlists:
            if pl.id not in unique_playlists:
                unique_playlists[pl.id] = pl
        playlists = list(unique_playlists.values())

        folder_map = await build_playlist_folder_map(session)
        console.print(f"[cyan]Exporting {len(playlists)} Playlists...[/cyan]")

        allocator = UniquePathAllocator()
        exported = 0
        failures: list[str] = []

        async def _export_one(pl: Any) -> None:
            nonlocal exported
            normalized_pl_id = normalises_playlist_id(pl.id)
            folder_name = folder_map.get(normalized_pl_id)

            if folder_name:
                target_dir = base_dir / "Playlists" / folder_name
            else:
                target_dir = base_dir / "Playlists"

            try:
                exported += await fetch_and_serialise_tracks(
                    pl.name, target_dir, pl.tracks, "Playlist", allocator
                )
            except Exception as e:
                # TaskGroup cancels every sibling on the first unhandled
                # exception, so one bad playlist must not escape here.
                failures.append(f"{pl.name}: {e}")
                logger.error("Export failed for {name}", name=pl.name, error=repr(e))

        await run_headless_tasks_async(playlists, _export_one)

        if failures:
            console.print(f"\n[bold red]{len(failures)} playlist(s) failed:[/bold red]")
            for detail in failures[:20]:
                console.print(f"  [dim]{detail}[/dim]")
    except Exception as e:
        logger.error("Failed to export playlists", error=str(e))


async def export_algorithmic_mixes_to_disk(session: tidalapi.Session, base_dir: Path) -> None:
    """
    Captures a point-in-time snapshot of Tidal's dynamic radio stations.

    Algorithmic stations (like 'My Daily Discovery') shift constantly. This
    function polyfills missing endpoints in the `tidalapi` wrapper to capture
    the current state of these MixV2 objects, serialising them as standard
    static playlists for future restoration.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
        base_dir (Path): The root output directory for the backup.
    """
    target_dir = base_dir / "Mixes & Radios"
    user = cast(TidalUser, cast(object, session.user))

    try:
        all_stations = []

        if hasattr(user, "favorites"):
            if hasattr(user.favorites, "mixes"):
                mixes_func = user.favorites.mixes
                mixes = await execute_network(mixes_func) if callable(mixes_func) else mixes_func
                if isinstance(mixes, list):
                    all_stations.extend(mixes)

            if hasattr(user.favorites, "radios"):
                radios_func = user.favorites.radios
                radios = (
                    await execute_network(radios_func) if callable(radios_func) else radios_func
                )
                if isinstance(radios, list):
                    all_stations.extend(radios)

        if not all_stations and hasattr(session, "mixes"):
            mixes_func = session.mixes
            mixes = await execute_network(mixes_func) if callable(mixes_func) else mixes_func
            if isinstance(mixes, list):
                all_stations.extend(mixes)

        if not all_stations:
            return

        console.print(f"[cyan]Snapshotting {len(all_stations)} Mixes & Radios...[/cyan]")

        allocator = UniquePathAllocator()
        failures: list[str] = []

        async def _export_station(station: Any) -> None:
            station_name = getattr(
                station,
                "title",
                getattr(station, "name", getattr(station, "id", "Unknown Station")),
            )
            fetch_target = None

            for attr in ("get_items", "get_tracks", "items", "tracks"):
                if hasattr(station, attr):
                    fetch_target = getattr(station, attr)
                    break

            if fetch_target is None and type(station).__name__ == "MixV2":

                def _get_v2_items(st=station):
                    if not getattr(st, "_retrieved", False):
                        st.get()
                    return getattr(st, "_items", []) or []

                fetch_target = _get_v2_items

            if fetch_target is None:
                logger.warning(f"Station '{station_name}' has no track parsing function available.")
                return

            if isinstance(fetch_target, list):

                def _wrap_list(items: list[Any]) -> Callable[..., list[Any]]:
                    return lambda **kwargs: items

                safe_fetch = _wrap_list(fetch_target)
            else:
                safe_fetch = fetch_target

            try:
                await fetch_and_serialise_tracks(
                    str(station_name), target_dir, safe_fetch, "Mix/Radio", allocator
                )
            except Exception as e:
                # TaskGroup cancels every sibling on the first unhandled
                # exception, so one bad station must not escape here.
                failures.append(f"{station_name}: {e}")
                logger.error("Export failed for {name}", name=station_name, error=repr(e))

        await run_headless_tasks_async(all_stations, _export_station)

        if failures:
            console.print(f"\n[bold red]{len(failures)} mix(es)/radio(s) failed:[/bold red]")
            for detail in failures[:20]:
                console.print(f"  [dim]{detail}[/dim]")

    except Exception as e:
        logger.error("Failed to fetch algorithmic stations", error=str(e))
