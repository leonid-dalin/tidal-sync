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
from .network import execute_network, fetch_all_async, fetch_blocked_artists
from .parser import (
    normalises_playlist_id,
    sanitize_filename,
    write_albums_csv_sync,
    write_artists_csv_sync,
    write_tracks_csv_sync,
)

console = Console()


async def fetch_and_serialise_tracks(
    name: str, target_dir: Path, fetch_items_coro: Any, log_type: str
) -> None:
    """
    Retrieves track metadata from the network and offloads file writing to the disk.

    To maintain high performance, this function resolves the network payload
    and immediately passes the blocking I/O write operation to a background
    thread, preventing the async event loop from freezing during large backups.

    Args:
        name (str): The raw name of the playlist or collection.
        target_dir (Path): The destination directory on the local filesystem.
        fetch_items_coro (Any): The API endpoint or coroutine to retrieve the tracks.
        log_type (str): The category used for telemetry (e.g., 'Playlist', 'Mix').
    """
    try:
        tracks = await fetch_all_async(fetch_items_coro)
        if not tracks:
            return

        safe_name = sanitize_filename(name)
        file_path = target_dir / f"{safe_name}.csv"

        await asyncio.to_thread(write_tracks_csv_sync, file_path, tracks)
        logger.bind(audit=True).debug("Snapshot Saved", type=log_type, name=name)

    except Exception as e:
        logger.error(f"Failed to export {log_type} '{name}'", error=str(e))


async def build_playlist_folder_map(session: tidalapi.Session) -> dict[str, str]:
    """
    Reconstructs the user's visual folder tree from Tidal's flat backend structure.

    Tidal's V2 API separates folders from playlists. This function queries
    the flattened folder list and maps each contained playlist UUID to its
    sanitised parent folder name, allowing the exporter to recreate the exact
    directory structure on the user's hard drive.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.

    Returns:
        dict[str, str]: A dictionary mapping normalised playlist UUIDs to folder names.
    """
    playlist_folder_map = {}
    base_params = {
        "deviceType": "BROWSER",
        "order": "DATE",
        "orderDirection": "DESC",
        "locale": "en_US",
        "limit": 50,
    }

    try:
        folders = []
        offset = 0
        while True:
            folder_params = base_params.copy()
            folder_params.update({"includeOnly": "FOLDER", "offset": offset})

            folder_res = await execute_network(
                session.request.request,
                "GET",
                "my-collection/playlists/folders/flattened",
                params=folder_params,
                base_url=session.config.api_v2_location,
            )

            if not folder_res.ok:
                break

            items = folder_res.json().get("items", [])
            if not items:
                break

            folders.extend(items)
            offset += len(items)

            if len(items) < 50:
                break

        for folder_item in folders:
            data = folder_item.get("data", {})
            folder_id = data.get("id") or data.get("uuid") or folder_item.get("id")
            folder_name = data.get("name") or data.get("title") or folder_item.get("name")

            if not folder_id or not folder_name:
                continue

            safe_folder_name = sanitize_filename(folder_name)
            c_offset = 0

            while True:
                content_params = base_params.copy()
                content_params.update({"folderId": folder_id, "offset": c_offset})

                content_res = await execute_network(
                    session.request.request,
                    "GET",
                    "my-collection/playlists/folders",
                    params=content_params,
                    base_url=session.config.api_v2_location,
                )

                if not content_res.ok:
                    break

                items = content_res.json().get("items", [])
                if not items:
                    break

                for item in items:
                    item_data = item.get("data", {})
                    pl_uuid = item_data.get("uuid") or item_data.get("id")

                    if not pl_uuid and "playlist" in item and isinstance(item["playlist"], dict):
                        pl_uuid = item["playlist"].get("uuid") or item["playlist"].get("id")
                    if not pl_uuid:
                        pl_uuid = item.get("id") or item.get("uuid")

                    if pl_uuid:
                        normalized_uuid = normalises_playlist_id(pl_uuid)
                        playlist_folder_map[normalized_uuid] = safe_folder_name

                c_offset += len(items)
                if len(items) < 50:
                    break

    except Exception as e:
        logger.warning(f"Could not construct folder map from V2 API: {e}")

    return playlist_folder_map


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

        async with asyncio.TaskGroup() as tg:
            for pl in playlists:
                normalized_pl_id = normalises_playlist_id(pl.id)
                folder_name = folder_map.get(normalized_pl_id)

                if folder_name:
                    target_dir = base_dir / "Playlists" / folder_name
                else:
                    target_dir = base_dir / "Playlists"

                tg.create_task(
                    fetch_and_serialise_tracks(pl.name, target_dir, pl.tracks, "Playlist")
                )
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

        async with asyncio.TaskGroup() as tg:
            for station in all_stations:
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

                if fetch_target is not None:
                    if isinstance(fetch_target, list):

                        def _wrap_list(items: list[Any]) -> Callable[..., list[Any]]:
                            return lambda **kwargs: items

                        safe_fetch = _wrap_list(fetch_target)
                    else:
                        safe_fetch = fetch_target

                    tg.create_task(
                        fetch_and_serialise_tracks(
                            str(station_name), target_dir, safe_fetch, "Mix/Radio"
                        )
                    )
                else:
                    logger.warning(
                        f"Station '{station_name}' has no track parsing function available."
                    )

    except Exception as e:
        logger.error("Failed to fetch algorithmic stations", error=str(e))
