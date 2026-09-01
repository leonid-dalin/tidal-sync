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


class FolderSession:
    """Has one playlist and two folders; folders are counted even on dry run."""

    class user:
        id = 1

        class favorites:
            @staticmethod
            def tracks(**kw):
                return []

            @staticmethod
            def albums(**kw):
                return []

            @staticmethod
            def artists(**kw):
                return []

        @staticmethod
        def playlists(**kw):
            pl = type("P", (), {"id": "p1"})()

            def _delete():
                return None

            pl.delete = _delete
            return [pl]


async def test_dry_run_counts_folders_like_a_live_run(monkeypatch):
    # CRIT-1: folders must be counted on a dry run, not just deleted live.
    folders = [("f1", "A"), ("f2", "B")]

    async def _fake_fetch_v2_folders(session):
        return list(folders)

    async def _fake_remove_folder(session, folder_id):
        return True

    monkeypatch.setattr("tidal_sync.engine.wiping.fetch_v2_folders", _fake_fetch_v2_folders)
    monkeypatch.setattr("tidal_sync.engine.wiping.remove_folder", _fake_remove_folder)

    live = await purge_target_category_async(FolderSession(), ClearTarget.ALL)
    dry = await purge_target_category_async(FolderSession(), ClearTarget.ALL, dry_run=True)

    assert dry.requested == live.requested, (dry.requested, live.requested)
    assert dry.deleted == 0
    assert live.deleted == len(folders) + 1  # one playlist plus two folders


async def test_requested_is_an_upper_bound_on_deleted_plus_failed(monkeypatch):
    # MAJ-6: every target is accounted for, so failures cannot exceed requested.
    folders = [("f1", "A"), ("f2", "B")]

    async def _fake_fetch_v2_folders(session):
        return list(folders)

    async def _fake_remove_folder(session, folder_id):
        return True

    monkeypatch.setattr("tidal_sync.engine.wiping.fetch_v2_folders", _fake_fetch_v2_folders)
    monkeypatch.setattr("tidal_sync.engine.wiping.remove_folder", _fake_remove_folder)

    report = await purge_target_category_async(FolderSession(), ClearTarget.ALL)
    assert report.deleted + report.failed <= report.requested


async def test_dry_run_prints_each_category_it_counts(capsys, monkeypatch):
    # m-1: a dry run names every category it counted, so the dry report
    # mirrors the live one and the user can predict the destructive run.
    folders = [("f1", "A"), ("f2", "B")]

    async def _fake_fetch_v2_folders(session):
        return list(folders)

    async def _fake_remove_folder(session, folder_id):
        return True

    monkeypatch.setattr("tidal_sync.engine.wiping.fetch_v2_folders", _fake_fetch_v2_folders)
    monkeypatch.setattr("tidal_sync.engine.wiping.remove_folder", _fake_remove_folder)

    await purge_target_category_async(FolderSession(), ClearTarget.ALL, dry_run=True)
    out = capsys.readouterr().out

    assert "Would remove 1 playlists" in out, out
    assert "Would remove 2 folders" in out, out


async def test_purge_v2_folders_helper_requires_explicit_dry_run():
    # m-4: the private helper has no default for dry_run. The single caller
    # passes an explicit value, and the helper cannot choose the destructive
    # path by omission.
    import inspect

    from tidal_sync.engine.wiping import _purge_v2_folders_async

    sig = inspect.signature(_purge_v2_folders_async)
    assert sig.parameters["dry_run"].default is inspect.Parameter.empty
