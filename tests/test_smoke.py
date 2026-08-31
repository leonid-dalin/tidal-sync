"""The package must import. This guards the F821 class of defect."""

import importlib

MODULES = (
    "tidal_sync.cli",
    "tidal_sync.auth",
    "tidal_sync.engine.network",
    "tidal_sync.engine.exporter",
    "tidal_sync.engine.importer",
    "tidal_sync.engine.wiping",
    "tidal_sync.engine.folders",
    "tidal_sync.engine.parser",
    "tidal_sync.engine.workers",
    "tidal_sync.engine.bisection",
)


def test_public_modules_import():
    for module in MODULES:
        importlib.import_module(module)  # raises on failure
