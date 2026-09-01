"""
V2 playlist folder access.

Tidal's V2 API manages folders separately from playlists, and tidalapi does
not expose them, so this module owns the raw endpoints. Every call goes
through execute_network so folder traffic obeys the global rate-limit gate.

The folder endpoints are undocumented and were reverse-engineered from the
web player. The requests below are reproduced exactly from the calls that
worked: verb, path, parameters, and the empty body Tidal's firewall
requires. Changing any of them breaks folder management against a real
account, which no unit test can detect.
"""

from typing import Any, Literal

import tidalapi
from loguru import logger

from .network import execute_network
from .parser import normalises_playlist_id, sanitize_filename

_V2_PATH = "my-collection/playlists/folders"
_PAGE_SIZE = 50
_V2_METHOD = Literal["GET", "PUT", "POST", "DELETE"]


def _base_params(session: tidalapi.Session) -> dict[str, Any]:
    params: dict[str, Any] = {"deviceType": "BROWSER", "locale": "en_US"}
    country_code = getattr(session, "country_code", None)
    if country_code:
        params["countryCode"] = country_code
    return params


def _v2(
    session: tidalapi.Session, method: _V2_METHOD, path: str, data: Any = None, **params: Any
) -> Any:
    """Issues one V2 request. The only place a V2 call is constructed."""
    request_params = {**_base_params(session), **params}
    return session.request.request(
        method,
        path,
        params=request_params,
        data=data,
        base_url=session.config.api_v2_location,
    )


async def fetch_v2_folders(session: tidalapi.Session) -> list[tuple[str, str]]:
    """Lists every V2 folder as (id, name) pairs.

    Stops on an empty page, a short page, or a page identical to the previous
    one. Without that last guard an endpoint that ignores `offset` would loop
    forever issuing calls into the gate.
    """
    folders: list[tuple[str, str]] = []
    offset = 0
    last_ids: list[str] = []

    while True:
        res = await execute_network(
            _v2,
            session,
            "GET",
            f"{_V2_PATH}/flattened",
            includeOnly="FOLDER",
            offset=offset,
            limit=_PAGE_SIZE,
        )
        items = res.json().get("items", [])
        if not items:
            break

        current_ids = [
            str(item.get("data", {}).get("uuid") or item.get("uuid") or "") for item in items
        ]
        if offset > 0 and current_ids == last_ids:
            break

        for item in items:
            data = item.get("data", {})
            folder_id = data.get("id") or data.get("uuid") or item.get("id")
            folder_name = data.get("name") or data.get("title") or item.get("name")
            if folder_id and folder_name:
                folders.append((str(folder_id), str(folder_name)))

        last_ids = current_ids
        offset += len(items)
        if len(items) < _PAGE_SIZE:
            break

    return folders


async def fetch_folder_playlists(session: tidalapi.Session, folder_id: str) -> list[str]:
    """Lists the playlist UUIDs directly inside a folder."""
    uuids: list[str] = []
    offset = 0
    last_ids: list[str] = []

    while True:
        res = await execute_network(
            _v2,
            session,
            "GET",
            _V2_PATH,
            folderId=folder_id,
            offset=offset,
            limit=_PAGE_SIZE,
        )
        items = res.json().get("items", [])
        if not items:
            break

        current_ids = [str(item.get("id") or item.get("uuid") or "") for item in items]
        if offset > 0 and current_ids == last_ids:
            break

        for item in items:
            data = item.get("data", {})
            pl_uuid = data.get("uuid") or data.get("id")
            if not pl_uuid and isinstance(item.get("playlist"), dict):
                pl_uuid = item["playlist"].get("uuid") or item["playlist"].get("id")
            if not pl_uuid:
                pl_uuid = item.get("id") or item.get("uuid")
            if pl_uuid:
                uuids.append(str(pl_uuid))

        last_ids = current_ids
        offset += len(items)
        if len(items) < _PAGE_SIZE:
            break

    return uuids


async def build_playlist_folder_map(session: tidalapi.Session) -> dict[str, str]:
    """Maps normalised playlist UUID -> sanitised parent folder name."""
    folder_map: dict[str, str] = {}
    try:
        for folder_id, folder_name in await fetch_v2_folders(session):
            safe_name = sanitize_filename(folder_name)
            for pl_uuid in await fetch_folder_playlists(session, folder_id):
                folder_map[normalises_playlist_id(pl_uuid)] = safe_name
    except Exception as e:
        logger.warning("Could not construct folder map from V2 API: {error}", error=repr(e))

    return folder_map


async def create_folder(session: tidalapi.Session, name: str) -> str | None:
    """Creates a V2 folder and returns its id, or None on failure.

    Reproduces the web player's call: PUT to create-folder with folderId=root
    and an empty trns.
    """
    try:
        res = await execute_network(
            _v2,
            session,
            "PUT",
            f"{_V2_PATH}/create-folder",
            folderId="root",
            name=name,
            trns="",
        )
        # tidalapi's request() raises on any non-2xx, so a returned response is
        # always ok. There is no success flag to check.
        payload = res.json()
        if not payload:
            return None
        data = payload.get("data", payload)
        return str(data.get("uuid") or data.get("id") or "") or None
    except Exception as e:
        logger.warning("Folder creation failed: {error}", error=repr(e))
        return None


async def assign_playlist(session: tidalapi.Session, playlist_id: str, folder_id: str) -> bool:
    """Moves a playlist into a folder. Returns True on success.

    The empty body forces Content-Length: 0, which Tidal's firewall requires.
    """
    trn = f"trn:playlist:{normalises_playlist_id(playlist_id)}"
    try:
        await execute_network(
            _v2,
            session,
            "PUT",
            f"{_V2_PATH}/move",
            data=b"",
            folderId=folder_id,
            trns=trn,
        )
        # tidalapi's request() raises on any non-2xx, so reaching here means success.
        return True
    except Exception as e:
        logger.warning("Folder assignment failed: {error}", error=repr(e))
        return False


async def remove_folder(session: tidalapi.Session, folder_id: str) -> bool:
    """Deletes a V2 folder. Returns True on success."""
    try:
        await execute_network(
            _v2,
            session,
            "PUT",
            f"{_V2_PATH}/remove",
            data=b"",
            trns=f"trn:folder:{folder_id}",
        )
        # tidalapi's request() raises on any non-2xx, so reaching here means success.
        return True
    except Exception as e:
        logger.warning("Folder removal failed: {error}", error=repr(e))
        return False


async def ensure_v2_folder_exists(session: tidalapi.Session, name: str) -> str | None:
    """Returns the id of the folder named `name`, creating it if absent.

    `name` is the sanitised directory name (slashes and illegal characters
    already stripped). Tidal stores the raw name, so compare against the
    sanitised form to avoid creating a duplicate of an existing folder.
    """
    for folder_id, folder_name in await fetch_v2_folders(session):
        if sanitize_filename(folder_name) == name:
            return folder_id
    return await create_folder(session, name)


async def assign_playlist_to_v2_folder(
    session: tidalapi.Session, playlist_id: str, folder_id: str
) -> bool:
    """Moves a playlist into a folder. Thin wrapper over assign_playlist."""
    return await assign_playlist(session, playlist_id, folder_id)
