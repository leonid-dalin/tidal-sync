"""
Fault recovery and batch bisection engine.

Tidal's API rejects entire batch uploads if a single track within the array
is geographically restricted (region-locked) or has been removed from their
catalogue. This module provides a recursive bisection algorithm to identify
and isolate these "poison" tracks, allowing the rest of the batch to upload
successfully rather than halting the synchronisation process.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from requests.exceptions import HTTPError
from rich.console import Console
from tidalapi.exceptions import ObjectNotFound

from ..domain.models import TrackRow
from .workers import ImportStats

console = Console()
MEDIUM_DELAY = 0.2


async def upload_batch_with_bisection_recovery(
    chunk: list[str],
    upload_callback: Callable[[list[str]], Awaitable[None]],
    stats: ImportStats,
    staged_tracks_map: dict[str, TrackRow],
    dest_name: str,
    progress_bar: Any,
    task_id: Any,
) -> None:
    """
    Attempts to upload a batch of tracks, applying recursive bisection if rejected.

    This function first attempts to push the full array. If it encounters a 412
    Stale ETag error, it momentarily pauses and retries. If it encounters a 404
    or 403 (typically indicating a region-locked track), it splits the array in
    half and recursively retries both halves to isolate the exact track causing
    the failure.

    Args:
        chunk (list[str]): The batch of Tidal UUIDs to upload.
        upload_callback (Callable): The asynchronous function responsible for the network call.
        stats (ImportStats): The shared session counter to increment upon success or failure.
        staged_tracks_map (dict): A mapping of UUIDs to metadata to identify dropped tracks.
        dest_name (str): The name of the target playlist or folder for telemetry.
        progress_bar (Any): The Rich UI progress bar instance.
        task_id (Any): The specific task ID bound to the progress bar.
    """
    try:
        await upload_callback(chunk)
        await stats.add_added(len(chunk))
        for tid in chunk:
            logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)
        progress_bar.advance(task_id, advance=len(chunk))

    except (HTTPError, ObjectNotFound) as e:
        # GUARD: Do not bisect if the error is a 412 ETag mismatch.
        # A 412 indicates a server-side version collision, not a broken track.
        is_etag_error = isinstance(e, HTTPError) and getattr(e.response, "status_code", None) == 412

        if is_etag_error:
            logger.bind(audit=True).warning("HTTP 412 (Stale ETag) detected. Retrying chunk...")
            try:
                await asyncio.sleep(1.0)
                await upload_callback(chunk)
                await stats.add_added(len(chunk))
                for tid in chunk:
                    logger.bind(audit=True).debug(
                        "Item Added", type="Track", id=tid, dest=dest_name
                    )
                progress_bar.advance(task_id, advance=len(chunk))
                return
            except Exception as retry_e:
                e = retry_e

        logger.bind(audit=True).error(
            "Chunk rejected, initiating bisection", chunk_size=len(chunk), error=str(e)
        )

        await _bisect_recursive_async(chunk, upload_callback, stats, staged_tracks_map, dest_name)
        progress_bar.advance(task_id, advance=len(chunk))
        await asyncio.sleep(MEDIUM_DELAY * 2)


async def _bisect_recursive_async(
    sub_chunk: list[str],
    upload_callback: Callable[[list[str]], Awaitable[None]],
    stats: ImportStats,
    staged_tracks_map: dict[str, TrackRow],
    dest_name: str,
) -> None:
    """The internal recursive loop that halves failing arrays to isolate poison tracks."""
    if not sub_chunk:
        return

    try:
        await upload_callback(sub_chunk)
        await stats.add_added(len(sub_chunk))
        for tid in sub_chunk:
            logger.bind(audit=True).debug("Item Added", type="Track", id=tid, dest=dest_name)

    except (HTTPError, ObjectNotFound) as _:
        # Base case: We have isolated the single failing track.
        if len(sub_chunk) == 1:
            poison_id = sub_chunk[0]
            poison_track = staged_tracks_map.get(poison_id)

            track_title = poison_track.track_name if poison_track else "Unknown"
            track_artist = poison_track.artist_name if poison_track else "Unknown"

            logger.bind(audit=True).error(
                "Dropped Track (Region Locked)",
                track_id=poison_id,
                name=track_title,
                artist=track_artist,
                dest=dest_name,
            )
            console.print(
                f"  [red]❌ Dropped (Region-locked): {track_title} by {track_artist}[/red]"
            )
            await stats.add_failed()
        else:
            # Recursive case: Split the array in half and test both sides.
            await asyncio.sleep(MEDIUM_DELAY * 2)
            mid = len(sub_chunk) // 2
            await _bisect_recursive_async(
                sub_chunk[:mid], upload_callback, stats, staged_tracks_map, dest_name
            )
            await _bisect_recursive_async(
                sub_chunk[mid:], upload_callback, stats, staged_tracks_map, dest_name
            )
