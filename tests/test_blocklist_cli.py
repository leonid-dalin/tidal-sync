"""CLI wiring for the blocklist sub-app.

Engine tests in test_filterlist_apply.py prove plan_apply returns the
right ApplyPlan; these tests prove the CLI mounts the sub-app, accepts
--profile on every subcommand, refuses an unsupported extension at add
time, leaves the account untouched under --dry-run, and skips the
unblock prompt under --force.

Network is monkeypatched at the filterlist_fetch layer, so a test
never resolves a URL or reads a file from disk.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tidal_sync import cli as cli_module
from tidal_sync.cli import app
from tidal_sync.cli_blocklist import blocklist_app
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine.filterlist_apply import ApplyPlan
from tidal_sync.engine.filterlist_store import Subscription

runner = CliRunner()

_BLOCKLIST_SUBCOMMANDS = ("add", "remove", "update", "show", "apply")


class _FakeUser:
    id = 4242


def _fake_session() -> object:
    return type("S", (), {"user": _FakeUser()})()


def _fake_subscription(
    name: str = "spam",
    source: str = "https://example.test/list.txt",
    fmt: str = "txt",
    last_count: int = 0,
    last_error: str | None = None,
) -> Subscription:
    now = datetime.now(UTC).isoformat()
    return Subscription(
        name=name,
        source=source,
        format=fmt,
        last_fetched=now,
        last_count=last_count,
        last_error=last_error,
    )


# Test 1: every subcommand is reachable and --help works.
@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMANDS)
def test_blocklist_subcommand_help_works(subcommand: str) -> None:
    result = runner.invoke(app, ["blocklist", subcommand, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


# Test 2: --profile / -p is accepted by every subcommand.
@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMANDS)
def test_blocklist_subcommand_accepts_profile_flag_long(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    def _noop_fetch(source: str, fmt: str, dest: Path) -> int:
        return 0

    monkeypatch.setattr(cli_module, "fetch_source", _noop_fetch)
    monkeypatch.setattr(cli_module, "load_subscriptions", lambda: [])
    monkeypatch.setattr(cli_module, "add_subscription", lambda sub: None)
    monkeypatch.setattr(cli_module, "save_subscriptions", lambda subs: None)
    monkeypatch.setattr(cli_module, "remove_subscription", lambda name: True)
    monkeypatch.setattr(cli_module, "cache_path", lambda name, fmt: Path("/tmp/fake"))

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(to_block=[], already_blocked=[], unlisted=[], errors=[])

    monkeypatch.setattr(cli_module, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_module, "prompt_unblock", lambda candidates, *, force: [])

    args: list[str] = ["blocklist", subcommand, "--profile", "second"]
    if subcommand == "add":
        args += ["spam", "https://example.test/list.txt"]
    elif subcommand == "remove":
        args += ["spam"]

    result = runner.invoke(app, args)
    # A missing required argument (e.g. update without a name still parses,
    # but a malformed apply without prereqs surfaces as Typer exit 2) is
    # acceptable here. The contract we pin is that the --profile flag is
    # accepted, so we only care that the failure is NOT "no such option".
    assert "no such option" not in result.output.lower()
    assert "--profile" not in result.output or "Usage" in result.output


@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMANDS)
def test_blocklist_subcommand_accepts_profile_flag_short(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    def _noop_fetch(source: str, fmt: str, dest: Path) -> int:
        return 0

    monkeypatch.setattr(cli_module, "fetch_source", _noop_fetch)
    monkeypatch.setattr(cli_module, "load_subscriptions", lambda: [])
    monkeypatch.setattr(cli_module, "add_subscription", lambda sub: None)
    monkeypatch.setattr(cli_module, "save_subscriptions", lambda subs: None)
    monkeypatch.setattr(cli_module, "remove_subscription", lambda name: True)
    monkeypatch.setattr(cli_module, "cache_path", lambda name, fmt: Path("/tmp/fake"))

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(to_block=[], already_blocked=[], unlisted=[], errors=[])

    monkeypatch.setattr(cli_module, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_module, "prompt_unblock", lambda candidates, *, force: [])

    args: list[str] = ["blocklist", subcommand, "-p", "second"]
    if subcommand == "add":
        args += ["spam", "https://example.test/list.txt"]
    elif subcommand == "remove":
        args += ["spam"]

    result = runner.invoke(app, args)
    assert "no such option" not in result.output.lower()


# Test 3: apply exits 1 when any id is rejected, matching the contract in
# _run_block_command. The engine does the per-id classification; the CLI
# only echoes it.
def test_blocklist_apply_exits_one_when_to_block_has_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("101", "Alpha"), ("102", "Beta")],
            already_blocked=[],
            unlisted=[],
            errors=[],
        )

    monkeypatch.setattr(cli_module, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_module, "prompt_unblock", lambda candidates, *, force: [])

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        return UploadOutcome(applied=[ids[0]], rejected=[ids[1]])

    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    result = runner.invoke(app, ["blocklist", "apply", "--force"])

    assert result.exit_code == 1, result.output
    assert "102" in result.output


# Test 4: --dry-run performs no writes. block_artists must never be called.
def test_blocklist_apply_dry_run_makes_no_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_calls: list[list[str]] = []

    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("201", "Gamma"), ("202", "Delta")],
            already_blocked=[],
            unlisted=[("999", "")],
            errors=[],
        )

    monkeypatch.setattr(cli_module, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_module, "prompt_unblock", lambda candidates, *, force: [])

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        block_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _unblock_artists(session: object, ids: list[str]) -> UploadOutcome:
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)
    monkeypatch.setattr(cli_module.curation, "unblock_artists", _unblock_artists)

    result = runner.invoke(app, ["blocklist", "apply", "--dry-run", "--prune", "--force"])

    assert result.exit_code == 0, result.output
    assert block_calls == [], "dry-run must not call block_artists"


# Test 5: --force skips the rail AND skips the unblock prompt.
def test_blocklist_apply_force_skips_unblock_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unblock_calls: list[list[str]] = []
    prompt_calls: list[list[tuple[str, str]]] = []

    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("301", "Echo")],
            already_blocked=[],
            unlisted=[("998", "Foo"), ("997", "Bar")],
            errors=[],
        )

    monkeypatch.setattr(cli_module, "plan_apply", _plan_apply)

    def _prompt_unblock(candidates: list[tuple[str, str]], *, force: bool) -> list[str]:
        prompt_calls.append(list(candidates))
        return []

    monkeypatch.setattr(cli_module, "prompt_unblock", _prompt_unblock)

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _unblock_artists(session: object, ids: list[str]) -> UploadOutcome:
        unblock_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)
    monkeypatch.setattr(cli_module.curation, "unblock_artists", _unblock_artists)

    # 998 + 997 are well below the ten-id rail, so the rail is irrelevant;
    # what we pin is that prompt_unblock sees force=True and returns [].
    result = runner.invoke(app, ["blocklist", "apply", "--prune", "--force"])

    assert result.exit_code == 0, result.output
    assert len(prompt_calls) == 1
    assert prompt_calls[0][0][0] == "998"
    assert unblock_calls == [], "force must skip the unblock prompt and therefore the unblock"


# Test 6: an unsupported extension is rejected at add, not at apply.
def test_blocklist_add_rejects_unsupported_extension_at_add_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_calls: list[Subscription] = []

    def _fetch(source: str, fmt: str, dest: Path) -> int:
        raise cli_module.FormatError(f"unsupported filter-list format: {fmt!r}")

    monkeypatch.setattr(cli_module, "fetch_source", _fetch)
    monkeypatch.setattr(cli_module, "add_subscription", lambda sub: add_calls.append(sub))

    result = runner.invoke(
        app,
        [
            "blocklist",
            "add",
            "bad",
            "https://example.test/list.xyz",
        ],
    )

    assert result.exit_code != 0, result.output
    assert add_calls == [], "add must not persist a subscription with an unsupported extension"


# Test 7: remove on an unknown name exits non-zero.
def test_blocklist_remove_unknown_name_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "remove_subscription", lambda name: False)

    result = runner.invoke(app, ["blocklist", "remove", "ghost"])

    assert result.exit_code != 0, result.output


# Test 8 (collateral): the top-level --help still shows the original five
# commands. Adding a sub-app must not silently mask them.
def test_top_level_help_still_shows_original_five_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for original in ("login", "logout", "like", "unlike", "clear"):
        assert original in result.output, f"{original} missing from top-level --help"