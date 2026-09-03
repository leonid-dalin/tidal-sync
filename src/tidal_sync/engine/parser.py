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
CSV parsing and data sanitisation module.

Provides robust ingestion of user-exported library files. It handles
various text encodings, strips hidden control characters (like Byte
Order Marks and null bytes), and validates rows against strict Pydantic
schemas to ensure metadata integrity before it reaches the synchronisation
engine.
"""

import csv
import io
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, ValidationError

from ..domain.exceptions import BackupFileError


def normalises_playlist_id(raw_id: str | Any) -> str:
    """
    Normalises Tidal IDs to reconcile V1 and V2 API differences.
    Strips URN prefixes (e.g., 'trn:playlist:') and ensures lowercase formatting.
    """
    if not raw_id:
        return ""

    clean_id = str(raw_id).strip().lower()
    if clean_id.startswith("trn:playlist:"):
        clean_id = clean_id.replace("trn:playlist:", "")

    return clean_id


_TIDAL_ID_FROM_URL = re.compile(r"/(?:track|artist|album)/(\d+)")


def extract_tidal_id(raw: str) -> str:
    """
    Resolves a bare id or a Tidal share URL into the bare id.

    Accepts the bare numeric id, a browse.tidal.com or listen.tidal.com URL
    of the form ``/<kind>/<id>`` with an optional query string and trailing
    slash. Raises ValueError when no id can be parsed, so the CLI surfaces it
    as a usage error rather than silently dropping the reference.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty reference")

    match = _TIDAL_ID_FROM_URL.search(text)
    if match:
        return match.group(1)

    # Bare ids are digit strings; any other shape is unparseable.
    if text.isdigit():
        return text

    raise ValueError(f"cannot extract Tidal id from {raw!r}")


_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_MAX_FILENAME_BYTES = 200  # leaves room for the "-N.csv" suffix


def sanitize_filename(name: str) -> str:
    """Removes illegal OS characters and clamps the result to a safe filename."""
    # Separators are replaced, not stripped, so a leading .. cannot survive
    # as a traversal component once the slashes around it are gone.
    pattern = r"[\\<>:/|?*\x00-\x1f]"
    cleaned = re.sub(pattern, "_", name).strip()
    cleaned = re.sub(r"^\.+|\.+$", "", cleaned)
    if not cleaned:
        return "untitled"

    if cleaned.lower() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"

    encoded = cleaned.encode("utf-8")
    if len(encoded) > _MAX_FILENAME_BYTES:
        cleaned = encoded[:_MAX_FILENAME_BYTES].decode("utf-8", errors="ignore").rstrip()

    return cleaned or "untitled"


