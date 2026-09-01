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
from typing import Any, Literal, cast

import requests
import tidalapi
from loguru import logger

from ..domain.exceptions import TidalPoisonError, TidalTransientError
from ..domain.protocols import TidalUser
from ..domain.results import UploadOutcome
from .network import execute_network, fetch_blocked_artists
from .workers import run_headless_tasks_async

_BLOCK_METHOD = Literal["POST", "DELETE"]


async def _apply_per_id(ids: list[str], action: Callable[[str], Any], label: str) -> UploadOutcome:
    """Runs `action` once per id, concurrently, and reports each outcome.

    The error boundary is deliberately narrow. `TidalTransientError` and
    `TidalPoisonError` are this id's problem and are recorded as rejections.
    `TidalRateLimitError` and any authentication failure are the account's
    problem: they propagate, cancel the sibling tasks through the TaskGroup,
    and abort the run rather than being misreported as hundreds of rejected
    items.

    `requests.HTTPError` is also a per-id outcome: `execute_network` re-raises
    a non-retryable HTTPError unchanged once `classify_error` returns None, so
    catching it here (after the gate has already had its say) is safe and is
    the right layer for a per-id classification.
    """
    results: dict[str, bool] = {}

    async def _one(item_id: str) -> None:
        try:
            ok = await execute_network(action, item_id)
        # The HTTPError that lands here came out of execute_network unchanged:
        # classify_error already classified it (and either retried, or returned
        # None for a non-retryable status). Catching it at this layer records
        # it as a per-id rejection without re-entering the gate.
        except (TidalTransientError, TidalPoisonError, requests.HTTPError) as e:
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


def _block_write_action(
    session: tidalapi.Session, method: _BLOCK_METHOD, per_artist_path: bool
) -> Callable[[str], Any]:
    """Builds the per-id block write. Exceptions are deliberately not caught
    here: `execute_network` must see them so `classify_error` can engage the
    abuse lock, retry a 5xx, and fail fast on a non-retryable status. The
    per-id boundary lives in `_apply_per_id`, outside the gate.
    """
    user = cast(TidalUser, cast(object, session.user))
    base_path = f"users/{user.id}/blocks/artists"

    def _action(artist_id: str) -> Any:
        path = f"{base_path}/{artist_id}" if per_artist_path else base_path
        if per_artist_path:
            return session.request.request(method, path)
        return session.request.request(
            method,
            path,
            params=None,
            data={"artistId": artist_id},
        )

    return _action


async def block_artists(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Blocks each artist id for the logged-in user.

    Probe confirmed (2026-09-01, throwaway account): POST
    users/{user_id}/blocks/artists with form field `artistId` returns 200
    with an empty body. The follow-up GET is the network's job; this engine
    trusts the per-id response.
    """
    return await _apply_per_id(ids, _block_write_action(session, "POST", False), "Block artist")


async def unblock_artists(session: tidalapi.Session, ids: list[str]) -> UploadOutcome:
    """Unblocks each artist id for the logged-in user.

    Probe confirmed (2026-09-01, throwaway account): DELETE
    users/{user_id}/blocks/artists/{artist_id} returns 204 with an empty
    body. The per-artist path is the correct shape; a body-bearing DELETE on
    the collection would be a different surface.
    """
    return await _apply_per_id(ids, _block_write_action(session, "DELETE", True), "Unblock artist")


async def fetch_blocked_artist_ids(session: tidalapi.Session) -> list[str]:
    """Lists the user's blocked artists as a flat list of string ids.

    Thin caller over `fetch_blocked_artists`; the pagination and error
    swallowing stay in network.py. Order matches the network order so the
    caller can correlate positions across runs.
    """
    artists = await execute_network(fetch_blocked_artists, session)
    return [str(a.id) for a in artists]
