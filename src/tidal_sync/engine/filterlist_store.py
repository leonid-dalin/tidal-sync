# tidal-sync: A high-performance tool for backing up and cloning Tidal libraries.
# Copyright (C) 2026 Leonid Dalin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: infoLeonid@protonMail.com
"""Subscription store for filter lists.

Holds the index of subscribed filter lists and the cached copy of each
fetched list. Lives in its own directory under the config dir so the
profile scanner in auth.py (which globs ``*.json`` for files carrying a
``user_id``) cannot mistake a subscription record for a Tidal token.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import orjson

from tidal_sync.auth import CONFIG_DIR
from tidal_sync.engine.filterlist import SUPPORTED_FORMATS

STORE_DIR: Path = CONFIG_DIR / "filter_lists"
_SUBSCRIPTIONS_FILE: str = "subscriptions.json"
_CACHE_DIR: str = "cache"

# Mirrors the discipline used by auth._PROFILE_NAME_RE so a subscription
# name cannot escape its directory with ``..`` or a separator.
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


class StoreError(Exception):
    """Raised when the on-disk subscription store cannot be trusted.

    The invariant this class protects: a read that failed is not an
    empty store. Returning ``[]`` on a decode error would let the next
    ``add_subscription`` overwrite the file with an index of one record
    and silently destroy every subscription on disk.
    """


@dataclass
class Subscription:
    """One entry in the filter-list index."""

    name: str
    source: str
    format: str
    last_fetched: str | None = None
    last_count: int = 0
    last_error: str | None = None
    ttl_hours: int = 24

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subscription:
        try:
            name = data["name"]
            source = data["source"]
            fmt = data["format"]
        except (KeyError, TypeError) as exc:
            raise StoreError(f"malformed subscription record: {data!r}") from exc
        _validate_format(fmt)
        return cls(
            name=name,
            source=source,
            format=fmt,
            last_fetched=data.get("last_fetched"),
            last_count=int(data.get("last_count", 0)),
            last_error=data.get("last_error"),
            ttl_hours=int(data.get("ttl_hours", 24)),
        )


def _validate_name(name: str) -> None:
    """Reject names that could escape the store directory."""
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid subscription name {name!r}. "
            "Use 1-64 characters: letters, digits, '_', '-' or '.', "
            "and do not start with '.'."
        )


def _validate_format(fmt: object) -> None:
    """Reject formats outside the allowlist.

    ``format`` reaches the cache path the same way ``name`` does, so it
    must be checked just as strictly. Membership in
    ``SUPPORTED_FORMATS`` is the gate; anything else (including a
    hand-edited traversal) raises.
    """
    if not isinstance(fmt, str) or fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported filter-list format {fmt!r}. Expected one of {SUPPORTED_FORMATS}."
        )


def _ensure_dir(path: Path) -> None:
    # POSIX-only permission. Windows ignores the mode argument, which is
    # acceptable: the brief explicitly allows that fallback.
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def store_index_path() -> Path:
    return STORE_DIR / _SUBSCRIPTIONS_FILE


def _read_index() -> list[dict[str, Any]]:
    path = store_index_path()
    if not path.exists():
        # A missing file is genuinely an empty store. Anything else
        # means the read failed, which must not be conflated with
        # reading nothing.
        return []
    try:
        data = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise StoreError(f"subscription index is not valid JSON: {path} ({exc})") from exc
    except OSError as exc:
        raise StoreError(f"subscription index could not be read: {path} ({exc})") from exc
    if not isinstance(data, list):
        raise StoreError(
            f"subscription index is not a JSON array: {path} "
            f"(top-level type was {type(data).__name__})"
        )
    return data


def _write_index(records: list[dict[str, Any]]) -> None:
    _ensure_dir(STORE_DIR)
    target = store_index_path()
    temp = target.with_name(target.name + ".part")
    try:
        with open(temp, "wb") as f:
            f.write(orjson.dumps(records, option=orjson.OPT_INDENT_2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def load_subscriptions() -> list[Subscription]:
    """Return every subscription on disk, in insertion order."""
    return [Subscription.from_dict(item) for item in _read_index()]


def save_subscriptions(subs: list[Subscription]) -> None:
    """Persist the given list, replacing whatever was on disk."""
    for sub in subs:
        _validate_name(sub.name)
    _write_index([asdict(sub) for sub in subs])


def add_subscription(sub: Subscription) -> None:
    """Insert or replace a subscription by name."""
    _validate_name(sub.name)
    _validate_format(sub.format)
    records = _read_index()
    for i, existing in enumerate(records):
        if existing.get("name") == sub.name:
            records[i] = asdict(sub)
            _write_index(records)
            return
    records.append(asdict(sub))
    _write_index(records)


def remove_subscription(name: str) -> bool:
    """Drop a subscription by name. Returns True if a record was removed."""
    records = _read_index()
    kept = [item for item in records if item.get("name") != name]
    if len(kept) == len(records):
        return False
    _write_index(kept)
    return True


def cache_path(name: str, fmt: str) -> Path:
    """Path where the cached copy of a subscription is written."""
    _validate_name(name)
    _validate_format(fmt)
    return STORE_DIR / _CACHE_DIR / f"{name}.{fmt}"
