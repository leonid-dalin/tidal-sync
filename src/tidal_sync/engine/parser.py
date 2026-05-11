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
from pathlib import Path
from typing import TypeVar, Any
from pydantic import BaseModel, ValidationError
from loguru import logger

T = TypeVar('T', bound=BaseModel)


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
        except ValidationError as e:
            logger.error("CSV Validation Error", file=file_path.name, error=str(e))
        except Exception as e:
            logger.warning("Unexpected error parsing row", file=file_path.name, error=str(e))

    return items