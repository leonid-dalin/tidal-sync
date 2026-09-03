"""Behaviour pins for the block command's list and rail paths.

The engine tests in test_filterlist_apply.py prove plan_apply returns the
right ApplyPlan. These tests prove the CLI wires that plan to writes through
the confirmation rail, on both the positional and the --from-list paths.

The curation verbs are faked; the session is a stub. Only the rail and the
write calls are under test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
import pytest
from typer.testing import CliRunner

from tidal_sync import cli as cli_module
from tidal_sync.cli import app
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine import filterlist_apply, filterlist_store
from tidal_sync.engine.filterlist import detect_format
from tidal_sync.engine.filterlist_apply import ApplyOutcome, ApplyPlan
from tidal_sync.engine.filterlist_store import Subscription

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
    monkeypatch.setattr(cli_module, "get_session", lambda profile: object())

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
    monkeypatch.setattr(cli_module, "get_session", lambda profile: object())

    result = runner.invoke(app, ["block", "--from-list", "many"], input="default\n")

    assert result.exit_code == 0
    assert len(blocked) == 1, f"expected one block call, got {len(blocked)}"
    assert len(blocked[0]) == 15


def test_a_capped_list_blocks_no_positional_leftover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A capped run must abort before the leftover write, not after.

    Printing "aborting" once a write has already gone out is the same
    shape as the rail defect: a guard reported after the action.
    """
    from tidal_sync.engine import curation

    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path / "filter_lists")
    source = tmp_path / "huge.txt"
    source.write_text("\n".join(str(100000 + i) for i in range(5001)), encoding="utf-8")
    filterlist_store.add_subscription(
        filterlist_store.Subscription(name="huge", source=str(source), format="txt")
    )

    blocked: list[list[str]] = []

    async def _block(session: object, ids: list[str]) -> UploadOutcome:
        blocked.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _named(session: object) -> list[tuple[str, str]]:
        return []

    monkeypatch.setattr(filterlist_apply, "block_artists", _block)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _named)
    monkeypatch.setattr(curation, "block_artists", _block)
    monkeypatch.setattr(cli_module, "get_session", lambda profile: object())

    result = runner.invoke(app, ["block", "--from-list", "huge", "777", "--force"])

    assert result.exit_code == 1
    assert blocked == [], "a capped run must write nothing at all"
    assert "Blocked artist 777" not in result.output


# ---------------------------------------------------------------------------
# Helpers shared by the flag-coverage tests below.
# ---------------------------------------------------------------------------


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

    async def _execute_apply(
        session: object, plan: ApplyPlan, *, unblock_ids: list[str]
    ) -> ApplyOutcome:
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


# ---------------------------------------------------------------------------
# Task 13: bare ``block`` is a usage error.
#
# Background: ``block`` grew ``default_factory=list`` on its positional
# argument so ``tidal-sync block`` with no ids and no flags silently
# succeeded. The contract is that blocking nothing is a mistake, not a
# no-op: Typer's BadParameter turns the no-args invocation into a
# non-zero exit with a clear message.
# ---------------------------------------------------------------------------


def test_block_with_no_ids_and_no_flags_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tidal-sync block`` with nothing must exit non-zero and say so.

    The CLI must not call ``curation.block_artists`` (no ids, no flag,
    nothing to do) and must surface the missing input as a usage
    error. Typer prints its own ``Missing argument`` or our
    ``BadParameter`` text; either is acceptable, so the assertion
    only pins the exit code and that nothing was blocked.
    """
    engine_calls: list[list[str]] = []

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())
    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    result = runner.invoke(app, ["block"])

    assert result.exit_code != 0, result.output
    assert engine_calls == [], "block with no input must never call block_artists"


# ---------------------------------------------------------------------------
# Task 8: one canonical extension resolver, no tracebacks on ordinary inputs.
#
# Background: ``detect_format`` is implemented once in
# ``engine/filterlist`` and called by both CLI modules; an
# extension is read off the URL path only (a query string or fragment
# is not part of it). ``blocklist add`` wraps ``cache_path`` so an
# invalid subscription name becomes an exit-1 message rather than a
# raw ``ValueError`` traceback, and ``--all-from`` synthesises a
# single ``one-off`` subscription so repeated invocations do not
# accumulate cache files.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["txt", "csv", "json"])
def test_all_from_reports_cleanly_for_every_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, suffix: str
) -> None:
    """No format may reach the operator as a traceback."""
    bodies = {
        "txt": b"4894212\n",
        "csv": b"artist_name,tidal_id\nX,4894212\n",
        "json": b'["4894212"]',
    }
    source = tmp_path / f"list.{suffix}"
    source.write_bytes(bodies[suffix])
    _patch_cli_common(monkeypatch, subs=[])

    result = runner.invoke(app, ["block", "--all-from", str(source)])

    assert not isinstance(result.exception, (ValueError, orjson.JSONDecodeError)), result.output


def test_all_from_a_file_whose_name_has_a_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A perfectly ordinary filename must not raise ValueError."""
    source = tmp_path / "my list.txt"
    source.write_text("4894212\n", encoding="utf-8")
    _patch_cli_common(monkeypatch, subs=[])

    result = runner.invoke(app, ["block", "--all-from", str(source)])

    assert not isinstance(result.exception, ValueError), result.output


def test_blocklist_add_rejects_a_bad_name_with_a_message(tmp_path: Path) -> None:
    """An invalid subscription name exits 1 with a message, not a traceback."""
    source = tmp_path / "x.txt"
    source.write_text("4894212\n", encoding="utf-8")

    result = runner.invoke(app, ["blocklist", "add", "bad name", str(source)])

    assert result.exit_code == 1, result.output
    assert "Invalid subscription name" in result.output
    assert not isinstance(result.exception, ValueError), result.output


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://example.com/list.txt", "txt"),
        ("https://example.com/list.txt?v=2", "txt"),
        ("https://example.com/list.json#frag", "json"),
        ("./local/list.csv", "csv"),
    ],
)
def test_detect_format_reads_the_path_not_the_query(source: str, expected: str) -> None:
    """A query string or fragment is not part of the extension."""
    assert detect_format(source) == expected
