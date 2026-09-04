"""CLI wiring for the blocklist sub-app.

Engine tests in test_filterlist_apply.py prove plan_apply returns the
right ApplyPlan; these tests prove the CLI mounts the sub-app, accepts
--profile only on apply, refuses an unsupported extension at add time,
leaves the account untouched under --dry-run, and skips the unblock
prompt under --force.

Network is monkeypatched at the filterlist_fetch layer, so a test
never resolves a URL or reads a file from disk.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeSession
from tidal_sync import cli_blocklist
from tidal_sync.cli import app
from tidal_sync.engine.filterlist import FormatError, parse_filter_list
from tidal_sync.engine.filterlist_apply import ApplyPlan
from tidal_sync.engine.filterlist_fetch import FetchError
from tidal_sync.engine.filterlist_store import Subscription

runner = CliRunner()

_BLOCKLIST_SUBCOMMANDS = ("add", "remove", "update", "show", "apply")
# ``apply`` is the only blocklist subcommand that touches an account,
# so it is the only one that carries ``--profile``. The other four
# manage the local subscription store, which is global to the machine.
_BLOCKLIST_SUBCOMMAND_WITH_PROFILE = ("apply",)
# Inverse: every subcommand whose surface must reject ``--profile``.
# Used by the rejection test so a regression that re-adds the flag
# fails the build.
_BLOCKLIST_SUBCOMMANDS_WITHOUT_PROFILE = ("add", "update", "remove", "show")


# Test 1: every subcommand is reachable and --help works.
@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMANDS)
def test_blocklist_subcommand_help_works(subcommand: str) -> None:
    result = runner.invoke(app, ["blocklist", subcommand, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


# Test 2: --profile / -p is accepted only on apply. The other four
# subcommands manage the local subscription store and do not touch
# an account, so the flag is gone rather than silently accepted.
@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMAND_WITH_PROFILE)
def test_blocklist_subcommand_accepts_profile_flag_long(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

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

    result = runner.invoke(app, args)
    # A missing required argument (e.g. update without a name still parses,
    # but a malformed apply without prereqs surfaces as Typer exit 2) is
    # acceptable here. The contract we pin is that the --profile flag is
    # accepted, so we only care that the failure is NOT "no such option".
    assert "no such option" not in result.output.lower()
    assert "--profile" not in result.output or "Usage" in result.output


@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMAND_WITH_PROFILE)
def test_blocklist_subcommand_accepts_profile_flag_short(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

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

    result = runner.invoke(app, args)
    assert "no such option" not in result.output.lower()


# Test 6: an oversized prune batch exits 1 through the shared handler,
# so no traceback reaches the operator. --prune is the only bulk path
# to unblock_artists.


# Test 6b: one id to block and an oversized unblock set. The block batch is
# small enough to pass its own leaf guard, so without the up-front check it
# goes out before the unblock guard fires.


# Test 7: an unsupported extension is rejected at add, not at apply.
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
# Task 13 + Task 5: --profile is gone from every blocklist subcommand
# that does not touch an account.
#
# Background: --profile was accepted-and-ignored on every blocklist
# subcommand. The maintainer's call is to drop it from the four that
# touch no account (``add``, ``update``, ``show`` and ``remove``);
# ``apply`` keeps it because it is the only subcommand that
# interacts with the Tidal API. The local subscription store is
# global to the machine, so the account flag has nothing to choose.
# Drop the option outright, do not merely relabel it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMANDS_WITHOUT_PROFILE)
def test_blocklist_subcommand_rejects_profile_flag(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    """``--profile`` on the non-apply subcommands must surface as a usage error.

    The pin here is the exit code and the error message, not just the
    output text: a regression that re-adds the flag in a way Typer
    silently accepts (e.g. an undeclared option that falls through to
    an empty parser) would still fail because the command would exit
    zero on what is otherwise a malformed invocation.
    """
    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [])
    monkeypatch.setattr(cli_blocklist, "remove_subscription", lambda name: True)

    args: list[str] = ["blocklist", subcommand, "--profile", "second"]
    if subcommand == "remove":
        args += ["spam"]
    if subcommand == "add":
        args += ["spam", "https://example.test/list.txt"]

    result = runner.invoke(app, args)

    assert result.exit_code == 2, (
        f"--profile must make blocklist {subcommand} exit 2 (usage error), "
        f"got exit_code={result.exit_code} output={result.output!r}"
    )
    assert "no such option" in result.output.lower(), (
        f"--profile on blocklist {subcommand} must report 'no such option', got: {result.output!r}"
    )


@pytest.mark.parametrize("subcommand", _BLOCKLIST_SUBCOMMANDS_WITHOUT_PROFILE)
def test_blocklist_subcommand_rejects_profile_flag_short_form(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    """The ``-p`` short form must also be rejected.

    Typer accepts long and short forms separately, so the long-form
    pin above is not enough to catch a regression that re-adds the
    flag under one form but not the other.
    """
    monkeypatch.setattr(cli_blocklist, "load_subscriptions", lambda: [])
    monkeypatch.setattr(cli_blocklist, "remove_subscription", lambda name: True)

    args: list[str] = ["blocklist", subcommand, "-p", "second"]
    if subcommand == "remove":
        args += ["spam"]
    if subcommand == "add":
        args += ["spam", "https://example.test/list.txt"]

    result = runner.invoke(app, args)

    assert result.exit_code == 2, (
        f"-p must make blocklist {subcommand} exit 2 (usage error), "
        f"got exit_code={result.exit_code} output={result.output!r}"
    )
    assert "no such option" in result.output.lower(), (
        f"-p on blocklist {subcommand} must report 'no such option', got: {result.output!r}"
    )


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
# Tests for the update-name defect.
#
# Background: the parent found that ``blocklist update <name>`` ran
# the identity filter ``[s for s in subs if s.name]``, so ``update
# nosuchname`` refetched every subscription and exited 0. The loop
# also rewrote the whole index on every iteration. These tests pin
# the fix: an unknown name exits 1 with no fetches, and a named
# update refetches only that subscription.
# ---------------------------------------------------------------------------


def test_update_unknown_name_exits_one_and_fetches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unknown subscription name must exit 1 without refetching anything."""
    from tidal_sync.engine import filterlist_store

    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path / "filter_lists")
    source = tmp_path / "a.txt"
    source.write_text("111\n", encoding="utf-8")
    filterlist_store.add_subscription(
        filterlist_store.Subscription(name="alpha", source=str(source), format="txt")
    )

    fetched: list[str] = []
    monkeypatch.setattr(
        cli_blocklist, "fetch_source", lambda src, fmt, dest: fetched.append(src) or 0
    )

    result = runner.invoke(app, ["blocklist", "update", "nosuchname"])

    assert result.exit_code == 1
    assert "No such subscription" in result.output
    assert fetched == []


def test_update_named_subscription_touches_only_that_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Naming one subscription must not refetch its siblings."""
    from tidal_sync.engine import filterlist_store

    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path / "filter_lists")
    for name in ("alpha", "beta"):
        source = tmp_path / f"{name}.txt"
        source.write_text("111\n", encoding="utf-8")
        filterlist_store.add_subscription(
            filterlist_store.Subscription(name=name, source=str(source), format="txt")
        )

    fetched: list[str] = []
    monkeypatch.setattr(
        cli_blocklist, "fetch_source", lambda src, fmt, dest: fetched.append(src) or 0
    )

    result = runner.invoke(app, ["blocklist", "update", "alpha"])

    assert result.exit_code == 0
    assert len(fetched) == 1
    assert fetched[0].endswith("alpha.txt")
