"""Shared fixtures.

loguru sinks are process-global, so every test starts from a clean set.
"""

import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def _isolate_loguru():
    logger.remove()
    yield
    logger.remove()


@pytest.fixture
def log_records():
    """Captures loguru records in a list.

    caplog does not work here: loguru writes to its own sinks and never
    reaches the standard logging handlers that pytest taps.
    """
    records: list[str] = []
    handler = logger.add(records.append, level="DEBUG", format="{message}")
    yield records
    logger.remove(handler)
