"""Tests for the filter-list subscription store.

Every test monkeypatches STORE_DIR to a tmp_path so the real
~/.tidal_sync/filter_lists/ directory is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tidal_sync.engine import filterlist_store
from tidal_sync.engine.filterlist_store import (
    Subscription,
    add_subscription,
    cache_path,
    load_subscriptions,
    remove_subscription,
    save_subscriptions,
)


@pytest.fixture
def store_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point STORE_DIR at a fresh per-test directory."""
    target = tmp_path / "filter_lists"
    monkeypatch.setattr(filterlist_store, "STORE_DIR", target)
    return target


def _sub(
    name: str = "default",
    source: str = "https://example.com/list.txt",
    fmt: str = "plain",
    last_fetched: str | None = None,
    last_count: int = 0,
    last_error: str | None = None,
    ttl_hours: int = 24,
) -> Subscription:
    return Subscription(
        name=name,
        source=source,
        format=fmt,
        last_fetched=last_fetched,
        last_count=last_count,
        last_error=last_error,
        ttl_hours=ttl_hours,
    )


def test_add_then_load_returns_same_record(store_dir: Path):
    add_subscription(_sub(name="one", source="https://a.example/list"))
    loaded = load_subscriptions()
    assert len(loaded) == 1
    assert loaded[0].name == "one"
    assert loaded[0].source == "https://a.example/list"


def test_every_field_survives_round_trip(store_dir: Path):
    original = _sub(
        name="full",
        source="https://b.example/list",
        fmt="dnsmasq",
        last_fetched="2026-09-02T12:00:00Z",
        last_count=42,
        last_error=None,
        ttl_hours=6,
    )
    add_subscription(original)
    loaded = load_subscriptions()
    assert loaded == [original]


def test_last_fetched_none_survives_round_trip(store_dir: Path):
    add_subscription(_sub(name="fresh", last_fetched=None))
    loaded = load_subscriptions()
    assert loaded[0].last_fetched is None


def test_adding_existing_name_replaces_it(store_dir: Path):
    add_subscription(_sub(name="dup", source="https://first.example"))
    add_subscription(_sub(name="dup", source="https://second.example"))
    loaded = load_subscriptions()
    assert len(loaded) == 1
    assert loaded[0].source == "https://second.example"


def test_remove_returns_true_and_drops_record(store_dir: Path):
    add_subscription(_sub(name="bye"))
    assert remove_subscription("bye") is True
    assert load_subscriptions() == []


def test_remove_unknown_returns_false(store_dir: Path):
    assert remove_subscription("never-added") is False


def test_load_on_missing_file_returns_empty_list(store_dir: Path):
    assert not store_dir.exists()
    assert load_subscriptions() == []


def test_load_on_corrupt_file_returns_empty_list(store_dir: Path):
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "subscriptions.json").write_text("{not valid json", encoding="utf-8")
    assert load_subscriptions() == []


def test_save_subscriptions_persists_directly(store_dir: Path):
    subs = [_sub(name="a"), _sub(name="b", source="https://b.example")]
    save_subscriptions(subs)
    assert load_subscriptions() == subs


def test_name_with_slash_is_rejected(store_dir: Path):
    with pytest.raises(ValueError):
        add_subscription(_sub(name="bad/name"))


def test_name_with_double_dot_is_rejected(store_dir: Path):
    with pytest.raises(ValueError):
        add_subscription(_sub(name="bad..name"))


def test_name_with_leading_dot_is_rejected(store_dir: Path):
    with pytest.raises(ValueError):
        add_subscription(_sub(name=".hidden"))


def test_store_dir_is_created_on_add(store_dir: Path):
    assert not store_dir.exists()
    add_subscription(_sub(name="bootstrap"))
    assert store_dir.is_dir()


def test_cache_path_joins_under_cache_dir(store_dir: Path):
    path = cache_path("a", "txt")
    assert path == store_dir / "cache" / "a.txt"
    assert path.parent == store_dir / "cache"


def test_cache_path_extension_follows_format(store_dir: Path):
    assert cache_path("list-one", "txt").name == "list-one.txt"
    assert cache_path("list-two", "csv").name == "list-two.csv"


def test_atomic_write_uses_part_sibling(store_dir: Path):
    add_subscription(_sub(name="atomic"))
    # The .part sibling is cleaned up on success: no leftover.
    leftover = list(store_dir.glob("*.part"))
    assert leftover == []


def test_written_file_is_valid_json(store_dir: Path):
    add_subscription(_sub(name="jsoncheck", source="https://x.example"))
    raw = (store_dir / "subscriptions.json").read_text(encoding="utf-8")
    decoded = json.loads(raw)
    assert isinstance(decoded, list)
    assert decoded[0]["name"] == "jsoncheck"


def test_remove_does_not_create_directory(store_dir: Path):
    assert remove_subscription("nope") is False
    # The store must stay missing: nothing was written.
    assert not store_dir.exists()
