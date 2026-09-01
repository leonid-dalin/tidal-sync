"""Per-file failure isolation during a directory import.

A backup directory can hold hundreds of CSVs. One undecodable file must
not cost the user the rest of the run, nor the summary that tells them
what happened.
"""

import pytest

from tidal_sync.domain.models import TrackRow
from tidal_sync.engine.importer import import_collection_from_disk
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
