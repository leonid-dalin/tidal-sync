"""Tests for the filter-list apply engine.

All tests monkeypatch ``fetch_source``, ``fetch_blocked_artists_named``,
``block_artists`` and ``unblock_artists`` so no real network or write
ever happens. The engine is async and awaits those verbs, so every
test here is ``async def`` to avoid the silent-no-op trap a sync test
would hit when calling an async function.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine import filterlist_apply, filterlist_store
from tidal_sync.engine.filterlist_apply import (
    MAX_APPLY_IDS,
    ApplyPlan,
    execute_apply,
    plan_apply,
)
from tidal_sync.engine.filterlist_store import Subscription


@pytest.fixture(autouse=True)
def store_dir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point STORE_DIR at a fresh per-test directory so the real user cache is never touched."""
    target = tmp_path / "filter_lists"
    monkeypatch.setattr(filterlist_store, "STORE_DIR", target)
    return target


def _fresh_iso(now: datetime, hours_ago: int = 1) -> str:
    return (now - timedelta(hours=hours_ago)).isoformat()


def _stale_iso(now: datetime, hours_ago: int = 99) -> str:
    return (now - timedelta(hours=hours_ago)).isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sub(
    name: str,
    *,
    source: str | None = None,
    fmt: str = "txt",
    ttl_hours: int = 24,
    last_fetched: str | None = None,
) -> Subscription:
    return Subscription(
        name=name,
        source=source or f"https://example.test/{name}",
        format=fmt,
        ttl_hours=ttl_hours,
        last_fetched=last_fetched,
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Tests for plan_apply
# ---------------------------------------------------------------------------


async def test_plan_apply_makes_no_writes(
    monkeypatch: pytest.MonkeyPatch, now: datetime, store_dir
):
    """``plan_apply`` is a pure function: it never calls the curation verbs."""
    subs = [_sub("a", last_fetched=_fresh_iso(now, hours_ago=1))]
    parse = {"a": [("1", "alpha"), ("2", "beta")]}

    monkeypatch.setattr(filterlist_apply, "_now_iso", lambda: now.isoformat())

    cached = store_dir / "cache"
    cached.mkdir(parents=True, exist_ok=True)
    (cached / "a.txt").write_bytes(b"SUB::a")

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return None

    r_block = _R()
    r_unblock = _R()

    async def _fetch_blocked(session):
        return []

    fetch_calls: dict[str, int] = {}

    def _fetch(source, fmt, dest):
        fetch_calls[dest.stem] = 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"SUB::{dest.stem}".encode())
        return 2

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", r_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", r_unblock)

    def _parse(data, fmt):
        return list(parse.get("a" if data == b"SUB::a" else "", []))

    monkeypatch.setattr(filterlist_apply, "parse_filter_list", _parse)

    plan = await plan_apply("session", subs)

    assert isinstance(plan, ApplyPlan)
    assert plan.to_block == [("1", "alpha"), ("2", "beta")]
    assert r_block.calls == []
    assert r_unblock.calls == []
    assert fetch_calls == {}


async def test_fetch_error_recorded_but_sibling_continues(
    monkeypatch: pytest.MonkeyPatch, now: datetime
):
    from tidal_sync.engine.filterlist_fetch import FetchError

    subs = [
        _sub("good", last_fetched=_stale_iso(now, hours_ago=99)),
        _sub("bad", last_fetched=_stale_iso(now, hours_ago=99)),
    ]
    parse = {"good": [("10", "ten")]}

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return None

    r_block = _R()
    r_unblock = _R()

    fetch_calls: dict[str, int] = {}

    def _fetch(source, fmt, dest):
        name = dest.stem
        if name == "bad":
            raise FetchError("boom")
        fetch_calls[name] = fetch_calls.get(name, 0) + 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"SUB::{name}".encode())
        return len(parse.get(name, []))

    async def _fetch_blocked(session):
        return []

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", r_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", r_unblock)

    def _parse(data, fmt):
        if data == b"SUB::good":
            return list(parse["good"])
        return []

    monkeypatch.setattr(filterlist_apply, "parse_filter_list", _parse)

    plan = await plan_apply("session", subs)

    assert plan.errors == [("bad", "boom")]
    assert plan.to_block == [("10", "ten")]


