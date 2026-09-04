"""Apply-path behaviour for the blocklist sub-app.

These prove the CLI honours the batch ceiling, the confirmation rail, the
dry-run gate and the unblock prompt on the ``apply`` path. Engine-level
guards live in test_filterlist_apply.py; these pin the CLI mounting on top
of them.

Network is monkeypatched at the filterlist_fetch layer, so a test never
resolves a URL or reads a file from disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeSession, fake_subscription
from tidal_sync import cli_blocklist
from tidal_sync.cli import app
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine import curation
from tidal_sync.engine.filterlist_apply import ApplyOutcome, ApplyPlan
from tidal_sync.engine.filterlist_store import Subscription

runner = CliRunner()

_BLOCKLIST_SUBCOMMANDS = ("add", "remove", "update", "show", "apply")
_BLOCKLIST_SUBCOMMAND_WITH_PROFILE = ("apply",)
_BLOCKLIST_SUBCOMMANDS_WITHOUT_PROFILE = ("add", "update", "remove", "show")


# Test 3: apply exits 1 when any id is rejected, matching the contract in
# _run_block_command. The engine does the per-id classification; the CLI
# only echoes it.
def test_blocklist_apply_exits_one_when_to_block_has_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_blocklist, "load_subscriptions", lambda: [fake_subscription(name="spam")]
    )
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("101", "Alpha"), ("102", "Beta")],
            already_blocked=[],
            unlisted=[],
            errors=[],
        )

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_blocklist, "prompt_unblock", lambda candidates, *, force: [])

    async def _execute_apply(
        session: object, plan: ApplyPlan, *, unblock_ids: list[str]
    ) -> ApplyOutcome:
        ids = [tid for tid, _name in plan.to_block]
        return ApplyOutcome(
            blocked=UploadOutcome(applied=ids[:1], rejected=ids[1:]),
            unblocked=None,
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

    monkeypatch.setattr(
        cli_blocklist, "load_subscriptions", lambda: [fake_subscription(name="spam")]
    )
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("201", "Gamma"), ("202", "Delta")],
            already_blocked=[],
            unlisted=[("999", "")],
            errors=[],
        )

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    monkeypatch.setattr(cli_blocklist, "prompt_unblock", lambda candidates, *, force: [])

    async def _execute_apply(
        session: object, plan: ApplyPlan, *, unblock_ids: list[str]
    ) -> ApplyOutcome:
        ids = [tid for tid, _name in plan.to_block]
        block_calls.append(list(ids))
        return ApplyOutcome(
            blocked=UploadOutcome(applied=list(ids), rejected=[]),
            unblocked=None,
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

    monkeypatch.setattr(
        cli_blocklist, "load_subscriptions", lambda: [fake_subscription(name="spam")]
    )
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

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

    async def _execute_apply(
        session: object, plan: ApplyPlan, *, unblock_ids: list[str]
    ) -> ApplyOutcome:
        ids = [tid for tid, _name in plan.to_block]
        if unblock_ids:
            unblock_calls.append(list(unblock_ids))
            return ApplyOutcome(
                blocked=UploadOutcome(applied=list(ids), rejected=[]),
                unblocked=UploadOutcome(applied=list(unblock_ids), rejected=[]),
            )
        return ApplyOutcome(
            blocked=UploadOutcome(applied=list(ids), rejected=[]),
            unblocked=None,
        )

    monkeypatch.setattr(cli_blocklist, "execute_apply", _execute_apply)

    # 998 + 997 are well below the ten-id rail, so the rail is irrelevant;
    # what we pin is that --prune routes the unblock through execute_apply
    # and prompt_unblock is never asked.
    result = runner.invoke(app, ["blocklist", "apply", "--prune", "--force"])

    assert result.exit_code == 0, result.output
    assert prompt_calls == [], "force must skip the unblock prompt"
    assert unblock_calls == [["998", "997"]], "execute_apply must handle the unblock under --prune"


def test_an_oversized_prune_is_refused_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tidal_sync.engine.curation import MAX_APPLY_IDS

    written: list[list[str]] = []

    monkeypatch.setattr(
        cli_blocklist, "load_subscriptions", lambda: [fake_subscription(name="spam")]
    )
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[],
            already_blocked=[],
            unlisted=[(str(300000 + i), "") for i in range(MAX_APPLY_IDS + 1)],
            errors=[],
        )

    async def _apply_per_id(ids: list[str], action: object, label: str) -> UploadOutcome:
        written.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    # Fake the per-id writer and leave unblock_artists real, so the
    # guard under test stays the shipped one. Patching the verb would
    # stub it away.
    monkeypatch.setattr(curation, "_apply_per_id", _apply_per_id)

    # execute_apply stays real, so the whole prune path runs as shipped.
    result = runner.invoke(app, ["blocklist", "apply", "--prune", "--force"])

    assert result.exit_code == 1, result.output
    assert written == [], f"an oversized prune must write nothing, got {len(written)} calls"
    assert "could not complete" in result.output
    assert str(MAX_APPLY_IDS) in result.output


def test_an_oversized_second_batch_blocks_the_first_one_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tidal_sync.engine.curation import MAX_APPLY_IDS

    written: list[list[str]] = []

    monkeypatch.setattr(
        cli_blocklist, "load_subscriptions", lambda: [fake_subscription(name="spam")]
    )
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

    async def _plan_apply(session: object, subs: list[Subscription], **_: object) -> ApplyPlan:
        return ApplyPlan(
            to_block=[("111", "Alpha")],
            already_blocked=[],
            unlisted=[(str(400000 + i), "") for i in range(MAX_APPLY_IDS + 1)],
            errors=[],
        )

    async def _apply_per_id(ids: list[str], action: object, label: str) -> UploadOutcome:
        written.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_blocklist, "plan_apply", _plan_apply)
    monkeypatch.setattr(curation, "_apply_per_id", _apply_per_id)

    result = runner.invoke(app, ["blocklist", "apply", "--prune", "--force"])

    assert result.exit_code == 1, result.output
    assert written == [], (
        f"the small block batch must not go out ahead of the oversized one, got {written}"
    )
    assert "could not complete" in result.output


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

    monkeypatch.setattr(
        cli_blocklist, "load_subscriptions", lambda: [fake_subscription(name="spam")]
    )
    monkeypatch.setattr(cli_blocklist, "get_session", lambda profile="default": FakeSession())

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

    async def _fetch_blocked_ids(session: object) -> list[tuple[str, str]]:
        return []

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked_ids)
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

    Mirrors the real function: block_artists, then optional
    unblock_artists. Returns ``ApplyOutcome`` so the CLI's print and
    exit logic runs as in production.
    """

    async def _execute_apply(
        session: object, plan: ApplyPlan, *, unblock_ids: list[str]
    ) -> ApplyOutcome:
        from tidal_sync.domain.results import UploadOutcome as _UO

        blocked = None
        if plan.to_block:
            blocked = _UO(applied=[tid for tid, _ in plan.to_block], rejected=[])
            box["block_calls"].append([tid for tid, _ in plan.to_block])
        unblocked = None
        if unblock_ids:
            unblocked = _UO(applied=list(unblock_ids), rejected=[])
            box["unblock_calls"].append(list(unblock_ids))
        return ApplyOutcome(blocked=blocked, unblocked=unblocked)

    return _execute_apply
