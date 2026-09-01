"""A purge must account for every item it touched.

Deletion errors were logged at DEBUG with no audit sink configured, so a
wipe that deleted nothing still reported success.
"""

from tidal_sync.domain.enums import ClearTarget
from tidal_sync.engine.wiping import purge_target_category_async


class Track:
    def __init__(self, tid):
        self.id = tid


class ExplodingSession:
    """Every deletion raises; nothing may be reported as deleted.

    tracks() returns real items. Returning [] here would let the purge
    exit before attempting anything, so the assertion on `failed` could
    never hold.
    """

    class user:
        id = 1

        class favorites:
            @staticmethod
            def tracks(**kw):
                return [Track("t1"), Track("t2")]

            @staticmethod
            def remove_track(tid):
                raise RuntimeError("403 forbidden")

        @staticmethod
        def playlists(**kw):
            return []


async def test_all_failing_deletions_are_reported_as_failures():
    report = await purge_target_category_async(ExplodingSession(), ClearTarget.TRACKS)

    assert report.requested == 2
    assert report.deleted == 0
    assert report.failed == 2
    assert "403 forbidden" in report.failures[0]


async def test_dry_run_deletes_nothing():
    report = await purge_target_category_async(ExplodingSession(), ClearTarget.TRACKS, dry_run=True)

    assert report.requested == 2
    assert report.deleted == 0
    assert report.failed == 0
