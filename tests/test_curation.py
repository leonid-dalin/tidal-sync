"""Curation engine: favourites and artist blocks."""

import pytest

from tests.fakes import FakeSession
from tidal_sync.domain.exceptions import TidalRateLimitError, TidalTransientError
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine import curation


def test_upload_outcome_is_importable_from_domain():
    """Two engines share this type, so it lives in domain, not in the importer."""
    outcome = UploadOutcome(applied=["1"], rejected=["2"])

    assert outcome.applied == ["1"]
    assert outcome.rejected == ["2"]


async def test_every_id_is_sent_individually():
    """Favorites.add_* returns one bool for a comma-joined batch, so batching
    would make per-id reporting a lie. One request per id is the contract."""
    session = FakeSession()

    outcome = await curation.like_tracks(session, ["1", "2", "3"])

    assert outcome.applied == ["1", "2", "3"]
    assert outcome.rejected == []
    assert session.user.favorites.added_tracks == ["1", "2", "3"]


async def test_one_rejected_id_does_not_sink_the_rest():
    session = FakeSession()
    session.user.favorites.reject = {"2"}

    outcome = await curation.like_tracks(session, ["1", "2", "3"])

    assert outcome.applied == ["1", "3"]
    assert outcome.rejected == ["2"]


async def test_results_are_ordered_by_input_not_completion():
    """Fan-out is concurrent, so the report must be sorted back into input
    order or the CLI prints a different sequence run to run."""
    session = FakeSession()
    session.user.favorites.reject = {"1"}

    outcome = await curation.like_tracks(session, ["1", "2", "3", "4"])

    assert outcome.applied == ["2", "3", "4"]
    assert outcome.rejected == ["1"]


async def test_a_transient_failure_is_recorded_per_id():
    session = FakeSession()
    session.user.favorites.raise_for = {"2": TidalTransientError("gave up")}

    outcome = await curation.like_tracks(session, ["1", "2", "3"])

    assert outcome.applied == ["1", "3"]
    assert outcome.rejected == ["2"]


async def test_an_account_level_failure_aborts_the_whole_run():
    """A rate limit or abuse lock is not this id's fault. Counting it as a
    per-id rejection would report a throttled account as hundreds of missing
    items and keep hammering the gate."""
    session = FakeSession()
    session.user.favorites.raise_for = {"2": TidalRateLimitError("locked")}

    with pytest.raises(BaseExceptionGroup) as excinfo:
        await curation.like_tracks(session, ["1", "2", "3"])
    assert excinfo.group_contains(TidalRateLimitError)


async def test_unlike_sends_one_id_at_a_time():
    """Favorites.remove_* returns False for a list rather than raising, so a
    list must never reach it."""
    session = FakeSession()

    outcome = await curation.unlike_tracks(session, ["1", "2"])

    assert outcome.applied == ["1", "2"]
    assert session.user.favorites.removed_tracks == ["1", "2"]


async def test_empty_input_makes_no_requests():
    session = FakeSession()

    outcome = await curation.like_tracks(session, [])

    assert outcome == curation.UploadOutcome(applied=[], rejected=[])
    assert session.user.favorites.added_tracks == []
