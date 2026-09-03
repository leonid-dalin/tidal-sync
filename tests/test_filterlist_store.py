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
    fmt: str = "txt",
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
        fmt="txt",
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


def test_load_on_corrupt_file_raises_store_error(store_dir: Path):
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "subscriptions.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(filterlist_store.StoreError):
        load_subscriptions()


def test_a_corrupt_index_refuses_rather_than_reading_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed read must not look like an empty store.

    Reading empty means the next add writes an index of one record and
    every existing subscription is gone.
    """
    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path)
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "subscriptions.json").write_bytes(b"{ this is not json")

    with pytest.raises(filterlist_store.StoreError):
        filterlist_store.load_subscriptions()


def test_a_corrupt_index_is_not_overwritten_by_add(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """add must refuse rather than replace an index it could not read."""
    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    index = tmp_path / "subscriptions.json"
    index.write_bytes(b"{ this is not json")

    with pytest.raises(filterlist_store.StoreError):
        filterlist_store.add_subscription(
            filterlist_store.Subscription(name="new", source="./x.txt", format="txt")
        )
    assert index.read_bytes() == b"{ this is not json"


def test_a_malformed_record_is_a_store_error_not_a_key_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record missing a field must not surface as a bare KeyError."""
    monkeypatch.setattr(filterlist_store, "STORE_DIR", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "subscriptions.json").write_bytes(b'[{"source": "./x.txt"}]')

    with pytest.raises(filterlist_store.StoreError):
        filterlist_store.load_subscriptions()


def test_a_format_from_the_index_cannot_escape_the_cache_dir() -> None:
    """format is validated, not just name; both reach the cache path."""
    with pytest.raises(ValueError):
        filterlist_store.cache_path("ok", "../../../evil")


def test_save_subscriptions_persists_directly(store_dir: Path):
    subs = [_sub(name="a"), _sub(name="b", source="https://b.example")]
    save_subscriptions(subs)
    assert load_subscriptions() == subs


def test_name_with_slash_is_rejected(store_dir: Path):
    with pytest.raises(ValueError):
        add_subscription(_sub(name="bad/name"))


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