class UniquePathAllocator:
    """Hands out non-colliding .csv paths within an export run.

    Callers run concurrently, so this removes the possibility of two tasks
    opening the same path for writing. Allocation happens on the event loop
    thread before work reaches a worker, so no lock is required.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def allocate(self, directory: Path, name: str) -> Path:
        base = sanitize_filename(name)
        candidate = directory / f"{base}.csv"
        key = str(candidate).casefold()

        suffix = 2
        while key in self._seen:
            candidate = directory / f"{base}-{suffix}.csv"
            key = str(candidate).casefold()
            suffix += 1

        self._seen.add(key)
        return candidate


def _atomic_write_csv(file_path: Path, write_rows) -> int:
    """Writes a CSV atomically and returns the number of data rows written.

    The file is built under a .part sibling and only moved into place once
    the content is fully written and flushed. A crash mid-write therefore
    leaves any previous backup untouched instead of a truncated one.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_name(file_path.name + ".part")

    try:
        with open(temp_path, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            rows = write_rows(writer)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, file_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return rows


def write_albums_csv_sync(file_path: Path, albums: list[Any]) -> int:
    """Synchronous I/O bound CSV writer for albums. Returns rows written."""

    def _rows(writer):
        writer.writerow(["album_name", "artist_name", "tidal_id"])
        for album in albums:
            artist_name = getattr(getattr(album, "artist", None), "name", "Unknown")
            writer.writerow(
                [
                    getattr(album, "name", getattr(album, "title", "Unknown")),
                    artist_name,
                    str(getattr(album, "id", "")),
                ]
            )
        return len(albums)

    return _atomic_write_csv(file_path, _rows)


def write_artists_csv_sync(file_path: Path, artists: list[Any]) -> int:
    """Synchronous I/O bound CSV writer for artists. Returns rows written."""

    def _rows(writer):
        writer.writerow(["artist_name", "tidal_id"])
        for artist in artists:
            writer.writerow(
                [
                    getattr(artist, "name", getattr(artist, "title", "Unknown")),
                    str(getattr(artist, "id", "")),
                ]
            )
        return len(artists)

    return _atomic_write_csv(file_path, _rows)


def write_tracks_csv_sync(file_path: Path, tracks: Sequence[Any]) -> int:
    """
    Synchronous I/O bound CSV writer. Returns rows written.

    Designed to be offloaded to a thread pool to avoid blocking the async
    event loop.
    """

    def _rows(writer):
        writer.writerow(["track_name", "artist_name", "album_name", "isrc", "tidal_id"])
        for track in tracks:
            artist_name = getattr(getattr(track, "artist", None), "name", "Unknown")
            album_name = getattr(getattr(track, "album", None), "name", "Unknown")

            writer.writerow(
                [
                    getattr(track, "name", "Unknown"),
                    artist_name,
                    album_name,
                    getattr(track, "isrc", ""),
                    str(getattr(track, "id", "")),
                ]
            )
        return len(tracks)

    return _atomic_write_csv(file_path, _rows)


def _summarise_validation_error(error: ValidationError) -> str:
    """Renders a Pydantic error as one compact, readable line."""
    parts = []
    for err in error.errors():
        field = ".".join(str(p) for p in err.get("loc", ())) or "<row>"
        parts.append(f"{field}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def _clean_row(row: dict[Any, Any]) -> dict[str, Any]:
    """
    Sanitises raw CSV exports and normalises header spellings.

    Music metadata exports often contain hidden Byte Order Marks (BOM),
    null bytes (\x00), and trailing whitespace. This function strips
    those artefacts from both column headers and values.

    Exports also arrive from Exportify, TuneMyMusic, and this tool itself,
    so headers vary in case and separator. Alongside the original key this
    exposes a lowercased, underscore-joined form so each model can
    declare one canonical snake_case alias per field.

    Args:
        row (dict[Any, Any]): A single parsed row from the CSV DictReader.

    Returns:
        dict[str, Any]: A cleaned dictionary safe for Pydantic validation.
    """
    cleaned: dict[str, Any] = {}
    for k, v in row.items():
        if k is not None:  # Ignore stray un-headered columns (e.g., trailing commas)
            clean_key = str(k).strip().lstrip("\ufeff")
            clean_val = str(v).strip() if isinstance(v, str) else v

            if isinstance(clean_val, str):
                clean_val = clean_val.replace("\x00", "")

            cleaned[clean_key] = clean_val

            normalised = clean_key.lower().replace(" ", "_")
            cleaned.setdefault(normalised, clean_val)
    return cleaned


def parse_csv_text[T: BaseModel](content: str, model_class: type[T], label: str) -> list[T]:
    """
    Validate already-decoded CSV text into typed rows.

    ``label`` names the source in any raised error. Split out of
    ``parse_csv`` so an in-memory body does not have to be written to a
    temporary file just to be read back.
    """
    if not content.strip():
        raise BackupFileError(f"{label}: file is empty")

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise BackupFileError(f"{label}: no CSV header row found")

    items: list[T] = []
    total = 0
    dropped = 0

    for row in reader:
        total += 1
        try:
            cleaned_row = _clean_row(row)
            model = model_class(**cleaned_row)
            items.append(model)
        except ValidationError as e:
            dropped += 1
            logger.warning(
                "Dropped CSV row (line {line}): {detail}",
                line=reader.line_num,
                detail=_summarise_validation_error(e),
                file=label,
            )
        except Exception as e:
            dropped += 1
            logger.warning(
                "Dropped CSV row (line {line}): unexpected error {error}",
                line=reader.line_num,
                error=str(e),
                file=label,
            )

    if dropped:
        logger.warning(
            "CSV import incomplete: dropped {dropped} of {total} rows from {file}",
            dropped=dropped,
            total=total,
            file=label,
        )

    if not items:
        raise BackupFileError(
            f"{label}: no valid rows for {model_class.__name__}; "
            "check the header names and the source export"
        )

    return items


def parse_csv[T: BaseModel](file_path: Path, model_class: type[T]) -> list[T]:
    """
    Reads, decodes, and validates a CSV file into strongly typed objects.

    Attempts to decode the file using UTF-8-SIG to strip Windows artefacts.
    If that fails, it falls back to CP1252 and Latin-1. Rows that fail
    schema validation are dropped and logged, preventing broken metadata
    from halting the entire synchronisation queue. If zero rows validate,
    it raises ValueError rather than returning an empty list.

    Args:
        file_path (Path): The absolute or relative path to the CSV file.
        model_class (type[T]): The Pydantic model representing the expected schema.

    Returns:
        list[T]: A list of validated model instances.
    """
    encodings = ["utf-8-sig", "cp1252", "latin-1"]
    content = ""

    for encoding in encodings:
        try:
            with open(file_path, encoding=encoding) as f:
                content = f.read()
                break
        except UnicodeDecodeError:
            if encoding == encodings[-1]:
                logger.error("Failed to decode CSV", file=file_path.name, error="Unknown encoding")
                raise BackupFileError(f"{file_path.name}: unknown encoding") from None
            continue

    # Every fallback encoding accepts nearly any byte sequence, so a corrupt
    # file decodes to garbage instead of raising.
    return parse_csv_text(content, model_class, file_path.name)
