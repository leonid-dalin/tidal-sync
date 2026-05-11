import csv
from pathlib import Path
from typing import TypeVar
from pydantic import BaseModel, ValidationError
from loguru import logger

T = TypeVar('T', bound=BaseModel)

def parse_csv(file_path: Path, model_class: type[T]) -> list[T]:
    """
    Reads and validates a CSV file into Pydantic models.

    Args:
        file_path (Path): The location of the CSV file.
        model_class (type[T]): The Pydantic model to validate the rows against.

    Returns:
        list[T]: A list of validated row objects. Malformed rows are skipped and logged.
    """
    items = []
    # We use 'utf-8-sig' to safely strip the Byte Order Mark (BOM) often injected by Windows/Excel exports
    with open(file_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row.pop(None, None)
            try:
                items.append(model_class(**row))
            except ValidationError as e:
                logger.error("CSV Validation Error", file=file_path.name, error=str(e))
    return items