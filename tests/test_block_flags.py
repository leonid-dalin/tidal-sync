"""Behaviour pins for the block command's list and rail paths.

The engine tests in test_filterlist_apply.py prove plan_apply returns the
right ApplyPlan. These tests prove the CLI wires that plan to writes through
the confirmation rail, on both the positional and the --from-list paths.

The curation verbs are faked; the session is a stub. Only the rail and the
write calls are under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tidal_sync import cli
from tidal_sync.cli import app
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine import filterlist_apply, filterlist_store

runner = CliRunner()


def test_from_list_declining_the_rail_blocks_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refused confirmation on block --from-list must issue no write.

    blocklist apply has this pinned; this path did not, and a mutation
    that deleted the rail outright left the whole suite green.
    """
    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path / "filter_lists")
    source = tmp_path / "many.txt"
    source.write_text("\n".join(str(2000 + i) for i in range(15)), encoding="utf-8")
    filterlist_store.add_subscription(
        filterlist_store.Subscription(name="many", source=str(source), format="txt")
    )

    blocked: list[list[str]] = []

    async def _block(session: object, ids: list[str]) -> UploadOutcome:
        blocked.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _named(session: object) -> list[tuple[str, str]]:
        return []

    monkeypatch.setattr(filterlist_apply, "block_artists", _block)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _named)
    monkeypatch.setattr(cli, "get_session", lambda profile: object())

    result = runner.invoke(app, ["block", "--from-list", "many"], input="WRONG\n")

    assert result.exit_code == 1
    assert blocked == [], "a refused confirmation must issue no block write"


def test_from_list_confirming_the_rail_blocks_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An accepted confirmation issues one write for the list ids."""
    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path / "filter_lists")
    source = tmp_path / "many.txt"
    source.write_text("\n".join(str(2000 + i) for i in range(15)), encoding="utf-8")
    filterlist_store.add_subscription(
        filterlist_store.Subscription(name="many", source=str(source), format="txt")
    )

    blocked: list[list[str]] = []

    async def _block(session: object, ids: list[str]) -> UploadOutcome:
        blocked.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _named(session: object) -> list[tuple[str, str]]:
        return []

    monkeypatch.setattr(filterlist_apply, "block_artists", _block)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _named)
    monkeypatch.setattr(cli, "get_session", lambda profile: object())

    result = runner.invoke(app, ["block", "--from-list", "many"], input="default\n")

    assert result.exit_code == 0
    assert len(blocked) == 1, f"expected one block call, got {len(blocked)}"
    assert len(blocked[0]) == 15
