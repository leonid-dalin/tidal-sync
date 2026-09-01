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
Library curation: favourites and artist blocks.

Every verb applies one id per request. tidalapi's `Favorites.add_*` accepts a
list but comma-joins it into a single POST returning a single boolean, so a
batched call cannot say which id failed. The importer reached the same
conclusion at `_build_favorites_uploader`; this module follows it. Throughput
is bounded by the shared semaphore in `run_headless_tasks_async`, not by
batch size.
"""

from collections.abc import Callable
from typing import Any, cast

import tidalapi
from loguru import logger

from ..domain.exceptions import TidalPoisonError, TidalTransientError
from ..domain.protocols import TidalUser
from ..domain.results import UploadOutcome
from .network import execute_network
from .workers import run_headless_tasks_async


async def _apply_per_id(ids: list[str], action: Callable[[str], Any], label: str) -> UploadOutcome:
    """Runs `action` once per id, concurrently, and reports each outcome.

    The error boundary is deliberately narrow. `TidalTransientError` and
    `TidalPoisonError` are this id's problem and are recorded as rejections.
    `TidalRateLimitError` and any authentication failure are the account's
    problem: they propagate, cancel the sibling tasks through the TaskGroup,
    and abort the run rather than being misreported as hundreds of rejected
    items.
    """
    results: dict[str, bool] = {}

    async def _one(item_id: str) -> None:
        try:
            ok = await execute_network(action, item_id)
        except (TidalTransientError, TidalPoisonError) as e:
            logger.bind(audit=True).error("{label} failed", label=label, id=item_id, error=repr(e))
            results[item_id] = False
            return
        results[item_id] = bool(ok)

    await run_headless_tasks_async(list(ids), _one)

    # Sorted back into input order: the fan-out completes out of order and a
    # report whose sequence changes run to run is not a report.
    return UploadOutcome(
        applied=[i for i in ids if results.get(i)],
        rejected=[i for i in ids if not results.get(i)],
    )


async def like_tracks(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Adds each track to the user's favourites."""
    user = cast(TidalUser, cast(object, session.user))
    return await _apply_per_id(ids, user.favorites.add_track, "Like track")


async def like_artists(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Adds each artist to the user's favourites."""
    user = cast(TidalUser, cast(object, session.user))
    return await _apply_per_id(ids, user.favorites.add_artist, "Like artist")


async def like_albums(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Adds each album to the user's favourites."""
    user = cast(TidalUser, cast(object, session.user))
    return await _apply_per_id(ids, user.favorites.add_album, "Like album")


async def unlike_tracks(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Removes each track from the user's favourites."""
    user = cast(TidalUser, cast(object, session.user))
    return await _apply_per_id(ids, user.favorites.remove_track, "Unlike track")


async def unlike_artists(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Removes each artist from the user's favourites."""
    user = cast(TidalUser, cast(object, session.user))
    return await _apply_per_id(ids, user.favorites.remove_artist, "Unlike artist")


async def unlike_albums(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Removes each album from the user's favourites."""
    user = cast(TidalUser, cast(object, session.user))
    return await _apply_per_id(ids, user.favorites.remove_album, "Unlike album")
