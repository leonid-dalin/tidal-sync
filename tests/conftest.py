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


@pytest.fixture
def gate():
    """A fresh global gate, installed and restored.

    GLOBAL_GATE is a module-level singleton: a test that triggers the 1800s
    abuse lock would otherwise leave every later test in the process sleeping
    at its pre-flight check.
    """
    from tidal_sync.engine import network

    original = network.GLOBAL_GATE
    network.GLOBAL_GATE = network.GlobalTidalGate()
    try:
        yield network.GLOBAL_GATE
    finally:
        network.GLOBAL_GATE = original
