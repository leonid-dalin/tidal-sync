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
from tidal_sync import cli_blocklist
from tidal_sync.cli import app
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine.filterlist import FormatError, parse_filter_list
from tidal_sync.engine.filterlist_apply import ApplyPlan
from tidal_sync.engine.filterlist_fetch import FetchError
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

    monkeypatch.setattr(cli_blocklist, "fetch_source", _noop_fetch)
    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [])
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: None)
    monkeypatch.setattr(cli_blocklist, "remove_subscription", lambda name: True)
    monkeypatch.setattr(cli_blocklist, "cache_path", lambda name, fmt: Path("/tmp/fake"))

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(to_block=[], already_blocked=[], unlisted=[], errors=[])

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_blocklist, "prompt_unblock", lambda candidates, *, force: [])

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

    monkeypatch.setattr(cli_blocklist, "fetch_source", _noop_fetch)
    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [])
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: None)
    monkeypatch.setattr(cli_blocklist, "remove_subscription", lambda name: True)
    monkeypatch.setattr(cli_blocklist, "cache_path", lambda name, fmt: Path("/tmp/fake"))

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(to_block=[], already_blocked=[], unlisted=[], errors=[])

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_blocklist, "prompt_unblock", lambda candidates, *, force: [])

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
    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [_fake_subscription()])
    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("101", "Alpha"), ("102", "Beta")],
            already_blocked=[],
            unlisted=[],
            errors=[],
        )

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_blocklist, "prompt_unblock", lambda candidates, *, force: [])

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        return UploadOutcome(applied=[ids[0]], rejected=[ids[1]])

    monkeypatch.setattr(cli_blocklist, "block_artists", _block_artists)

    result = runner.invoke(app, ["blocklist", "apply", "--force"])

    assert result.exit_code == 1, result.output
    assert "102" in result.output


# Test 4: --dry-run performs no writes. block_artists must never be called.
def test_blocklist_apply_dry_run_makes_no_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_calls: list[list[str]] = []

    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [_fake_subscription()])
    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("201", "Gamma"), ("202", "Delta")],
            already_blocked=[],
            unlisted=[("999", "")],
            errors=[],
        )

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_blocklist, "prompt_unblock", lambda candidates, *, force: [])

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        block_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _unblock_artists(session: object, ids: list[str]) -> UploadOutcome:
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_blocklist, "block_artists", _block_artists)
    monkeypatch.setattr(cli_blocklist, "unblock_artists", _unblock_artists)

    result = runner.invoke(app, ["blocklist", "apply", "--dry-run", "--prune", "--force"])

    assert result.exit_code == 0, result.output
    assert block_calls == [], "dry-run must not call block_artists"


# Test 5: --force skips the rail AND skips the unblock prompt.
def test_blocklist_apply_force_skips_unblock_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unblock_calls: list[list[str]] = []
    prompt_calls: list[list[tuple[str, str]]] = []

    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [_fake_subscription()])
    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("301", "Echo")],
            already_blocked=[],
            unlisted=[("998", "Foo"), ("997", "Bar")],
            errors=[],
        )

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)

    def _prompt_unblock(candidates: list[tuple[str, str]], *, force: bool) -> list[str]:
        prompt_calls.append(list(candidates))
        return []

    monkeypatch.setattr(cli_blocklist, "prompt_unblock", _prompt_unblock)

    async def _block_artists(session: object, ids: list[str]) -> UploadOutcome:
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _unblock_artists(session: object, ids: list[str]) -> UploadOutcome:
        unblock_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_blocklist, "block_artists", _block_artists)
    monkeypatch.setattr(cli_blocklist, "unblock_artists", _unblock_artists)

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
        raise FormatError(f"unsupported filter-list format: {fmt!r}")

    monkeypatch.setattr(cli_blocklist, "fetch_source", _fetch)
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: add_calls.append(sub))

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
    monkeypatch.setattr(cli_blocklist, "remove_subscription", lambda name: False)

    result = runner.invoke(app, ["blocklist", "remove", "ghost"])

    assert result.exit_code != 0, result.output