async def test_same_id_in_two_lists_appears_once(monkeypatch: pytest.MonkeyPatch, now: datetime):
    subs = [
        _sub("a", last_fetched=_stale_iso(now, hours_ago=99)),
        _sub("b", last_fetched=_stale_iso(now, hours_ago=99)),
    ]
    parse = {
        "a": [("1", "one"), ("2", "two")],
        "b": [("2", "two"), ("3", "three")],
    }

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return None

    r_block = _R()

    def _fetch(source, fmt, dest):
        name = dest.stem
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"SUB::{name}".encode())
        return len(parse.get(name, []))

    async def _fetch_blocked(session):
        return []

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", r_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", _R())

    def _parse(data, fmt):
        if data == b"SUB::a":
            return list(parse["a"])
        if data == b"SUB::b":
            return list(parse["b"])
        return []

    monkeypatch.setattr(filterlist_apply, "parse_filter_list", _parse)

    plan = await plan_apply("session", subs)

    ids = [pair[0] for pair in plan.to_block]
    assert sorted(ids) == ["1", "2", "3"]
    assert ids.count("2") == 1


async def test_stale_refetched_fresh_skipped(
    monkeypatch: pytest.MonkeyPatch, now: datetime, tmp_path
):
    fresh_ts = _fresh_iso(now, hours_ago=1)
    stale_ts = _stale_iso(now, hours_ago=99)
    subs = [
        _sub("fresh", last_fetched=fresh_ts),
        _sub("stale", last_fetched=stale_ts),
        _sub("never"),
    ]
    parse = {"fresh": [], "stale": [("5", "five")], "never": [("7", "seven")]}

    monkeypatch.setattr(filterlist_apply, "_now_iso", lambda: now.isoformat())

    cached = tmp_path / "filter_lists" / "cache"
    cached.mkdir(parents=True, exist_ok=True)
    (cached / "fresh.txt").write_bytes(b"SUB::fresh")

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return None

    r_block = _R()

    fetch_calls: dict[str, int] = {}

    def _fetch(source, fmt, dest):
        name = dest.stem
        fetch_calls[name] = fetch_calls.get(name, 0) + 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"SUB::{name}".encode())
        return len(parse.get(name, []))

    async def _fetch_blocked(session):
        return []

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", r_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", _R())

    def _parse(data, fmt):
        for name, pairs in parse.items():
            if data == f"SUB::{name}".encode():
                return list(pairs)
        return []

    monkeypatch.setattr(filterlist_apply, "parse_filter_list", _parse)

    plan = await plan_apply("session", subs)

    assert "fresh" not in fetch_calls
    assert fetch_calls["stale"] == 1
    assert fetch_calls["never"] == 1
    assert plan.to_block == [("5", "five"), ("7", "seven")]


async def test_already_blocked_separated(monkeypatch: pytest.MonkeyPatch, now: datetime):
    subs = [_sub("a", last_fetched=_stale_iso(now, hours_ago=99))]
    parse = {"a": [("1", "alpha"), ("2", "beta"), ("3", "gamma")]}

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return None

    r_block = _R()

    def _fetch(source, fmt, dest):
        name = dest.stem
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"SUB::{name}".encode())
        return len(parse.get(name, []))

    async def _fetch_blocked(session):
        # "2" is already on the live blocklist.
        return [("2", "")]

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", r_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", _R())

    def _parse(data, fmt):
        if data == b"SUB::a":
            return list(parse["a"])
        return []

    monkeypatch.setattr(filterlist_apply, "parse_filter_list", _parse)

    plan = await plan_apply("session", subs)

    assert plan.already_blocked == [("2", "beta")]
    assert plan.to_block == [("1", "alpha"), ("3", "gamma")]


