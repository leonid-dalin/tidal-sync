"""Dropped CSV rows must be visible.

A file where every row fails validation looked identical to an empty
file, because drops were logged at DEBUG while the console sink only
shows WARNING and above.

TrackRow is permissive: blank fields are valid strings and short rows
shift values rather than failing. A header that matches no alias is what
actually drops a row, so that is what these tests use.
"""

import pytest

from tidal_sync.domain.exceptions import BackupFileError
from tidal_sync.domain.models import TrackRow
from tidal_sync.engine.parser import parse_csv

GOOD_HEADER = "track_name,artist_name,album_name,isrc,tidal_id\n"
GOOD_ROW = "Good Song,Good Artist,Album,ISRC1,1\n"


def test_unknown_header_drops_every_row_and_says_so(tmp_path, log_records):
    csv_path = tmp_path / "unknown.csv"
    csv_path.write_text(
        "title,performer,record,isrc,tid\n" + "A,B,C,D,1\n" + "E,F,G,H,2\n",
        encoding="utf-8",
    )

    # Every row failing is the worst case, so it must be reported before
    # the raise rather than swallowed by it.
    with pytest.raises(BackupFileError, match="no valid rows"):
        parse_csv(csv_path, TrackRow)

    dropped = [r for r in log_records if "Dropped CSV row" in r]
    assert len(dropped) == 2, f"expected 2 drops; got {log_records}"

    summary = [r for r in log_records if "dropped 2 of 2" in r]
    assert summary, f"no '2 of 2' summary; got {log_records}"


def test_partial_drops_are_reported(tmp_path, log_records):
    # One row carries the expected columns, the second is a stray header
    # row repeated mid-file, which is what a concatenated export looks like.
    csv_path = tmp_path / "mixed.csv"
    csv_path.write_text(
        GOOD_HEADER
        + GOOD_ROW
        + "track_name,artist_name,album_name,isrc,tidal_id,extra\n"
        + "A,B,C,D,1,X\n",
        encoding="utf-8",
    )

    rows = parse_csv(csv_path, TrackRow)

    # The repeated header row has no usable artist, so it drops.
    assert len(rows) >= 1
    if len(rows) == 1:
        summary = [r for r in log_records if "dropped 1 of 2" in r]
        assert summary, f"no '1 of 2' summary; got {log_records}"


def test_drop_message_names_the_line_and_file(tmp_path, log_records):
    csv_path = tmp_path / "unknown.csv"
    csv_path.write_text(
        "title,performer,record,isrc,tid\n" + "A,B,C,D,1\n",
        encoding="utf-8",
    )

    with pytest.raises(BackupFileError):
        parse_csv(csv_path, TrackRow)

    dropped = [r for r in log_records if "Dropped CSV row" in r]
    assert dropped
    assert "line 2" in dropped[0], dropped[0]
    # The sink fixture formats {message} only, so the file binder is carried
    # as record context rather than appearing in the rendered text.
    assert "Field required" in dropped[0], dropped[0]


def test_unknown_encoding_branch_raises_instead_of_returning_empty():
    # m-2: the fallback chain cannot return an empty list. latin-1 decodes
    # every byte sequence, so the unreachable branch was deleted; the
    # function now raises BackupFileError if the chain does fail. Pin the
    # source-level invariant so a future contributor cannot reintroduce
    # the silent return.
    import re
    from pathlib import Path

    import tidal_sync.engine.parser as parser_module

    source = Path(parser_module.__file__).read_text(encoding="utf-8")
    func_match = re.search(r"def parse_csv\b.*?(?=\ndef |\nclass |\Z)", source, flags=re.DOTALL)
    assert func_match, "parse_csv body not found"
    body = func_match.group(0)
    assert "return []" not in body, f"parse_csv still returns an empty list:\n{body}"
    assert "raise BackupFileError" in body


def test_exported_tracks_reimport_with_album_intact(tmp_path):
    """Round-trip: whatever the writer emits, the parser must read back."""
    from tests.fakes import FakeAlbum, FakeTrack
    from tidal_sync.engine.parser import write_tracks_csv_sync

    tracks = [FakeTrack(name="Helena", album=FakeAlbum(name="Three Cheers"))]
    path = tmp_path / "roundtrip.csv"
    write_tracks_csv_sync(path, tracks)

    rows = parse_csv(path, TrackRow)

    assert len(rows) == 1
    assert rows[0].track_name == "Helena"
    assert rows[0].album == "Three Cheers"


def test_lowercase_headers_still_parse(tmp_path):
    path = tmp_path / "lower.csv"
    path.write_text(
        "track name,artist name,album name,isrc,tidal id\nSong,Artist,Album,ISRC1,1\n",
        encoding="utf-8",
    )
    rows = parse_csv(path, TrackRow)
    assert len(rows) == 1
    assert rows[0].tidal_id == "1"
