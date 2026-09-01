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
Fault recovery for batch uploads.

Tidal rejects an entire batch upload if one track in it is region-locked or
has been removed from the catalogue. It also answers 200 while silently
skipping tracks it will not accept. This module isolates refused tracks so
the rest of the batch still uploads.

Recovery is a linear per-item scan rather than a recursive bisection. Chunks
are capped at CHUNK_SIZE (50), where bisection costs up to 2n-1 calls versus
n for a scan, and the scan never re-sends a track that was already applied.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger
from tidalapi.exceptions import ObjectNotFound

from ..domain.exceptions import TidalPoisonError
from ..domain.models import TrackRow
from .workers import ImportStats

if TYPE_CHECKING:
    from .importer import UploadOutcome

MEDIUM_DELAY = 0.2

# Only these mean "this specific item is bad". Anything else is an auth,
# network, or server problem and must not be blamed on the track.
_POISON_STATUS = frozenset({403, 404})
_RETRYABLE_STATUS = frozenset({412, 500, 502, 503, 504})


def is_poison(error: BaseException) -> bool:
    """Reports whether an error means one specific item must be dropped."""
    if isinstance(error, (TidalPoisonError, ObjectNotFound)):
        return True
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status in _POISON_STATUS


def _is_retryable(error: BaseException) -> bool:
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status in _RETRYABLE_STATUS


async def upload_batch_with_recovery(
    chunk: list[str],
    upload_callback: Callable[[list[str]], Awaitable["UploadOutcome"]],
    stats: ImportStats,
    staged_tracks_map: dict[str, TrackRow],
    dest_name: str,
    progress_bar: Any,
    task_id: Any,
) -> None:
    """Uploads a batch, isolating any tracks Tidal refuses to accept.

    The whole batch is attempted first. Rejections reported in the outcome
    are dropped directly, since Tidal has already named them. Only an
    exception widens the search to a per-item rescan.

    Args:
        chunk: The batch of Tidal UUIDs to upload.
        upload_callback: Uploads a batch, reporting what was applied and
            what was rejected.
        stats: Shared session counters.
        staged_tracks_map: UUID -> metadata, used to name dropped tracks.
        dest_name: Target playlist or category, for telemetry.
        progress_bar: Rich progress bar owned by the caller.
        task_id: The task bound to that progress bar.

    Raises:
        Any non-poison error: auth failures, rate limits, and server errors
        propagate. Blaming them on a track and marking it dead loses good
        data, so they are never absorbed here.
    """
    try:
        outcome = await upload_callback(chunk)
    except BaseException as e:
        if not is_poison(e):
            # A 412 is a version collision, not a bad track: retry once with
            # the same batch. Everything else propagates to the caller.
            if _is_retryable(e):
                logger.bind(audit=True).warning("Retryable chunk failure, retrying", error=str(e))
                await asyncio.sleep(1.0)
                outcome = await upload_callback(chunk)
            else:
                raise
        else:
            outcome = None

    if outcome is not None:
        await stats.add_added(len(outcome.applied))
        _log_added(outcome.applied, dest_name)
        progress_bar.advance(task_id, advance=len(outcome.applied))

        for tid in outcome.rejected:
            # Tidal named these itself, so there is nothing to isolate.
            await _drop_track(tid, stats, staged_tracks_map, dest_name)
            progress_bar.advance(task_id, advance=1)
        return

    # The batch as a whole was refused. Isolate the offenders one at a time.
    logger.bind(audit=True).error("Chunk rejected, isolating tracks", chunk_size=len(chunk))

    for tid in chunk:
        try:
            outcome = await upload_callback([tid])
        except BaseException as e:
            if not is_poison(e):
                raise
            await _drop_track(tid, stats, staged_tracks_map, dest_name)
        else:
            if outcome.rejected:
                await _drop_track(tid, stats, staged_tracks_map, dest_name)
            else:
                await stats.add_added(len(outcome.applied))
                _log_added(outcome.applied, dest_name)

        progress_bar.advance(task_id, advance=1)
        await asyncio.sleep(MEDIUM_DELAY)


def _log_added(track_ids: list[str], dest_name: str) -> None:
    for tid in track_ids:
        logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)


async def _drop_track(
    track_id: str,
    stats: ImportStats,
    staged_tracks_map: dict[str, TrackRow],
    dest_name: str,
) -> None:
    await stats.add_failed()
    track = staged_tracks_map.get(track_id)
    logger.bind(audit=True).error(
        "Dropped Track (Region Locked)",
        track_id=track_id,
        name=track.track_name if track else "Unknown",
        artist=track.artist_name if track else "Unknown",
        dest=dest_name,
    )