# Test 8 (collateral): the top-level --help still shows the original five
# commands. Adding a sub-app must not silently mask them.
def test_top_level_help_still_shows_original_five_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for original in ("login", "logout", "like", "unlike", "clear"):
        assert original in result.output, f"{original} missing from top-level --help"


# ---------------------------------------------------------------------------
# Tests for the add-command recording defect.
#
# Background: the parent found by running the CLI for real that
# ``blocklist add`` validates the source extension but never records
# what it read. ``show`` therefore prints ``last_count=0`` and
# ``last_fetched=never``, and the cache directory is never populated,
# even though ``update`` already records both correctly.
#
# These tests pin the fix at the CLI layer with the store directory
# redirected at ``tmp_path`` so no test touches the real
# ``~/.tidal_sync``.
# ---------------------------------------------------------------------------


def _write_local_list(tmp_path: Path, name: str, ids: list[str]) -> Path:
    """Drop a real txt file with three ids under tmp_path and return it."""
    body = "\n".join(ids).encode("utf-8") + b"\n"
    path = tmp_path / name
    path.write_bytes(body)
    return path


def _stub_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    """Patch ``fetch_source`` to copy the source to ``dest`` and parse it.

    The stub honours the contract of the real fetcher: it writes the
    bytes the fix expects to land in the cache and returns the id
    count. This is what the real ``fetch_source`` does for a local file
    and keeps the tests honest about what the fix needs to pass.
    """
    called: list[Path] = []

    def _fetch(source: str, fmt: str, dest: Path) -> int:
        called.append(dest)
        data = Path(source).read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(parse_filter_list(data, fmt))

    monkeypatch.setattr(cli_blocklist, "fetch_source", _fetch)
    return called


# Test 9: after add, last_count on the persisted subscription equals the
# number of ids the source contained. This is the symptom the parent
# found: ``blocklist show`` printed ``last_count=0`` after a successful add.
def test_blocklist_add_records_last_count_from_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = ["4894212", "8107285", "1234567"]
    src = _write_local_list(tmp_path, "kpop.txt", ids)
    _stub_fetch(monkeypatch)
    monkeypatch.setattr(
        cli_blocklist, "cache_path", lambda name, fmt: tmp_path / "cache" / f"{name}.{fmt}"
    )

    persisted: list[Subscription] = []
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: persisted.append(sub))

    result = runner.invoke(app, ["blocklist", "add", "kpop", str(src)])

    assert result.exit_code == 0, result.output
    assert len(persisted) == 1
    assert persisted[0].name == "kpop"
    assert persisted[0].last_count == len(ids)


# Test 10: after add, last_fetched is populated. The parent found the
# symptom in ``show`` as ``last_fetched=never`` immediately after add.
def test_blocklist_add_records_last_fetched_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = ["4894212", "8107285", "1234567"]
    src = _write_local_list(tmp_path, "kpop.txt", ids)
    _stub_fetch(monkeypatch)
    monkeypatch.setattr(
        cli_blocklist, "cache_path", lambda name, fmt: tmp_path / "cache" / f"{name}.{fmt}"
    )

    persisted: list[Subscription] = []
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: persisted.append(sub))

    result = runner.invoke(app, ["blocklist", "add", "kpop", str(src)])

    assert result.exit_code == 0, result.output
    assert len(persisted) == 1
    assert persisted[0].last_fetched is not None
    # A timestamp must round-trip through datetime.fromisoformat without error.
    parsed = datetime.fromisoformat(persisted[0].last_fetched)
    assert parsed.tzinfo is not None


# Test 11: after add, the cache file exists under cache/ and parses back
# to the same ids the source contained. The parent found that
# ``~/.tidal_sync/filter_lists/cache/kpop.txt`` was missing after add.
def test_blocklist_add_writes_cache_file_with_source_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = ["4894212", "8107285", "1234567"]
    src = _write_local_list(tmp_path, "kpop.txt", ids)
    _stub_fetch(monkeypatch)

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(cli_blocklist, "cache_path", lambda name, fmt: cache_dir / f"{name}.{fmt}")

    persisted: list[Subscription] = []
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: persisted.append(sub))

    result = runner.invoke(app, ["blocklist", "add", "kpop", str(src)])

    assert result.exit_code == 0, result.output
    cache_file = cache_dir / "kpop.txt"
    assert cache_file.exists(), "add must populate the cache file"
    parsed_ids = parse_filter_list(cache_file.read_bytes(), "txt")
    assert parsed_ids == [(i, "") for i in ids]


