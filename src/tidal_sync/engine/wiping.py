"""
Tidal library clearance and data purging engine.

This module facilitates the destructive removal of user data from a Tidal account.
It utilises concurrent, headless worker groups to rapidly process deletion requests
across various categories without blocking the primary application loop. It also
includes raw HTTP bypasses to ensure undocumented V2 entities, such as folders,
are properly eradicated.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import tidalapi
from loguru import logger
from rich.console import Console

from ..domain.enums import ClearTarget
from ..domain.protocols import TidalUser
from .folders import fetch_v2_folders, remove_folder
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


async def _purge_v2_folders_async(
    session: tidalapi.Session, report: PurgeReport, dry_run: bool
) -> None:
    """Deletes every V2 folder, accounting for each outcome.

    The ``dry_run`` flag has no default: this helper has exactly one caller and
    must always receive an explicit value, so the destructive path is not
    chosen by omission. The flag is mirrored by ``_clear_category_async`` and
    counts and names targets in the same order whether live or dry, so the
    user can predict the destructive run from the dry report.
    """
    try:
        folders = await fetch_v2_folders(session)
    except Exception as e:
        report.record_failure(f"folder listing: {e}")
        return

    if not folders:
        return

    # Counted and named even on a dry run so the report mirrors a live run.
    report.requested += len(folders)
    verb = "Would remove" if dry_run else "Removing"
    console.print(f"[cyan]{verb} {len(folders)} folders...[/cyan]")

    if dry_run:
        return

    async def _delete(entry: tuple[str, str]) -> None:
        folder_id, folder_name = entry
        if await remove_folder(session, folder_id):
            report.deleted += 1
        else:
            report.record_failure(f"folder '{folder_name}' ({folder_id})")

    await run_headless_tasks_async(folders, _delete)


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
        verb = "Would remove" if dry_run else "Removing"
        console.print(f"[cyan]{verb} {len(items)} {category_name}...[/cyan]")

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

        # Folder purging counts targets on a dry run and only deletes when live.
        await _purge_v2_folders_async(session, report, dry_run=dry_run)

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
