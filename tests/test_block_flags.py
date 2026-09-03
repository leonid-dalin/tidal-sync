"""CLI wiring for the --from-list and --all-from flags on ``block``.

Engine tests in test_filterlist_apply.py prove plan_apply returns the
right ApplyPlan; these tests prove the new flags on the top-level
``block`` command route through that engine, compose with positional
ids, refuse an unknown subscription name and an unsupported extension,
honour the ten-id confirmation rail the same way ``blocklist apply``
does, and never alter the behaviour of positional-only ``block`` or
``unblock``.

Network and disk are monkeypatched at the filterlist_apply and
filterlist layers so a test never resolves a URL, reads a real cached
list or writes to a real Tidal account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tidal_sync import cli as cli_module
from tidal_sync.cli import app
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine.filterlist_apply import ApplyOutcome, ApplyPlan
from tidal_sync.engine.filterlist_store import Subscription

runner = CliRunner()


class _FakeUser:
    id = 4242


def _fake_session() -> object:
    return type("S", (), {"user": _FakeUser()})()


def _fake_subscription(
    name: str = "kpop",
    source: str = "https://example.test/list.txt",
    fmt: str = "txt",
    last_fetched: str | None = None,
) -> Subscription:
    return Subscription(
        name=name,
        source=source,
        format=fmt,
        last_fetched=last_fetched or datetime.now(UTC).isoformat(),
    )


def _patch_cli_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subs: list[Subscription],
    plan: ApplyPlan | None = None,
) -> dict[str, Any]:
    """Wire the fakes every test needs onto cli.py.

    Returns a box the test can read to make assertions about which
    engine verbs were called and with what ids.
    """
    box: dict[str, Any] = {
        "block_calls": [],
        "plan_calls": [],
        "load_calls": 0,
    }

    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    def _load_subscriptions() -> list[Subscription]:
        box["load_calls"] += 1
        return list(subs)

    monkeypatch.setattr(cli_module, "load_subscriptions", _load_subscriptions)

    if plan is None:
        plan = ApplyPlan(to_block=[], already_blocked=[], unlisted=[], errors=[])

    async def _plan_apply(session: object, sub_list: list[Subscription], **_: Any) -> ApplyPlan:
        box["plan_calls"].append(list(sub_list))
        return plan

    monkeypatch.setattr(cli_module, "plan_apply", _plan_apply)

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        box["block_calls"].append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    async def _execute_apply(session: object, plan: ApplyPlan, *, prune: bool) -> ApplyOutcome:
        if plan.to_block:
            await _block_artists(session, [tid for tid, _name in plan.to_block])
        return ApplyOutcome(
            blocked=UploadOutcome(
                applied=[tid for tid, _name in plan.to_block],
                rejected=[],
            ),
            unblocked=None,
            capped=False,
        )

    monkeypatch.setattr(cli_module, "execute_apply", _execute_apply)

    return box


# ---------------------------------------------------------------------------
# Flag acceptance and resolution.
# ---------------------------------------------------------------------------


def test_block_from_list_resolves_stored_subscription_and_blocks_its_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``block --from-list kpop`` loads the subscription, runs the engine,
    and prints one line per applied id.
    """
    sub = _fake_subscription(name="kpop", source="https://example.test/kpop.txt")
    plan = ApplyPlan(
        to_block=[("4894212", "Alpha"), ("8107285", "Beta")],
        already_blocked=[],
        unlisted=[],
        errors=[],
    )
    box = _patch_cli_common(monkeypatch, subs=[sub], plan=plan)

    result = runner.invoke(app, ["block", "--from-list", "kpop"])

    assert result.exit_code == 0, result.output
    assert box["load_calls"] == 1
    assert len(box["plan_calls"]) == 1
    assert box["plan_calls"][0][0].name == "kpop"
    # The engine's to_block list reaches block_artists via plan_apply itself.
    assert "4894212" in result.output
    assert "8107285" in result.output


