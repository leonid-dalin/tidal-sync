"""CSV writes must be atomic and must report what they wrote.

A truncated backup that the exporter reports as success is worse than a
loud failure, since the user only finds out when they try to restore.
"""

import pytest

from tests.fakes import FakeAlbum, FakeArtist, FakeTrack
from tidal_sync.engine.parser import (
    write_albums_csv_sync,
    write_artists_csv_sync,
    write_tracks_csv_sync,
)


def test_write_tracks_returns_row_count(tmp_path):
    path = tmp_path / "Liked Songs.csv"
    rows = write_tracks_csv_sync(path, [FakeTrack(), FakeTrack(id=101)])
    assert rows == 2
    assert path.read_text(encoding="utf-8-sig").count("\n") == 3  # header + 2


def test_failed_write_leaves_previous_file_intact(tmp_path):
    """A mid-write failure must not destroy the backup already on disk."""
    path = tmp_path / "Liked Songs.csv"
    path.write_text("previous,good,backup\n", encoding="utf-8-sig")

    class ExplodingTrack:
        name = "ok"
        isrc = "x"
        id = 1
        artist = FakeArtist()

        @property
        def album(self):
            raise OSError("disk full")

    with pytest.raises(OSError):
        write_tracks_csv_sync(path, [ExplodingTrack()])

    assert path.read_text(encoding="utf-8-sig") == "previous,good,backup\n"


def test_no_temp_file_left_behind(tmp_path):
    path = tmp_path / "a.csv"
    write_tracks_csv_sync(path, [FakeTrack()])
    assert not path.with_name("a.csv.part").exists()


def test_write_albums_and_artists_return_counts(tmp_path):
    assert write_albums_csv_sync(tmp_path / "a.csv", [FakeAlbum()]) == 1
    assert write_artists_csv_sync(tmp_path / "b.csv", [FakeArtist()]) == 1
