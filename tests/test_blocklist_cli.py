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
from tidal_sync.engine.filterlist_apply import ApplyOutcome, ApplyPlan
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

    async def _execute_apply(session: object, plan: ApplyPlan, *, prune: bool) -> ApplyOutcome:
        ids = [tid for tid, _name in plan.to_block]
        return ApplyOutcome(
            blocked=UploadOutcome(applied=ids[:1], rejected=ids[1:]),
            unblocked=None,
            capped=False,
        )

    monkeypatch.setattr(cli_blocklist, "execute_apply", _execute_apply)

    result = runner.invoke(app, ["blocklist", "apply", "--force"])

    assert result.exit_code == 1, result.output
    assert "102" in result.output


# Test 4: --dry-run performs no writes. execute_apply must never be called.
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

    async def _execute_apply(session: object, plan: ApplyPlan, *, prune: bool) -> ApplyOutcome:
        ids = [tid for tid, _name in plan.to_block]
        block_calls.append(list(ids))
        return ApplyOutcome(
            blocked=UploadOutcome(applied=list(ids), rejected=[]),
            unblocked=None,
            capped=False,
        )

    monkeypatch.setattr(cli_blocklist, "execute_apply", _execute_apply)

    result = runner.invoke(app, ["blocklist", "apply", "--dry-run", "--prune", "--force"])

    assert result.exit_code == 0, result.output
    assert block_calls == [], "dry-run must not call execute_apply"


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

    async def _execute_apply(session: object, plan: ApplyPlan, *, prune: bool) -> ApplyOutcome:
        ids = [tid for tid, _name in plan.to_block]
        unlisted_ids = [tid for tid, _name in plan.unlisted]
        if prune and unlisted_ids:
            unblock_calls.append(list(unlisted_ids))
            return ApplyOutcome(
                blocked=UploadOutcome(applied=list(ids), rejected=[]),
                unblocked=UploadOutcome(applied=list(unlisted_ids), rejected=[]),
                capped=False,
            )
        return ApplyOutcome(
            blocked=UploadOutcome(applied=list(ids), rejected=[]),
            unblocked=None,
            capped=False,
        )

    monkeypatch.setattr(cli_blocklist, "execute_apply", _execute_apply)

    # 998 + 997 are well below the ten-id rail, so the rail is irrelevant;
    # what we pin is that --prune routes the unblock through execute_apply
    # and prompt_unblock is never asked.
    result = runner.invoke(app, ["blocklist", "apply", "--prune", "--force"])

    assert result.exit_code == 0, result.output
    assert prompt_calls == [], "force must skip the unblock prompt"
    assert unblock_calls == [["998", "997"]], "execute_apply must handle the unblock under --prune"


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


# ---------------------------------------------------------------------------
# Tests for the rail guarding execute_apply.
#
# Background: the parent found that ``plan_apply`` performed the
# block write before the CLI asked the operator to retype the
# profile name, so a declined confirmation still left the account
# touched. These tests drive the REAL ``plan_apply`` and fake only
# the curation verbs; a wrong-shaped answer must leave
# ``block_artists`` untouched and a right-shaped answer must call
# it exactly once.
# ---------------------------------------------------------------------------