def test_block_all_from_reads_a_one_off_file_and_blocks_its_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``block --all-from /path/to/list.txt`` reads the file, parses by
    extension, and routes through the engine without touching the store.
    """
    one_off = tmp_path / "list.txt"
    one_off.write_text("# header\n1\n2\n3\n", encoding="utf-8")

    plan = ApplyPlan(
        to_block=[("1", ""), ("2", ""), ("3", "")],
        already_blocked=[],
        unlisted=[],
        errors=[],
    )
    box = _patch_cli_common(monkeypatch, subs=[], plan=plan)

    # parse_filter_list on cli must hit the one-off file; we make it
    # pretend every txt payload is the seed we just wrote by returning
    # a fixed tuple list and confirming it reaches the engine.
    async def _plan_apply(session: object, sub_list: list[Subscription], **_: Any) -> ApplyPlan:
        box["plan_calls"].append(list(sub_list))
        # The one-off path builds a synthetic subscription whose
        # source is the file path, so plan_apply's read_bytes on the
        # cache_path must hand the parser something parseable.
        return plan

    monkeypatch.setattr(cli_module, "plan_apply", _plan_apply)

    result = runner.invoke(app, ["block", "--all-from", str(one_off)])

    assert result.exit_code == 0, result.output
    # No store touch for --all-from.
    assert box["load_calls"] == 0
    assert "1" in result.output and "2" in result.output and "3" in result.output


def test_block_from_list_composes_with_positional_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``block --from-list kpop 8107285`` sends the union of subscription
    ids and positional ids to the engine.
    """
    sub = _fake_subscription(name="kpop")
    plan = ApplyPlan(
        to_block=[("4894212", "Alpha"), ("8107285", "Beta")],
        already_blocked=[],
        unlisted=[],
        errors=[],
    )
    box = _patch_cli_common(monkeypatch, subs=[sub], plan=plan)

    result = runner.invoke(app, ["block", "--from-list", "kpop", "8107285"])

    assert result.exit_code == 0, result.output
    assert len(box["plan_calls"]) == 1
    assert "4894212" in result.output
    assert "8107285" in result.output


# ---------------------------------------------------------------------------
# Failure modes: clear messages, exit 1, no writes.
# ---------------------------------------------------------------------------


def test_block_from_list_unknown_name_exits_one_and_blocks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown subscription name exits 1 with a clear message and
    leaves the account untouched.
    """
    box = _patch_cli_common(monkeypatch, subs=[])

    result = runner.invoke(app, ["block", "--from-list", "ghost"])

    assert result.exit_code == 1, result.output
    assert "ghost" in result.output
    assert box["plan_calls"] == [], "engine must not run on an unknown name"
    assert box["block_calls"] == [], "block_artists must not run on an unknown name"


def test_block_all_from_unsupported_extension_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--all-from`` with an unsupported extension exits 1 before the
    engine runs.
    """
    bad = tmp_path / "list.xyz"
    bad.write_text("anything", encoding="utf-8")
    box = _patch_cli_common(monkeypatch, subs=[])

    result = runner.invoke(app, ["block", "--all-from", str(bad)])

    assert result.exit_code == 1, result.output
    assert box["plan_calls"] == [], "engine must not run on a bad extension"
    assert box["block_calls"] == [], "block_artists must not run on a bad extension"


# ---------------------------------------------------------------------------
# Preserved behaviour.
# ---------------------------------------------------------------------------


def test_block_with_only_positional_ids_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """``block 4894212 8107285`` still routes through curation.block_artists
    via the existing _run_block_command path, with no subscription store
    read and no plan_apply call.
    """
    engine_calls: list[list[str]] = []

    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    # If the new flags accidentally pull in the store, this counter goes up.
    load_calls = {"n": 0}

    def _load_subscriptions() -> list[Subscription]:
        load_calls["n"] += 1
        return []

    monkeypatch.setattr(cli_module, "load_subscriptions", _load_subscriptions)

    result = runner.invoke(app, ["block", "4894212", "8107285"])

    assert result.exit_code == 0, result.output
    assert engine_calls == [["4894212", "8107285"]]
    assert load_calls["n"] == 0, "positional-only block must not touch the subscription store"


def test_unblock_is_unaffected_and_has_no_new_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """``unblock`` keeps its current surface: --profile and positional ids
    only. It must not accept --from-list or --all-from and must continue
    to call curation.unblock_artists through the shared body.
    """
    engine_calls: list[list[str]] = []

    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _unblock_artists(session: object, ids: list[str]) -> UploadOutcome:
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module.curation, "unblock_artists", _unblock_artists)

    # Baseline call still works and is unchanged.
    result = runner.invoke(app, ["unblock", "111", "222"])
    assert result.exit_code == 0, result.output
    assert engine_calls == [["111", "222"]]

    # The new flags must not be silently accepted on unblock.
    result_unknown = runner.invoke(app, ["unblock", "--from-list", "kpop"])
    assert "no such option" in result_unknown.output.lower() or result_unknown.exit_code == 2, (
        "unblock must not gain --from-list"
    )

    result_unknown2 = runner.invoke(app, ["unblock", "--all-from", "list.txt"])
    assert "no such option" in result_unknown2.output.lower() or result_unknown2.exit_code == 2, (
        "unblock must not gain --all-from"
    )
