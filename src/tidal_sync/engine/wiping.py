"""
Tidal library clearance and data purging engine.

This module facilitates the destructive removal of user data from a Tidal account.
It utilises concurrent, headless worker groups to rapidly process deletion requests
across various categories without blocking the primary application loop. It also
includes raw HTTP bypasses to ensure undocumented V2 entities, such as folders,
are properly eradicated.
"""

import asyncio
import requests
from typing import Any, Callable, cast
from loguru import logger
import tidalapi
from rich.console import Console

from ..domain.enums import ClearTarget
from ..domain.protocols import TidalUser
from .network import execute_network, fetch_all_async, fetch_blocked_artists
from .workers import run_headless_tasks_async

console = Console()


async def _purge_v2_folders_async(session: tidalapi.Session) -> None:
    """
    Identifies and deletes all V2 playlist folders associated with the user.

    Because Tidal's V2 API manages folders separately from standard playlists,
    this function directly queries the flattened folder hierarchy and issues
    raw HTTP PUT requests to the undocumented `/remove` endpoint, bypassing
    the standard library wrapper.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
    """
    base_params: dict[str, Any] = {"deviceType": "BROWSER", "locale": "en_US"}
    if hasattr(session, "country_code") and session.country_code:
        base_params["countryCode"] = session.country_code

    base_v2_url = "https://api.tidal.com/v2"
    folders_to_delete = []

    headers = {
        "Authorization": f"Bearer {session.access_token}",
        "Accept": "application/json"
    }

    # 1. Fetch all existing folders (Bypassing tidalapi entirely)
    offset = 0
    while True:
        params = base_params.copy()
        params.update({"includeOnly": "FOLDER", "offset": offset, "limit": 50})

        try:
            def _fetch_folders():
                return requests.get(
                    f"{base_v2_url}/my-collection/playlists/folders/flattened",
                    params=params,
                    headers=headers
                )

            res = await asyncio.to_thread(_fetch_folders)

            if not res.ok:
                logger.debug(f"Failed to fetch folders. HTTP {res.status_code}: {res.text}")
                break

            data_payload = res.json()
            items = data_payload.get("items", [])

            if not items:
                break

            for item in items:
                data = item.get("data", {})
                raw_id = data.get("uuid") or data.get("id") or item.get("uuid")
                if raw_id:
                    folders_to_delete.append(str(raw_id))

            offset += len(items)
            if len(items) < 50:
                break

        except Exception as e:
            logger.debug(f"Exception during folder fetch: {e}")
            break

    if not folders_to_delete:
        return

    console.print(f"[cyan]Removing {len(folders_to_delete)} folders...[/cyan]")

    # 2. Execute deletion via headless workers mimicking the Web Player
    async def _delete_folder(folder_id: str) -> None:
        delete_params = base_params.copy()
        delete_params["trns"] = f"trn:folder:{folder_id}"

        del_headers = headers.copy()
        del_headers["Content-Type"] = "application/json"

        def _execute_remove():
            return requests.put(
                f"{base_v2_url}/my-collection/playlists/folders/remove",
                params=delete_params,
                headers=del_headers,
                data=b''  # Forces Content-Length: 0
            )

        try:
            res = await asyncio.to_thread(_execute_remove)
            if not res.ok:
                logger.debug(f"Folder deletion failed for {folder_id}. HTTP {res.status_code}")
        except Exception as e:
            logger.debug(f"Silently bypassed folder deletion error: {e}")

    async def _async_wrapper(f_id: str):
        await _delete_folder(f_id)

    await run_headless_tasks_async(folders_to_delete, _async_wrapper)


async def purge_target_category_async(session: tidalapi.Session, target: ClearTarget) -> None:
    """
    Destructively removes items from a user's Tidal account based on the selected target.

    Executes concurrent deletion requests across the designated category (e.g., tracks,
    albums, playlists). Errors such as HTTP 404s or 500s are intentionally absorbed
    to prevent isolated server faults from halting the entire batch deletion process.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
        target (ClearTarget): The specific library segment to wipe.
    """
    user = cast(TidalUser, cast(object, session.user))
    if not hasattr(user, 'favorites'):
        return

    async def _clear_category_async(
            items: list[Any],
            sync_action_factory: Callable[[Any], Callable[[], Any]],
            category_name: str
    ) -> None:
        if not items:
            return

        console.print(f"[cyan]Removing {len(items)} {category_name}...[/cyan]")

        async def _async_wrapper(item: Any):
            try:
                await execute_network(sync_action_factory(item))
            except Exception as e:
                logger.debug(f"Silently bypassed deletion error for item: {e}")

        await run_headless_tasks_async(items, _async_wrapper)

    # 1. Clear Playlists and Folders
    if target in (ClearTarget.ALL, ClearTarget.PLAYLISTS):
        playlists = await fetch_all_async(user.playlists)
        await _clear_category_async(playlists, lambda p: p.delete, "playlists")

        # Purge empty folders after the playlists have been removed
        await _purge_v2_folders_async(session)

    # 2. Clear Tracks
    if target in (ClearTarget.ALL, ClearTarget.TRACKS):
        tracks = await fetch_all_async(user.favorites.tracks)
        await _clear_category_async(tracks, lambda t: lambda: user.favorites.remove_track(str(t.id)), "liked songs")

    # 3. Clear Albums
    if target in (ClearTarget.ALL, ClearTarget.ALBUMS):
        albums = await fetch_all_async(user.favorites.albums)
        await _clear_category_async(albums, lambda a: lambda: user.favorites.remove_album(str(a.id)), "liked albums")

    # 4. Clear Artists & Blocklist
    if target in (ClearTarget.ALL, ClearTarget.ARTISTS):
        artists = await fetch_all_async(user.favorites.artists)
        await _clear_category_async(artists, lambda art: lambda: user.favorites.remove_artist(str(art.id)), "artists")

        try:
            blocked = await execute_network(fetch_blocked_artists, session)
            if blocked:
                def _unblock_factory(art):
                    return lambda: session.request.request(
                        "DELETE",
                        f"users/{user.id}/blocks/artists/{art.id}"
                    )

                await _clear_category_async(blocked, _unblock_factory, "blocked artists")
        except Exception as e:
            logger.warning(f"Failed to clear blocked artists: {e}")

    console.print(f"[bold green]Successfully cleared '{target.value}' from library.[/bold green]")