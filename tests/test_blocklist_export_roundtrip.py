# tidal-sync: A high-performance tool for backing up and cloning Tidal libraries.
# Copyright (C) 2026 Leonid Dalin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: infoLeonid@protonMail.com

"""
Proves a blocked-artists CSV export written by this tool loads back as a
filter list.

The exporter writes Blocked Artists.csv from the same artist objects the
blocklist read returns, so the file the engine just produced is the most
likely first filter list a user subscribes to. Nothing else in PR 3 pins
that a file written by write_artists_csv_sync and read by parse_filter_list
round trips cleanly through the UTF-8 BOM and CSV quoting.
"""

from pathlib import Path

import pytest

from tidal_sync.domain.exceptions import BackupFileError
from tidal_sync.engine.filterlist import parse_filter_list
from tidal_sync.engine.parser import write_artists_csv_sync


class _Artist:
    """A minimal stand-in for the tidalapi artist objects the engine reads.

    The exporter reaches for ``artist.name`` and ``artist.id`` only, so any
    object with those two attributes round trips through
    ``write_artists_csv_sync``. tidalapi.Artist cannot be instantiated without
    a session, and the project's FakeArtist hard-codes ``id = 1``, which would
    make the multi-artist round trip ambiguous.
    """

    def __init__(self, artist_id: int, name: str) -> None:
        self.id = artist_id
        self.name = name


def _write_blocklist_csv(path: Path, artists: list[_Artist]) -> bytes:
    """Writes the blocked-artists CSV and returns the on-disk bytes."""
    write_artists_csv_sync(path, artists)
    return path.read_bytes()


def test_blocklist_csv_starts_with_utf8_bom(tmp_path: Path) -> None:
    """The exporter opens the file with utf-8-sig, so the BOM is present."""
    path = tmp_path / "Blocked Artists.csv"
    raw = _write_blocklist_csv(path, [_Artist(4894212, "Bad Bunny")])

    assert raw[:3] == b"\xef\xbb\xbf", f"Expected a UTF-8 BOM (ef bb bf), got {raw[:3].hex()}"


def test_blocklist_export_loads_back_as_filter_list(tmp_path: Path) -> None:
    """A blocked-artists export reads back as the same id and name pairs."""
    path = tmp_path / "Blocked Artists.csv"
    artists = [
        _Artist(4894212, "Bad Bunny"),
        _Artist(8107285, "Rosalia"),
    ]
    _write_blocklist_csv(path, artists)

    parsed = parse_filter_list(path.read_bytes(), "csv")

    assert parsed == [
        ("4894212", "Bad Bunny"),
        ("8107285", "Rosalia"),
    ]


def test_empty_blocklist_writes_header_only(tmp_path: Path) -> None:
    """An empty blocklist still writes a valid header that fails to parse.

    ``write_artists_csv_sync`` writes the BOM and header row but no data rows.
    ``parse_csv`` raises ``BackupFileError`` on a file with zero valid rows, so
    an empty blocklist cannot round trip. That is preserved behaviour: the
    exporter is not modified and the parser is not modified.
    """
    path = tmp_path / "Blocked Artists.csv"
    written = write_artists_csv_sync(path, [])

    assert written == 0
    text = path.read_text(encoding="utf-8-sig")
    assert text == "artist_name,tidal_id\n"
    assert path.read_bytes()[:3] == b"\xef\xbb\xbf"

    with pytest.raises(BackupFileError):
        parse_filter_list(path.read_bytes(), "csv")


def test_blocklist_export_handles_comma_in_artist_name(tmp_path: Path) -> None:
    """A name containing a comma is quoted by csv.writer and survives the trip."""
    path = tmp_path / "Blocked Artists.csv"
    artists = [
        _Artist(1234, "Earth, Wind & Fire"),
        _Artist(5678, "Plain"),
    ]
    _write_blocklist_csv(path, artists)

    parsed = parse_filter_list(path.read_bytes(), "csv")

    assert parsed == [
        ("1234", "Earth, Wind & Fire"),
        ("5678", "Plain"),
    ]
