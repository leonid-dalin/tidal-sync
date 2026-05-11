import csv
import io
from pathlib import Path
from typing import TypeVar, Any
from pydantic import BaseModel, ValidationError
from loguru import logger

T = TypeVar('T', bound=BaseModel)


def _clean_row(row: dict[Any, Any]) -> dict[str, Any]:
    """
    Sanitises messy CSV exports by stripping hidden characters,
    stray whitespace, and null bytes from both keys and values.
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
    Reads and validates a CSV file into Pydantic models.

    Args:
        file_path (Path): The location of the CSV file.
        model_class (type[T]): The Pydantic model to validate the rows against.

    Returns:
        list[T]: A list of validated row objects. Malformed rows are skipped and logged.
    """
    items: list[T] = []
    seen_hashes: set[int] = set()

    # Fallback encoding strategy: Try UTF-8 (with BOM strip) first.
    # If it fails, fallback to cp1252 (Windows) and latin-1.
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

            row_hash = hash(model.model_dump_json())
            if row_hash not in seen_hashes:
                seen_hashes.add(row_hash)
                items.append(model)
            else:
                track_name = getattr(model, 'track_name', getattr(model, 'album_name', 'Unknown'))
                logger.debug("Skipped duplicate row in CSV", file=file_path.name, item=track_name)

        except ValidationError as e:
            logger.error("CSV Validation Error", file=file_path.name, error=str(e))
        except Exception as e:
            logger.warning("Unexpected error parsing row", file=file_path.name, error=str(e))

    return items