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
Filter-list source parsers.

Three parsers, one per format, each taking raw bytes and returning a
uniform ``list[tuple[str, str]]`` of ``(tidal_id, name)`` pairs. The
shape is the only contract the rest of the engine sees, so the dispatch
sits here: a format hint selects the parser, and anything else is a
``FormatError`` raised at parse time.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import orjson

from ..domain.models import ArtistRow
from .parser import extract_tidal_id, parse_csv


class FormatError(Exception):
    """Raised when a filter list cannot be parsed.

    The txt path attaches the offending line number; json and dispatch
    failures do not need one.
    """


def _parse_txt(data: bytes) -> list[tuple[str, str]]:
    """Decodes bytes line by line, skipping blanks and ``#`` comments."""
    text = data.decode("utf-8")
    pairs: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tidal_id = extract_tidal_id(stripped)
        except ValueError as exc:
            raise FormatError(f"line {line_number}: {exc}") from exc
        pairs.append((tidal_id, ""))
    return pairs


def _coerce_artist_row(row: ArtistRow) -> tuple[str, str] | None:
    """Maps an ``ArtistRow`` to a pair, dropping rows with no id."""
    tidal_id = row.tidal_id
    if tidal_id is None or tidal_id == "":
        return None
    return (tidal_id, row.artist_name)


def _parse_csv(data: bytes) -> list[tuple[str, str]]:
    """Writes bytes to a temp file and reuses ``parse_csv`` + ``ArtistRow``."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        rows: Iterable[ArtistRow] = parse_csv(tmp_path, ArtistRow)
    finally:
        tmp_path.unlink(missing_ok=True)

    pairs: list[tuple[str, str]] = []
    for row in rows:
        coerced = _coerce_artist_row(row)
        if coerced is not None:
            pairs.append(coerced)
    return pairs


def _coerce_json_entry(entry: Any) -> tuple[str, str]:
    """Reduces a JSON list element to ``(tidal_id, name)``.

    String entries become ``(id, "")``; dict entries must carry a string
    ``tidal_id`` and an optional ``artist_name``. Anything else is a
    ``FormatError``: ids are strings, and silent coercion would let
    upstream code accept shapes the rest of the engine does not expect.
    """
    if isinstance(entry, str):
        return (entry, "")

    if not isinstance(entry, dict):
        raise FormatError(f"json entry must be a string or object, got {type(entry).__name__}")

    raw_id = entry.get("tidal_id")
    if not isinstance(raw_id, str):
        raise FormatError("json object entry requires a string 'tidal_id'")

    name_value = entry.get("artist_name", "")
    if name_value is None:
        name_value = ""
    if not isinstance(name_value, str):
        raise FormatError("json object entry 'artist_name' must be a string when present")

    return (raw_id, name_value)


def _parse_json(data: bytes) -> list[tuple[str, str]]:
    """Decodes a JSON list of ids or id-bearing objects.

    A top-level dict or scalar parses with orjson but is not a list of
    references, so it is rejected here rather than silently coerced.
    orjson decode failures are wrapped as ``FormatError`` so the
    module's contract ("FormatError raised at parse time") holds for
    every input shape, including malformed bytes.
    """
    try:
        decoded = orjson.loads(data)
    except orjson.JSONDecodeError as exc:
        raise FormatError(f"json decode failed: {exc}") from exc
    if not isinstance(decoded, list):
        kind = type(decoded).__name__
        raise FormatError(f"json filter list must be a top-level array, got {kind}")
    return [_coerce_json_entry(entry) for entry in decoded]


def parse_filter_list(data: bytes, fmt: str) -> list[tuple[str, str]]:
    """Dispatches to the parser for ``fmt`` and returns ``(id, name)`` pairs."""
    normalised = fmt.lower()
    if normalised == "txt":
        return _parse_txt(data)
    if normalised == "csv":
        return _parse_csv(data)
    if normalised == "json":
        return _parse_json(data)
    raise FormatError(f"unsupported filter-list format: {fmt!r}")
