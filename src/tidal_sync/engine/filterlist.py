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

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import orjson

from ..domain.exceptions import BackupFileError
from ..domain.models import ArtistRow
from .parser import extract_tidal_id, parse_csv_text

SUPPORTED_FORMATS: tuple[str, ...] = ("txt", "csv", "json")


class FormatError(Exception):
    """Raised when a filter list cannot be parsed.

    The txt path attaches the offending line number; json and dispatch
    failures do not need one.
    """


def detect_format(source: str) -> str:
    """Read the filter-list format off a URL or path extension.

    The extension is taken from the URL path, so a query string or a
    fragment does not become part of it. This is the only extension
    resolver; two divergent copies is how the query-string case was
    missed in the first place.
    """
    path = urlparse(source).path if "://" in source else source
    suffix = PurePosixPath(path).suffix.lstrip(".").lower()
    if suffix not in SUPPORTED_FORMATS:
        raise FormatError(
            f"unsupported filter-list format: {suffix or source!r}; "
            f"expected one of {', '.join(SUPPORTED_FORMATS)}"
        )
    return suffix


def _validated_id(raw: str, where: str) -> str:
    """Resolve one reference to a bare Tidal id, or raise FormatError.

    Every format runs through here. The txt parser always did; csv and
    json did not, which let an arbitrary string reach the network layer,
    where unblock_artists interpolates an id into the request path.
    """
    try:
        return extract_tidal_id(raw)
    except ValueError as exc:
        raise FormatError(f"{where}: {exc}") from exc


def _parse_txt(data: bytes) -> list[tuple[str, str]]:
    """Decode lines, skipping blanks and ``#`` comments.

    utf-8-sig, not utf-8: a leading BOM is invisible to the operator and
    would otherwise fail line 1 of any list saved by a Windows editor.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FormatError(f"source is not valid UTF-8: {exc}") from exc
    pairs: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pairs.append((_validated_id(stripped, f"line {line_number}"), ""))
    return pairs


def _parse_csv(data: bytes) -> list[tuple[str, str]]:
    """Validate CSV bytes through the shared ArtistRow reader."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FormatError(f"source is not valid UTF-8: {exc}") from exc
    try:
        rows = parse_csv_text(text, ArtistRow, "filter list")
    except (BackupFileError, ValueError) as exc:
        raise FormatError(str(exc)) from exc
    return [
        (_validated_id(row.tidal_id, f"row {index}"), row.artist_name)
        for index, row in enumerate(rows, start=1)
        if row.tidal_id
    ]


def _coerce_json_entry(entry: Any, index: int) -> tuple[str, str]:
    """Reduces a JSON list element to ``(tidal_id, name)``.

    String entries become ``(id, "")``; dict entries must carry a string
    ``tidal_id`` and an optional ``artist_name``. Anything else is a
    ``FormatError``: ids are strings, and silent coercion would let
    upstream code accept shapes the rest of the engine does not expect.

    The returned id is run through ``_validated_id`` so an arbitrary
    string in a list element cannot reach the network layer, where
    ``unblock_artists`` interpolates it into a request path.
    """
    if isinstance(entry, str):
        return (_validated_id(entry, f"entry {index}"), "")

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

    return (_validated_id(raw_id, f"entry {index}"), name_value)


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
        raise FormatError(
            f"json filter list must be a top-level array, got {type(decoded).__name__}"
        )
    return [_coerce_json_entry(entry, index) for index, entry in enumerate(decoded, start=1)]


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


__all__ = ["FormatError", "SUPPORTED_FORMATS", "detect_format", "parse_filter_list"]