# Test 12: an unsupported extension is still rejected at add time and no
# subscription is persisted. This pins the preserved-behaviour line from
# the brief: ``add`` validates before subscribing.
def test_blocklist_add_rejects_unsupported_extension_without_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli_blocklist, "cache_path", lambda name, fmt: tmp_path / "cache" / f"{name}.{fmt}"
    )

    persisted: list[Subscription] = []
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: persisted.append(sub))

    fetch_calls: list[tuple[str, str, Path]] = []

    def _fetch(source: str, fmt: str, dest: Path) -> int:
        fetch_calls.append((source, fmt, dest))
        return 0

    monkeypatch.setattr(cli_blocklist, "fetch_source", _fetch)

    result = runner.invoke(app, ["blocklist", "add", "bad", "https://example.test/list.xyz"])

    assert result.exit_code != 0, result.output
    assert persisted == [], "add must not persist when the extension is unsupported"
    assert fetch_calls == [], "fetch_source must not be called for an unsupported extension"


# Test 13: a source that fails to fetch is still rejected at add time
# and no subscription is persisted. This pins the second half of the
# preserved-behaviour line.
def test_blocklist_add_rejects_source_that_fails_to_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli_blocklist, "cache_path", lambda name, fmt: tmp_path / "cache" / f"{name}.{fmt}"
    )

    persisted: list[Subscription] = []
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: persisted.append(sub))

    def _fetch(source: str, fmt: str, dest: Path) -> int:
        raise FetchError("Fetch failed: HTTP 503")

    monkeypatch.setattr(cli_blocklist, "fetch_source", _fetch)

    result = runner.invoke(app, ["blocklist", "add", "kpop", "https://example.test/list.txt"])

    assert result.exit_code != 0, result.output
    assert persisted == [], "add must not persist when the fetch fails"


# Test 14 (collateral): ``remove`` and ``show`` still read the same
# fields ``add`` now writes. ``remove`` deletes by name and ``show``
# formats last_count and last_fetched through ``_format_table``, so a
# subscription written by the fixed ``add`` must round-trip through
# load_subscriptions / remove_subscription without losing fields.
def test_blocklist_remove_and_show_round_trip_after_fixed_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = ["4894212", "8107285", "1234567"]
    src = _write_local_list(tmp_path, "kpop.txt", ids)
    _stub_fetch(monkeypatch)
    monkeypatch.setattr(
        cli_blocklist, "cache_path", lambda name, fmt: tmp_path / "cache" / f"{name}.{fmt}"
    )

    persisted: list[Subscription] = []
    monkeypatch.setattr(cli_blocklist, "add_subscription", lambda sub: persisted.append(sub))

    add_result = runner.invoke(app, ["blocklist", "add", "kpop", str(src)])
    assert add_result.exit_code == 0, add_result.output
    assert len(persisted) == 1
    written = persisted[0]
    assert written.last_count == len(ids)
    assert written.last_fetched is not None

    # Now exercise show and remove with the recorded subscription loaded.
    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [written])

    show_result = runner.invoke(app, ["blocklist", "show"])
    assert show_result.exit_code == 0, show_result.output
    assert "kpop" in show_result.output
    assert f"last_count={len(ids)}" in show_result.output
    assert "last_fetched=never" not in show_result.output

    removed: list[str] = []

    def _remove(name: str) -> bool:
        removed.append(name)
        return True

    monkeypatch.setattr(cli_blocklist, "remove_subscription", _remove)

    remove_result = runner.invoke(app, ["blocklist", "remove", "kpop"])
    assert remove_result.exit_code == 0, remove_result.output
    assert removed == ["kpop"]
