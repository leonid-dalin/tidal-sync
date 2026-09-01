"""Per-file failure isolation during a directory import.

A backup directory can hold hundreds of CSVs. One undecodable file must
not cost the user the rest of the run, nor the summary that tells them
what happened.
"""

import pytest

from tidal_sync.domain.models import TrackRow
from tidal_sync.engine.importer import (
    ImportStats,
    import_collection_from_disk,
    resolve_and_import_playlist,
)
from tidal_sync.engine.parser import parse_csv


class StubSession:
    """Parses nothing; enough to reach the per-file loop."""

    class user:
        id = 1


def test_undecodable_bytes_are_not_reported_as_success(tmp_path):
    # cp1252 and latin-1 accept nearly any byte sequence, so corrupt input
    # used to decode to garbage and yield zero rows without complaint.
    bad = tmp_path / "Bad.csv"
    bad.write_bytes(b"\xff\xfe\x00\x01not a csv at all")

    with pytest.raises(ValueError, match="no valid rows"):
        parse_csv(bad, TrackRow)


def test_a_headerless_file_is_rejected(tmp_path):
    empty = tmp_path / "Headerless.csv"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        parse_csv(empty, TrackRow)


async def test_one_bad_file_does_not_abort_the_directory(tmp_path, capsys):
    good = tmp_path / "Good.csv"
    good.write_text(
        "track_name,artist_name,album_name,isrc,tidal_id\nSong,Artist,Album,ISRC1,1\n",
        encoding="utf-8",
    )
    bad = tmp_path / "Bad.csv"
    bad.write_bytes(b"\xff\xfe\x00\x01not a csv at all")

    # Must not raise.
    await import_collection_from_disk(StubSession(), tmp_path)

    out = capsys.readouterr().out
    assert "Audit Report Generated" in out, "the summary must still print"


async def test_failed_files_are_named_in_the_summary(tmp_path, capsys):
    bad = tmp_path / "Broken.csv"
    bad.write_bytes(b"\xff\xfe\x00\x01not a csv at all")

    await import_collection_from_disk(StubSession(), tmp_path)

    out = capsys.readouterr().out
    assert "file(s) failed" in out
    assert "Broken.csv" in out


class RecordingArtistSession:
    """Records the artist ids it was asked to add."""

    def __init__(self):
        self.added: list[str] = []
        self.searches: list[str] = []
        self.user = self.User(self)

    class User:
        id = 1

        def __init__(self, session):
            self.favorites = RecordingArtistSession.Favorites(session)

    class Favorites:
        def __init__(self, session):
            self._session = session

        def artists(self, *a, **kw):
            return []

        def add_artist(self, a_id: str) -> None:
            self._session.added.append(a_id)

    def search(self, query: str, *a, **kw):
        self.searches.append(query)
        return {"artists": [], "tracks": [], "albums": [], "playlists": [], "videos": []}


async def test_followed_artists_file_routes_to_the_artist_importer(tmp_path):
    csv = tmp_path / "Followed Artists.csv"
    csv.write_text(
        "artist_name,tidal_id\nHelena,111\n",
        encoding="utf-8",
    )
    session = RecordingArtistSession()

    await resolve_and_import_playlist(session, csv, None, ImportStats())

    assert session.added == ["111"], "the artist importer must be reached"


async def test_artist_file_is_not_parsed_as_tracks(tmp_path):
    csv = tmp_path / "Followed Artists.csv"
    csv.write_text(
        "artist_name,tidal_id\nHelena,111\n",
        encoding="utf-8",
    )
    session = RecordingArtistSession()

    await resolve_and_import_playlist(session, csv, None, ImportStats())

    assert session.searches == [], "no track search should fire for an artist file"


class TrackMatchingSession:
    """Drive import_tracks_category_async with a controllable matcher.

    The matcher raises for one nominated track so we can prove the
    per-item error boundary keeps the remaining matches flowing into
    track_ids_to_add instead of cancelling the whole TaskGroup.
    """

    def __init__(self, fail_track_name: str):
        self.fail_track_name = fail_track_name
        self.matched_ids: list[str] = []
        self.user = self.User()

    class User:
        id = 1
        favorites = None

        def playlists(self, *a, **kw):
            return []

        def create_playlist(self, name, *_):
            self.playlist = TrackMatchingSession.Playlist()
            return self.playlist

    class Playlist:
        id = "pl-1"

        def __init__(self):
            self.added: list[str] = []

        def tracks(self, *a, **kw):
            return []

        def add(self, batch, *a, **kw):
            self.added.extend(str(t) for t in batch)
            return batch

    def search(self, query, *a, **kw):
        # Return a single hit whose id echoes the queried track name.
        class _Hit:
            id = query

        return {"tracks": [_Hit()], "albums": [], "artists": [], "videos": []}


async def test_one_failed_match_still_resolves_the_others(tmp_path):
    from tidal_sync.engine.importer import import_tracks_category_async

    csv = tmp_path / "Songs.csv"
    csv.write_text(
        "track_name,artist_name,album_name,isrc,tidal_id\n"
        "Keep1,Artist,Album,ISRC1,1\n"
        "Drop,Artist,Album,ISRC2,2\n"
        "Keep2,Artist,Album,ISRC3,3\n",
        encoding="utf-8",
    )

    real_resolve = None

    import tidal_sync.engine.importer as imp

    async def failing_resolve(session, track_name, artist_name, tidal_id=None, isrc=None):
        if track_name == "Drop":
            raise RuntimeError("network exhausted")
        return await real_resolve(
            session,
            track_name=track_name,
            artist_name=artist_name,
            tidal_id=tidal_id,
            isrc=isrc,
        )

    real_resolve = imp.resolve_track_to_id
    imp.resolve_track_to_id = failing_resolve
    try:
        session = TrackMatchingSession("Drop")
        stats = ImportStats()
        await import_tracks_category_async(session, csv, stats, playlist_name="Songs")
    finally:
        imp.resolve_track_to_id = real_resolve

    # The two healthy tracks must still reach the add queue.
    assert sorted(session.user.playlist.added) == ["1", "3"], session.user.playlist.added
    assert stats.failed == 1, "the dropped track is counted as a failure"
    assert stats.added == 2, "the surviving tracks are recorded as added"
