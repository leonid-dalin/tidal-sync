"""
Tidal V2 API folder management and HTTP bypasses.

This module provides raw HTTP interventions to manage playlist folders.
It intentionally bypasses the standard `tidalapi` wrapper to prevent
JSON decoding errors triggered by Tidal's undocumented V2 API, which
frequently returns empty HTTP response bodies upon success.
"""

import asyncio
from typing import Any

import requests
import tidalapi
from loguru import logger
from rich.console import Console

from .parser import normalises_playlist_id

console = Console()

# Module-level cache to minimise redundant API calls during batch processing
_v2_folder_cache: dict[str, str] = {}


async def ensure_v2_folder_exists(session: tidalapi.Session, folder_name: str) -> str | None:
    """
    Idempotently resolves a folder UUID by its name, creating it if it does not already exist.

    This function relies on raw `requests` calls offloaded to the asyncio thread pool.
    By doing so, we prevent the core `tidalapi` library from crashing when Tidal
    returns a successful 200 OK with no body data, or when it misinterprets base URL overrides.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
        folder_name (str): The exact string name of the target folder to find or create.

    Returns:
        str | None: The UUID of the folder, or None if the creation request failed.
    """
    if folder_name in _v2_folder_cache:
        return _v2_folder_cache[folder_name]

    base_params: dict[str, Any] = {"deviceType": "BROWSER", "locale": "en_US"}
    if hasattr(session, "country_code") and session.country_code:
        base_params["countryCode"] = session.country_code

    base_v2_url = "https://api.tidal.com/v2"

    headers = {"Authorization": f"Bearer {session.access_token}", "Accept": "application/json"}

    # 1. Check if the folder already exists (Raw Requests Bypass)
    offset = 0
    while True:
        params = base_params.copy()
        params.update({"includeOnly": "FOLDER", "offset": offset, "limit": 50})
        try:

            def _fetch_folders(params=params):
                return requests.get(
                    f"{base_v2_url}/my-collection/playlists/folders/flattened",
                    params=params,
                    headers=headers,
                )

            res = await asyncio.to_thread(_fetch_folders)

            if not res.ok:
                logger.debug(f"Failed to fetch existing folders. HTTP {res.status_code}")
                break

            data_payload = res.json()
            items = data_payload.get("items", [])

            if not items:
                break

            for item in items:
                data = item.get("data", {})
                name = data.get("name") or data.get("title") or item.get("name")
                raw_id = data.get("uuid") or data.get("id") or item.get("uuid")

                if name == folder_name and raw_id:
                    f_id = str(raw_id)
                    _v2_folder_cache[folder_name] = f_id
                    return f_id

            offset += len(items)
            if len(items) < 50:
                break

        except Exception as e:
            logger.debug(f"Exception during existing folder fetch: {e}")
            break

    # 2. Create a new folder mimicking the Web Player's behaviour
    create_params = base_params.copy()
    create_params.update({"folderId": "root", "name": folder_name, "trns": ""})

    try:

        def _execute_create():
            return requests.put(
                f"{base_v2_url}/my-collection/playlists/folders/create-folder",
                params=create_params,
                headers=headers,
            )

        res = await asyncio.to_thread(_execute_create)

        if res.ok:
            data_payload = res.json()
            raw_id = data_payload.get("data", {}).get("id") or data_payload.get("id")

            if raw_id:
                f_id = str(raw_id)
                _v2_folder_cache[folder_name] = f_id
                console.print(f"[green]✓ Created Folder:[/green] {folder_name}")
                return f_id
        else:
            logger.warning(f"Folder creation failed. HTTP {res.status_code}: {res.text}")

    except Exception as e:
        logger.warning(f"Failed to create folder '{folder_name}': {e}")

    return None


async def assign_playlist_to_v2_folder(
    session: tidalapi.Session, playlist_id: str, folder_id: str
) -> None:
    """
    Moves a specified playlist into a designated V2 folder directory.

    This function strictly emulates the Web Player's request headers. It explicitly
    declares a Content-Length of 0 with an empty byte payload to bypass Tidal's
    internal firewalls, which would otherwise reject the raw PUT request.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
        playlist_id (str): The standard UUID or URN-prefixed ID of the playlist.
        folder_id (str): The destination folder's UUID.
    """
    normalized_uuid = normalises_playlist_id(playlist_id)
    trn_playlist = f"trn:playlist:{normalized_uuid}"

    params = {
        "folderId": folder_id,
        "trns": trn_playlist,
        "deviceType": "BROWSER",
        "locale": "en_US",
    }

    if hasattr(session, "country_code") and session.country_code:
        params["countryCode"] = session.country_code

    headers = {
        "Authorization": f"Bearer {session.access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:

        def _execute_move():
            return requests.put(
                "https://api.tidal.com/v2/my-collection/playlists/folders/move",
                params=params,
                headers=headers,
                data=b"",
            )

        res = await asyncio.to_thread(_execute_move)

        if not res.ok:
            logger.warning(f"Folder assignment failed. HTTP {res.status_code}: {res.text}")
        else:
            logger.debug(f"Successfully moved playlist {playlist_id} into folder {folder_id}")

    except Exception as e:
        logger.warning(f"Failed to move playlist to folder: {e}")
