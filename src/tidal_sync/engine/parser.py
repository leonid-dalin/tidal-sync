"""
CSV parsing and data sanitisation module.

Provides robust ingestion of user-exported library files. It handles
various text encodings, strips hidden control characters (like Byte
Order Marks and null bytes), and validates rows against strict Pydantic
schemas to ensure metadata integrity before it reaches the synchronisation
engine.
"""

import re
import csv
import io
from pathlib import Path
from typing import TypeVar, Any
from pydantic import BaseModel, ValidationError
from loguru import logger

T = TypeVar('T', bound=BaseModel)


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


def sanitize_filename(name: str) -> str:
    """Removes illegal OS characters to ensure safe cross-platform file saving."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()


def write_albums_csv_sync(file_path: Path, albums: list[Any]) -> None:
    """Synchronous I/O bound CSV writer for albums."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, mode='w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(["album_name", "artist_name", "tidal_id"])

        for album in albums:
            artist_name = getattr(getattr(album, 'artist', None), 'name', 'Unknown')
            writer.writerow([
                getattr(album, 'name', getattr(album, 'title', 'Unknown')),
                artist_name,
                str(getattr(album, 'id', ''))
            ])


def write_artists_csv_sync(file_path: Path, artists: list[Any]) -> None:
    """Synchronous I/O bound CSV writer for artists."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, mode='w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(["artist_name", "tidal_id"])

        for artist in artists:
            writer.writerow([
                getattr(artist, 'name', getattr(artist, 'title', 'Unknown')),
                str(getattr(artist, 'id', ''))
            ])

            
def write_tracks_csv_sync(file_path: Path, tracks: Sequence[Any]) -> None:
    """
    Synchronous I/O bound CSV writer.
    Designed to be offloaded to a thread pool to avoid blocking the async event loop.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, mode='w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(["track_name", "artist_name", "album_name", "isrc", "tidal_id"])

        for track in tracks:
            # Safely chain getattr to prevent crashes if metadata is missing/malformed
            artist_name = getattr(getattr(track, 'artist', None), 'name', 'Unknown')
            album_name = getattr(getattr(track, 'album', None), 'name', 'Unknown')

            writer.writerow([
                getattr(track, 'name', 'Unknown'),
                artist_name,
                album_name,
                getattr(track, 'isrc', ''),
                str(getattr(track, 'id', ''))
            ])


def _clean_row(row: dict[Any, Any]) -> dict[str, Any]:
    """
    Sanitises raw CSV exports to prevent database mismatch errors.

    Music metadata exports often contain hidden Byte Order Marks (BOM),
    null bytes (\\x00), and trailing whitespace. This function strips
    those artefacts from both column headers and values.

    Args:
        row (dict[Any, Any]): A single parsed row from the CSV DictReader.

    Returns:
        dict[str, Any]: A cleaned dictionary safe for Pydantic validation.
    """
    cleaned = {}
    for k, v in row.items():
        if k is not None:  # Ignore stray un-headered columns (e.g., trailing commas)
            clean_key = str(k).strip()
            clean_val = str(v).strip() if isinstance(v, str) else v

            if isinstance(clean_val, str):
                clean_val = clean_val.replace('\x00', '')

            cleaned[clean_key] = clean_val
    return cleaned


def parse_csv(file_path: Path, model_class: type[T]) -> list[T]:
    """
    Reads, decodes, and validates a CSV file into strongly typed objects.

    Attempts to decode the file using UTF-8-SIG to strip Windows artefacts.
    If that fails, it falls back to CP1252 and Latin-1. Rows that fail
    schema validation are dropped and logged, preventing broken metadata
    from halting the entire synchronisation queue.

    Args:
        file_path (Path): The absolute or relative path to the CSV file.
        model_class (type[T]): The Pydantic model representing the expected schema.

    Returns:
        list[T]: A list of validated model instances.
    """
    items: list[T] = []

    encodings = ['utf-8-sig', 'cp1252', 'latin-1']
    content = ""

    for encoding in encodings:
        try:
            with open(file_path, mode='r', encoding=encoding) as f:
                content = f.read()
                break
        except UnicodeDecodeError:
            if encoding == encodings[-1]:
                logger.error("Failed to decode CSV", file=file_path.name, error="Unknown encoding")
                return []
            continue

    reader = csv.DictReader(io.StringIO(content))

    for row in reader:
        try:
            cleaned_row = _clean_row(row)
            model = model_class(**cleaned_row)
            items.append(model)
        except ValidationError as _:
            logger.debug("Skipped malformed/empty CSV row", file=file_path.name)
        except Exception as e:
            logger.warning("Unexpected error parsing row", file=file_path.name, error=str(e))

    return items