@pytest.fixture
def rail_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list[list[str]]]:
    """Wire the dependencies of the real ``plan_apply`` for the rail tests.

    Returns a box the test can read to count ``block_artists`` and
    ``unblock_artists`` calls without monkeypatching them.
    """
    from tidal_sync.engine import filterlist_apply, filterlist_store

    box: dict[str, list[list[str]]] = {"block_calls": [], "unblock_calls": []}

    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path / "filter_lists")

    ids = [
        ("501", "Alpha"),
        ("502", "Beta"),
        ("503", "Gamma"),
        ("504", "Delta"),
        ("505", "Epsilon"),
        ("506", "Zeta"),
        ("507", "Eta"),
        ("508", "Theta"),
        ("509", "Iota"),
        ("510", "Kappa"),
        ("511", "Lambda"),
    ]

    def _fake_subscription() -> Subscription:
        now = datetime.now(UTC).isoformat()
        return Subscription(
            name="spam",
            source="https://example.test/spam.txt",
            format="txt",
            last_fetched=now,
            last_count=len(ids),
        )

    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [_fake_subscription()])
    monkeypatch.setattr(cli_module, "get_session", lambda profile="default": _fake_session())

    def _fetch(source: str, fmt: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(tid for tid, _name in ids) + "\n", encoding="utf-8")
        return len(ids)

    monkeypatch.setattr(cli_blocklist, "fetch_source", _fetch)

    # Pre-populate the cache so the real plan_apply reads from disk
    # rather than refetching: the subscription is fresh.
    cache_dir = tmp_path / "filter_lists" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "spam.txt").write_text(
        "\n".join(tid for tid, _name in ids) + "\n", encoding="utf-8"
    )

    async def _fetch_blocked_ids(session: object) -> list[str]:
        return []

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artist_ids", _fetch_blocked_ids)
    monkeypatch.setattr(filterlist_apply, "parse_filter_list", lambda data, fmt: ids)

    async def _block(session: object, ids: list[str]) -> UploadOutcome:
        box["block_calls"].append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _unblock(session: object, ids: list[str]) -> UploadOutcome:
        box["unblock_calls"].append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(filterlist_apply, "block_artists", _block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", _unblock)
    # The CLI imports execute_apply at module load; patch the
    # binding the CLI holds, not just the attribute on the engine
    # module.
    monkeypatch.setattr(cli_blocklist, "execute_apply", _execute_apply_through(box))

    return box


def _execute_apply_through(box: dict[str, list[list[str]]]):
    """Build a fake ``execute_apply`` that delegates to the box-tracked verbs.

    Mirrors the real function: cap check, then block_artists, then
    optional unblock_artists. Returns ``ApplyOutcome`` so the CLI's
    print and exit logic runs as in production.
    """

    async def _execute_apply(session: object, plan: ApplyPlan, *, prune: bool) -> ApplyOutcome:
        from tidal_sync.domain.results import UploadOutcome as _UO
        from tidal_sync.engine.filterlist_apply import (
            MAX_APPLY_IDS,
        )
        from tidal_sync.engine.filterlist_apply import (
            ApplyOutcome as _AO,
        )

        if len(plan.to_block) > MAX_APPLY_IDS:
            return _AO(blocked=None, unblocked=None, capped=True)
        blocked = None
        if plan.to_block:
            blocked = _UO(applied=[tid for tid, _ in plan.to_block], rejected=[])
            box["block_calls"].append([tid for tid, _ in plan.to_block])
        unblocked = None
        if prune and plan.unlisted:
            unblocked = _UO(applied=[tid for tid, _ in plan.unlisted], rejected=[])
            box["unblock_calls"].append([tid for tid, _ in plan.unlisted])
        return _AO(blocked=blocked, unblocked=unblocked, capped=False)

    return _execute_apply


def test_declining_the_rail_blocks_nothing(
    rail_setup: dict[str, list[list[str]]],
) -> None:
    """Declining the confirmation must leave ``block_artists`` untouched.

    The pre-fix bug was that ``plan_apply`` performed the block
    write before the CLI asked the operator to retype the profile
    name, so a declined prompt still touched the account.
    """
    box = rail_setup
    # Four ids are above the ten-id rail's "print every id, no prompt"
    # threshold is below; we add enough positional ids to push it
    # over by using the subscription union alone. The plan carries
    # four ids, so without --force the rail fires.
    result = runner.invoke(app, ["blocklist", "apply"], input="wrong-name\n")

    assert result.exit_code != 0, result.output
    assert "Confirmation did not match" in result.output
    assert box["block_calls"] == [], "a declined confirmation must never call block_artists"
    assert box["unblock_calls"] == [], "a declined confirmation must never call unblock_artists"


def test_confirming_the_rail_blocks_exactly_once(
    rail_setup: dict[str, list[list[str]]],
) -> None:
    """Accepting the confirmation calls ``block_artists`` exactly once with every id.

    The pre-fix bug doubled the writes because the same ids were
    blocked once inside the planner and once again in the CLI.
    """
    box = rail_setup
    result = runner.invoke(app, ["blocklist", "apply"], input="default\n")

    assert result.exit_code == 0, result.output
    assert len(box["block_calls"]) == 1, (
        f"block_artists must be called exactly once, got {len(box['block_calls'])}"
    )
    assert sorted(box["block_calls"][0]) == [
        "501",
        "502",
        "503",
        "504",
        "505",
        "506",
        "507",
        "508",
        "509",
        "510",
        "511",
    ]
    assert box["unblock_calls"] == [], "without --prune the rail must not trigger any unblock"
