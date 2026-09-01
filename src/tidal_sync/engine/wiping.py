"""
Tidal library clearance and data purging engine.

This module facilitates the destructive removal of user data from a Tidal account.
It utilises concurrent, headless worker groups to rapidly process deletion requests
across various categories without blocking the primary application loop. It also
includes raw HTTP bypasses to ensure undocumented V2 entities, such as folders,
are properly eradicated.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import requests
import tidalapi
from loguru import logger
from rich.console import Console

from ..domain.enums import ClearTarget
from ..domain.protocols import TidalUser
from .network import execute_network, fetch_all_async, fetch_blocked_artists
from .workers import run_headless_tasks_async

console = Console()


@dataclass
class PurgeReport:
    """Outcome of a destructive purge. Every item is accounted for."""

    requested: int = 0
    deleted: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    def record_failure(self, detail: str) -> None:
        self.failed += 1
        # Bounded so a 10k-item wipe does not buffer 10k strings.
        if len(self.failures) < 50:
            self.failures.append(detail)


async def _purge_v2_folders_async(session: tidalapi.Session, report: PurgeReport) -> None:
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

    def _auth_headers() -> dict[str, str]:
        # Rebuilt per request: a long wipe outlives the access token, and a
        # stale Bearer token turns every later request into a swallowed 401.
        return {
            "Authorization": f"Bearer {session.access_token}",
            "Accept": "application/json",
        }

    # 1. Fetch all existing folders (Bypassing tidalapi entirely)
    offset = 0
    while True:
        params = base_params.copy()
        params.update({"includeOnly": "FOLDER", "offset": offset, "limit": 50})

        try:

            def _fetch_folders(params=params):
                return requests.get(
                    f"{base_v2_url}/my-collection/playlists/folders/flattened",
                    params=params,
                    headers=_auth_headers(),
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

        del_headers = {**_auth_headers(), "Content-Type": "application/json"}

        def _execute_remove():
            return requests.put(
                f"{base_v2_url}/my-collection/playlists/folders/remove",
                params=delete_params,
                headers=del_headers,
                data=b"",  # Forces Content-Length: 0
            )

        try:
            res = await asyncio.to_thread(_execute_remove)
            if not res.ok:
                detail = f"folder {folder_id}: HTTP {res.status_code}"
                logger.warning("Folder deletion failed: {detail}", detail=detail)
                report.record_failure(detail)
            else:
                report.deleted += 1
        except Exception as e:
            detail = f"folder {folder_id}: {e}"
            logger.warning("Folder deletion error: {detail}", detail=detail)
            report.record_failure(detail)

    async def _async_wrapper(f_id: str):
        await _delete_folder(f_id)

    await run_headless_tasks_async(folders_to_delete, _async_wrapper)


async def purge_target_category_async(
    session: tidalapi.Session,
    target: ClearTarget,
    dry_run: bool = False,
) -> PurgeReport:
    """
    Destructively removes items from a user's Tidal account based on the selected target.

    Executes concurrent deletion requests across the designated category (e.g., tracks,
    albums, playlists). A failure is counted and reported rather than absorbed: a wipe
    that reports success while deleting nothing is worse than no wipe at all.

    Args:
        session (tidalapi.Session): The active, authenticated Tidal session.
        target (ClearTarget): The specific library segment to wipe.
        dry_run: When set, count what would be removed without deleting.

    Returns:
        PurgeReport: The counts of what was requested, deleted and failed.
    """
    user = cast(TidalUser, cast(object, session.user))
    report = PurgeReport()

    if dry_run:
        console.print("[yellow]--dry-run: no changes will be made.[/yellow]")

    favorites_available = hasattr(user, "favorites")

    async def _clear_category_async(
        items: list[Any],
        sync_action_factory: Callable[[Any], Callable[[], Any]],
        category_name: str,
    ) -> None:
        if not items:
            return

        report.requested += len(items)
        console.print(f"[cyan]Removing {len(items)} {category_name}...[/cyan]")

        if dry_run:
            return

        async def _async_wrapper(item: Any):
            try:
                await execute_network(sync_action_factory(item))
                report.deleted += 1
            except Exception as e:
                report.record_failure(f"{category_name}: {e}")
                logger.warning(
                    "Deletion failed for {category}: {error}",
                    category=category_name,
                    error=str(e),
                )

        await run_headless_tasks_async(items, _async_wrapper)

    # 1. Clear Playlists and Folders
    if target in (ClearTarget.ALL, ClearTarget.PLAYLISTS):
        playlists = await fetch_all_async(user.playlists)
        await _clear_category_async(playlists, lambda p: p.delete, "playlists")

        if not dry_run:
            await _purge_v2_folders_async(session, report)

    if not favorites_available:
        if target in (
            ClearTarget.TRACKS,
            ClearTarget.ALBUMS,
            ClearTarget.ARTISTS,
            ClearTarget.ALL,
        ):
            console.print("[yellow]This account does not expose a favorites collection.[/yellow]")
        return report

    if target in (ClearTarget.ALL, ClearTarget.TRACKS):
        tracks = await fetch_all_async(user.favorites.tracks)
        await _clear_category_async(
            tracks, lambda t: lambda: user.favorites.remove_track(str(t.id)), "liked songs"
        )

    # 3. Clear Albums
    if target in (ClearTarget.ALL, ClearTarget.ALBUMS):
        albums = await fetch_all_async(user.favorites.albums)
        await _clear_category_async(
            albums, lambda a: lambda: user.favorites.remove_album(str(a.id)), "liked albums"
        )

    # 4. Clear Artists & Blocklist
    if target in (ClearTarget.ALL, ClearTarget.ARTISTS):
        artists = await fetch_all_async(user.favorites.artists)
        await _clear_category_async(
            artists, lambda art: lambda: user.favorites.remove_artist(str(art.id)), "artists"
        )

        try:
            blocked = await execute_network(fetch_blocked_artists, session)
            if blocked:

                def _unblock_factory(art):
                    return lambda: session.request.request(
                        "DELETE", f"users/{user.id}/blocks/artists/{art.id}"
                    )

                await _clear_category_async(blocked, _unblock_factory, "blocked artists")
        except Exception as e:
            report.record_failure(f"blocked artists: {e}")
            logger.warning("Failed to clear blocked artists: {error}", error=str(e))

    return report
