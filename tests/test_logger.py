"""Audit logging must survive the run and redact credentials.

The audit trail cannot be trusted if a Path or set in `extra` silently drops
the record, or if a Bearer token reaches the file. These tests pin both.
"""

from pathlib import Path

from loguru import logger

from tidal_sync.infrastructure.logger import (
    audit_log_path,
    redact,
    setup_audit_logging,
    setup_global_logging,
    stop_audit_logging,
)


def test_non_serialisable_extras_do_not_lose_the_record(tmp_path):
    setup_global_logging()
    handler_id = setup_audit_logging(tmp_path)
    logger.bind(audit=True).info("Item Added", path=Path("/x/y"), ids={1, 2})
    path = audit_log_path(handler_id)
    stop_audit_logging()

    text = path.read_text(encoding="utf-8")
    assert "Item Added" in text, "the record itself must survive"


def test_tokens_are_redacted_from_extras():
    assert "secret" not in redact("Bearer secret-token-value")
    assert "secret" not in redact("access_token=secret123")


def test_nested_extras_are_redacted():
    assert "secret" not in redact({"token": "Bearer secret-token-value"})


def test_two_runs_in_the_same_second_do_not_share_a_log_file(tmp_path, monkeypatch):
    """F27: two runs in the same tick must not merge into one file.

    The clock is frozen so both calls get an identical timestamp. The fix
    derives uniqueness from a random suffix (not the wall clock), so the two
    paths still differ. Against the old implementation this fails: with a
    frozen clock and no suffix the two names are identical.
    """
    from datetime import datetime

    from tidal_sync.infrastructure import logger as logger_module

    fixed = datetime(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr(logger_module, "datetime", type("_FrozenDt", (datetime,), {}))
    monkeypatch.setattr(logger_module.datetime, "now", classmethod(lambda cls: fixed))

    first = setup_audit_logging(tmp_path)
    first_path = audit_log_path(first)
    stop_audit_logging()
    second = setup_audit_logging(tmp_path)
    second_path = audit_log_path(second)
    stop_audit_logging()

    assert first_path != second_path


def test_stop_audit_logging_leaves_the_console_sink_installed(tmp_path):
    setup_global_logging()
    handler_id = setup_audit_logging(tmp_path)

    stop_audit_logging()

    assert handler_id not in [hid for hid in _AUDIT_HANDLERS_ids()]
    assert logger._core.handlers, "the console sink must survive"


def _AUDIT_HANDLERS_ids():
    from tidal_sync.infrastructure.logger import _AUDIT_HANDLERS

    return [hid for hid, _ in _AUDIT_HANDLERS]


def test_audit_log_is_created_for_each_command(tmp_path):
    setup_global_logging()

    paths = []
    for name in ("import", "export", "clear"):
        handler_id = setup_audit_logging(tmp_path)
        logger.bind(audit=True).info("Job Started", command=name)
        path = audit_log_path(handler_id)
        stop_audit_logging()
        assert path is not None, f"{name} must have an audit file"
        assert path.exists()
        paths.append(path)

    assert len(set(paths)) == 3, "each command writes to its own file"

    for path in paths:
        assert path.read_text(encoding="utf-8").strip(), f"{path.name} is empty"


def test_audit_records_are_valid_jsonl(tmp_path):
    import json

    setup_global_logging()
    handler_id = setup_audit_logging(tmp_path)
    logger.bind(audit=True).info("Item Added", type="Track", id="2124179")
    logger.bind(audit=True).error("Dropped Track", name="{Remix} [feat. X]")
    path = audit_log_path(handler_id)
    stop_audit_logging()

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(lines) == 2
    for line in lines:
        record = json.loads(line)
        assert "timestamp" in record
        assert "level" in record
        assert "message" in record
    assert "{Remix} [feat. X]" in lines[1]
