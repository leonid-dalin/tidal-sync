"""Fault recovery must not blame a track for an auth or server failure.

The old handler caught HTTPError, which covers 401 and 5xx, so an
expired token was logged as "region locked" and the track marked dead.
"""

import pytest
import requests

from tidal_sync.domain.exceptions import TidalPoisonError
from tidal_sync.engine.importer import UploadOutcome
from tidal_sync.engine.upload_recovery import upload_batch_with_recovery
from tidal_sync.engine.workers import ImportStats


class StubProgress:
    def __init__(self):
        self.advanced = 0

    def advance(self, task_id, advance=1):
        self.advanced += advance


def _ok(chunk):
    """Tidal accepted everything."""
    return UploadOutcome(applied=list(chunk), rejected=[])


def _poison_response(status):
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


async def test_unauthorized_is_not_reported_as_region_locked():
    """A 401 is an auth problem, never a region-locked track."""

    async def upload(chunk):
        raise _poison_response(401)

    stats = ImportStats()
    with pytest.raises(Exception) as excinfo:
        await upload_batch_with_recovery(
            ["t1", "t2"], upload, stats, {}, "dest", StubProgress(), None
        )
    assert not isinstance(excinfo.value, TidalPoisonError)
    assert stats.failed == 0, "no track should be marked dead for a 401"


async def test_server_error_is_not_reported_as_region_locked():
    async def upload(chunk):
        raise _poison_response(503)

    stats = ImportStats()
    with pytest.raises(requests.exceptions.HTTPError):
        await upload_batch_with_recovery(["t1"], upload, stats, {}, "dest", StubProgress(), None)
    assert stats.failed == 0


async def test_only_the_poison_track_is_dropped():
    async def upload(chunk):
        if "bad" in chunk:
            raise _poison_response(404)
        return _ok(chunk)

    stats = ImportStats()
    progress = StubProgress()
    await upload_batch_with_recovery(
        ["good1", "bad", "good2"], upload, stats, {}, "dest", progress, None
    )

    assert stats.added == 2
    assert stats.failed == 1
    assert progress.advanced == 3  # every track accounted for exactly once


async def test_progress_matches_outcomes():
    async def upload(chunk):
        if "bad" in chunk:
            raise _poison_response(403)
        return _ok(chunk)

    stats = ImportStats()
    progress = StubProgress()
    await upload_batch_with_recovery(["a", "bad", "b"], upload, stats, {}, "dest", progress, None)
    assert progress.advanced == stats.added + stats.failed


async def test_server_side_rejections_are_not_counted_as_added():
    """F41: Tidal answers 200 and skips tracks. That is a rejection."""

    async def upload(chunk):
        applied = [tid for tid in chunk if tid != "locked"]
        rejected = [tid for tid in chunk if tid == "locked"]
        return UploadOutcome(applied=applied, rejected=rejected)

    stats = ImportStats()
    progress = StubProgress()
    await upload_batch_with_recovery(
        ["a", "locked", "b"], upload, stats, {}, "dest", progress, None
    )

    assert stats.added == 2
    assert stats.failed == 1
    assert progress.advanced == 3


async def test_rejections_do_not_trigger_a_rescan():
    """A server-side skip already names the offender; rescanning is waste."""
    calls = []

    async def upload(chunk):
        calls.append(list(chunk))
        rejected = [tid for tid in chunk if tid == "locked"]
        applied = [tid for tid in chunk if tid != "locked"]
        return UploadOutcome(applied=applied, rejected=rejected)

    stats = ImportStats()
    await upload_batch_with_recovery(
        ["a", "locked", "b"], upload, stats, {}, "dest", StubProgress(), None
    )

    assert calls == [["a", "locked", "b"]]
    assert stats.added == 2
    assert stats.failed == 1


async def test_etag_mismatch_still_retries_the_whole_chunk():
    """A 412 is a version collision, not a bad track."""
    calls = []

    def upload_impl(chunk):
        calls.append(list(chunk))
        if len(calls) == 1:
            raise _poison_response(412)
        return _ok(chunk)

    async def upload(chunk):
        return upload_impl(chunk)

    stats = ImportStats()
    await upload_batch_with_recovery(["a", "b"], upload, stats, {}, "dest", StubProgress(), None)

    assert stats.added == 2
    assert stats.failed == 0
