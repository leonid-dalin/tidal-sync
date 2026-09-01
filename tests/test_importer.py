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
