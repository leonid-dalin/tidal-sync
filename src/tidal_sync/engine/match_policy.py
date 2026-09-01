"""Import match policy.

Decides what happens to one matched item: add it directly, skip it as a
duplicate, or record it as failed. This is import domain policy, so it lives
beside ImportStats rather than in the concurrency module that merely runs the
work.
"""

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from loguru import logger

from .workers import ImportStats


class MatchDecision(Enum):
    ADDED = "added"
    STAGED = "staged"
    SKIPPED = "skipped"
    FAILED = "failed"


async def decide(
    matched_id: str | None,
    item_type: str,
    item_name: str,
    artist_name: str,
    source_file: str,
    dest_name: str,
    existing_ids: set[str],
    stats: ImportStats,
    add_method: Callable[[str], Awaitable[Any]] | None = None,
    ids_to_add: list[str] | None = None,
    failure_reason: str = "Not Found on Tidal",
) -> MatchDecision:
    """Classifies one match and updates the session counters accordingly.

    The duplicate check runs inside the stats lock and holds it across the
    set-add and list-append, so two workers matching the same item cannot
    both decide it is new.
    """
    if not matched_id:
        await stats.add_failed()
        logger.bind(audit=True).warning(
            "Failed to Match",
            type=item_type,
            name=item_name,
            artist=artist_name,
            source=source_file,
            reason=failure_reason,
        )
        return MatchDecision.FAILED

    is_new = False
    async with stats.lock:
        if matched_id not in existing_ids:
            existing_ids.add(matched_id)
            if ids_to_add is not None:
                ids_to_add.append(matched_id)
            is_new = True

    if not is_new:
        await stats.add_skipped()
        logger.bind(audit=True).info(
            "Skipped (Duplicate)",
            type=item_type,
            name=item_name,
            artist=artist_name,
            dest=dest_name,
        )
        return MatchDecision.SKIPPED

    if add_method:
        try:
            await add_method(matched_id)
            await stats.add_added()
            return MatchDecision.ADDED
        except Exception as e:
            # Single-item adds (albums, artists) fail on region locks. Record
            # it and keep going: one dead album must not stop the batch.
            await stats.add_failed()
            logger.bind(audit=True).error(
                "Item Add Failed (Region Locked or Removed)",
                type=item_type,
                name=item_name,
                artist=artist_name,
                error=str(e),
            )
            return MatchDecision.FAILED

    logger.bind(audit=True).debug("Item Staged", type=item_type, name=item_name, dest=dest_name)
    return MatchDecision.STAGED