async def test_unlisted_computed(monkeypatch: pytest.MonkeyPatch, now: datetime):
    subs = [_sub("a", last_fetched=_stale_iso(now, hours_ago=99))]
    parse = {"a": [("1", "alpha")]}

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return None

    r_unblock = _R()

    def _fetch(source, fmt, dest):
        name = dest.stem
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"SUB::{name}".encode())
        return len(parse.get(name, []))

    async def _fetch_blocked(session):
        # "99" is blocked but not in the subscription list.
        return [("99", "")]

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", _R())
    monkeypatch.setattr(filterlist_apply, "unblock_artists", r_unblock)

    def _parse(data, fmt):
        if data == b"SUB::a":
            return list(parse["a"])
        return []

    monkeypatch.setattr(filterlist_apply, "parse_filter_list", _parse)

    plan = await plan_apply("session", subs)

    assert plan.unlisted == [("99", "")]
    assert r_unblock.calls == []


async def test_unlisted_carries_artist_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unblock prompt is the one place a name matters most.

    An operator deciding what to stop blocking cannot do it from bare
    numeric ids, and the blocklist read already returns artist objects.
    """

    async def _blocked(session: object) -> list[tuple[str, str]]:
        return [("4894212", "Bad Bunny"), ("8107285", "Rosalia")]

    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _blocked)

    plan = await filterlist_apply.plan_apply(object(), [])

    assert plan.unlisted == [("4894212", "Bad Bunny"), ("8107285", "Rosalia")]


async def test_empty_subscriptions_yields_empty_plan(monkeypatch: pytest.MonkeyPatch):
    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return None

    r_block = _R()
    r_unblock = _R()

    async def _fetch_blocked(session):
        return []

    monkeypatch.setattr(filterlist_apply, "fetch_source", _R())
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", r_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", r_unblock)
    monkeypatch.setattr(filterlist_apply, "parse_filter_list", lambda d, f: [])

    plan = await plan_apply("session", [])

    assert plan.to_block == []
    assert plan.already_blocked == []
    assert plan.unlisted == []
    assert plan.errors == []
    assert r_block.calls == []
    assert r_unblock.calls == []


# ---------------------------------------------------------------------------
# Tests for execute_apply
# ---------------------------------------------------------------------------


async def test_prune_true_calls_unblock(monkeypatch: pytest.MonkeyPatch):
    """``execute_apply(plan, prune=True)`` calls ``unblock_artists`` on ``plan.unlisted``."""
    plan = ApplyPlan(
        to_block=[],
        already_blocked=[],
        unlisted=[("50", "")],
        errors=[],
    )

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return UploadOutcome(applied=list(ids), rejected=[])

    r_unblock = _R()

    monkeypatch.setattr(filterlist_apply, "unblock_artists", r_unblock)

    outcome = await execute_apply("session", plan, prune=True)

    assert r_unblock.calls == [["50"]]
    assert outcome.unblocked is not None
    assert outcome.unblocked.applied == ["50"]
    assert outcome.capped is False


async def test_prune_false_skips_unblock(monkeypatch: pytest.MonkeyPatch):
    """``execute_apply(plan, prune=False)`` never calls ``unblock_artists``."""
    plan = ApplyPlan(
        to_block=[],
        already_blocked=[],
        unlisted=[("50", "")],
        errors=[],
    )

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return UploadOutcome(applied=list(ids), rejected=[])

    r_unblock = _R()

    monkeypatch.setattr(filterlist_apply, "unblock_artists", r_unblock)

    outcome = await execute_apply("session", plan, prune=False)

    assert r_unblock.calls == []
    assert outcome.unblocked is None


async def test_max_apply_ids_aborts_without_writing(monkeypatch: pytest.MonkeyPatch):
    """A plan exceeding ``MAX_APPLY_IDS`` returns ``capped=True`` and issues no writes."""
    plan = ApplyPlan(
        to_block=[(str(i), f"name{i}") for i in range(MAX_APPLY_IDS + 1)],
        already_blocked=[],
        unlisted=[],
        errors=[],
    )

    class _R:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def __call__(self, session, ids):
            self.calls.append(list(ids))
            return UploadOutcome(applied=list(ids), rejected=[])

    r_block = _R()
    r_unblock = _R()

    monkeypatch.setattr(filterlist_apply, "block_artists", r_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", r_unblock)

    outcome = await execute_apply("session", plan, prune=True)

    assert outcome.capped is True
    assert outcome.blocked is None
    assert outcome.unblocked is None
    assert r_block.calls == []
    assert r_unblock.calls == []


# ---------------------------------------------------------------------------
# Tests for engine isolation and naive-timestamp handling (Task 13).
#
# Background: the fresh-cache branch used to read and parse outside the
# per-subscription try, so a missing cache file (deleted between the fetch
# that recorded last_fetched and this read) or a bad parse crashed the
# whole run. ``_is_stale`` also only caught ``ValueError``; a naive
# ``last_fetched`` makes ``now - last`` raise ``TypeError`` and killed the
# run the same way.
# ---------------------------------------------------------------------------


async def test_a_missing_cache_file_is_a_recorded_error_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, now: datetime, tmp_path
) -> None:
    """One subscription's missing cache must not stop the others.

    ``gone`` claims to be fresh but its cache file is gone; ``good`` is
    also fresh with a real cache. The whole run must complete with
    ``gone`` reported as an error and ``good`` contributing to the plan.
    """
    fresh_ts = _fresh_iso(now, hours_ago=1)
    subs = [
        _sub("gone", last_fetched=fresh_ts),
        _sub("good", last_fetched=fresh_ts),
    ]

    monkeypatch.setattr(filterlist_apply, "_now_iso", lambda: now.isoformat())

    # The "good" subscription has a real cache; "gone" does not. The
    # autouse ``store_dir`` fixture already pointed STORE_DIR at
    # tmp_path/filter_lists, so we write the real cache there.
    cache_dir = tmp_path / "filter_lists" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "good.txt").write_bytes(b"SUB::good")

    parse = {"good": [("10", "ten")]}

    def _fetch(source: str, fmt: str, dest: Path) -> int:
        # No sub is stale here, so fetch_source must never be called.
        raise AssertionError("fetch_source must not run when every sub is fresh")

    async def _fetch_blocked(session: object) -> list[str]:
        return []

    monkeypatch.setattr(filterlist_apply, "fetch_source", _fetch)
    monkeypatch.setattr(filterlist_apply, "fetch_blocked_artists_named", _fetch_blocked)
    monkeypatch.setattr(filterlist_apply, "block_artists", _noop_block)
    monkeypatch.setattr(filterlist_apply, "unblock_artists", _noop_block)

    def _parse(data: bytes, fmt: str) -> list[tuple[str, str]]:
        if data == b"SUB::good":
            return list(parse["good"])
        return []

    monkeypatch.setattr(filterlist_apply, "parse_filter_list", _parse)

    plan = await plan_apply("session", subs)

    assert len(plan.errors) == 1
    assert plan.errors[0][0] == "gone"
    assert "good" not in {name for name, _err in plan.errors}
    assert plan.to_block == [("10", "ten")]


def _noop_block(session: object, ids: list[str]):  # noqa: ANN001
    async def _call() -> UploadOutcome:
        return UploadOutcome(applied=list(ids), rejected=[])

    return _call()


def test_a_naive_last_fetched_is_treated_as_stale() -> None:
    """A timestamp without a timezone must not raise TypeError.

    ``_is_stale`` previously caught only ``ValueError``; the subtraction
    of a naive ``last`` from a tz-aware ``now`` raises ``TypeError``.
    That exception escaped ``_is_stale`` and crashed the whole run.
    """
    sub = Subscription(
        name="n",
        source="./n.txt",
        format="txt",
        last_fetched="2026-09-01T10:00:00",
    )
    # Use a fixed now string with a tz so the only mismatch is the naive
    # ``last_fetched``: pre-fix, ``now - last`` raised TypeError.
    assert filterlist_apply._is_stale(sub, "2026-09-02T10:00:00+00:00") is True
