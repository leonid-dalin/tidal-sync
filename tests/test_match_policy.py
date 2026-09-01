"""Import policy must classify one match and update counters correctly.

The duplicate check runs inside the stats lock, so two workers matching the
same id cannot both decide it is new. Staging is not an add: the counter moves
only when the upload lands.
"""

import asyncio

from tidal_sync.engine.match_policy import MatchDecision, decide
from tidal_sync.engine.workers import ImportStats


async def test_unmatched_is_failed():
    stats = ImportStats()
    outcome = await decide(
        matched_id=None,
        item_type="Track",
        item_name="Song",
        artist_name="Artist",
        source_file="a.csv",
        dest_name="Liked Songs",
        existing_ids=set(),
        stats=stats,
        failure_reason="Text search failed",
    )
    assert outcome is MatchDecision.FAILED
    assert stats.failed == 1
    assert stats.added == 0


async def test_duplicate_is_skipped_not_added():
    stats = ImportStats()
    existing = {"t1"}
    outcome = await decide(
        matched_id="t1",
        item_type="Track",
        item_name="Song",
        artist_name="Artist",
        source_file="a.csv",
        dest_name="Liked Songs",
        existing_ids=existing,
        stats=stats,
    )
    assert outcome is MatchDecision.SKIPPED
    assert stats.skipped == 1
    assert stats.added == 0


async def test_new_item_is_staged():
    stats = ImportStats()
    ids_to_add: list[str] = []
    outcome = await decide(
        matched_id="t9",
        item_type="Track",
        item_name="Song",
        artist_name="Artist",
        source_file="a.csv",
        dest_name="Liked Songs",
        existing_ids=set(),
        stats=stats,
        ids_to_add=ids_to_add,
    )
    assert outcome is MatchDecision.STAGED
    assert ids_to_add == ["t9"]
    # Staging is not an add. The counter moves only when the upload lands.
    assert stats.added == 0


async def test_add_method_marks_added():
    stats = ImportStats()
    captured: list[str] = []

    async def add(a_id: str) -> None:
        captured.append(a_id)

    outcome = await decide(
        matched_id="t2",
        item_type="Album",
        item_name="LP",
        artist_name="Artist",
        source_file="a.csv",
        dest_name="Liked Albums",
        existing_ids=set(),
        stats=stats,
        add_method=add,
    )
    assert outcome is MatchDecision.ADDED
    assert captured == ["t2"]
    assert stats.added == 1


async def test_add_failure_is_recorded_not_crashed():
    stats = ImportStats()

    async def boom(_: str) -> None:
        raise RuntimeError("region locked")

    outcome = await decide(
        matched_id="t3",
        item_type="Album",
        item_name="LP",
        artist_name="Artist",
        source_file="a.csv",
        dest_name="Liked Albums",
        existing_ids=set(),
        stats=stats,
        add_method=boom,
    )
    assert outcome is MatchDecision.FAILED
    assert stats.failed == 1
    assert stats.added == 0


async def test_duplicate_added_once_under_concurrent_lock():
    stats = ImportStats()
    existing: set[str] = set()
    seen: list[str] = []

    outcomes = await asyncio.gather(
        decide("t1", "Track", "S", "A", "f", "d", existing, stats, ids_to_add=seen),
        decide("t1", "Track", "S", "A", "f", "d", existing, stats, ids_to_add=seen),
    )
    assert outcomes == [MatchDecision.STAGED, MatchDecision.SKIPPED]
    assert seen == ["t1"]
