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

"""Apply engine for filter-list subscriptions.

Given a set of subscriptions, compute the union of their ids, the
set of ids already blocked on the live blocklist, and the set of
ids blocked on the live blocklist but named by no subscription.

The decision about what to unblock lives at this layer, not in the
CLI: this module owns the "what does prune mean" invariant. The CLI
only decides whether to ask the operator to invoke this engine.

Layering: ``plan_apply`` is pure and never touches the network
beyond reading the live blocklist. The confirmation rail sits
between the planner and the writer so a declined prompt issues
zero block writes. The writer is ``execute_apply`` and accepts a
plan; it owns the cap check and the calls to ``block_artists`` and
``unblock_artists``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import tidalapi

from ..domain.results import UploadOutcome

# Imported here so tests can monkeypatch by attribute. The real
# implementations are used unchanged; nothing in this module redefines
# the curation verbs or ``parse_filter_list``.
from .curation import (
    block_artists,
    fetch_blocked_artists_named,
    unblock_artists,
)
from .filterlist import FormatError, parse_filter_list
from .filterlist_fetch import FetchError, fetch_source
from .filterlist_store import Subscription, cache_path


def now_iso() -> str:
    """The current UTC time as an ISO string.

    Lives in the engine because ``_is_stale`` is the only caller that
    reasons about the value; the CLI merely stamps a record with it and
    reaches down for it, which is the direction the layering allows.
    A module-level function so tests can monkeypatch a deterministic
    clock without the engine importing time itself.
    """
    return datetime.now(UTC).isoformat()


@dataclass
class ApplyPlan:
    """The sets the apply engine computes from subscriptions and a live blocklist.

    Every id is paired with its display name so the CLI can print
    names rather than just numbers.
    """

    to_block: list[tuple[str, str]]
    already_blocked: list[tuple[str, str]]
    unlisted: list[tuple[str, str]]
    errors: list[tuple[str, str]]


@dataclass
class ApplyOutcome:
    """The result of running ``execute_apply`` against a plan.

    ``blocked`` and ``unblocked`` carry the engine's per-id
    classification. The batch ceiling is enforced at the single
    write leaf and raises rather than returning a partial outcome,
    so there is no ``capped`` field to set.
    """

    blocked: UploadOutcome | None
    unblocked: UploadOutcome | None


def _is_stale(sub: Subscription, now_iso: str) -> bool:
    """A list is stale if it was never fetched or its ``last_fetched`` is older than ``ttl_hours``.

    A missing or unparseable ``last_fetched`` is treated as never
    fetched, so a freshly added subscription refetches on its first
    run rather than silently joining with zero rows. A naive
    ``last_fetched`` (no timezone) is treated as stale for the same
    reason: the subtraction of naive from aware raises ``TypeError``
    and the run cannot continue.
    """
    if sub.last_fetched is None:
        return True
    try:
        last = datetime.fromisoformat(sub.last_fetched)
        now = datetime.fromisoformat(now_iso)
        delta_hours = (now - last).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return True
    return delta_hours >= sub.ttl_hours


async def plan_apply(
    session: tidalapi.Session,
    subscriptions: list[Subscription],
) -> ApplyPlan:
    """Compute the apply plan from subscriptions and the live blocklist.

    Issues no Tidal write. It reads the live blocklist and may fetch
    stale subscription sources over HTTP. Every mutation, including
    the cap, the ``block_artists`` call and the ``unblock_artists``
    call, lives in ``execute_apply``. Splitting them keeps the rail in
    the CLI honest: there is no path through the engine that bypasses
    the CLI's confirmation prompt.
    """
    stamp = now_iso()

    errors: list[tuple[str, str]] = []
    union_pairs: list[tuple[str, str]] = []

    # Steps 1 and 2. One bad list never stops the others. The fresh
    # branch shares this try with the stale one: a cache file can be
    # deleted between the fetch that recorded ``last_fetched`` and this
    # read, so missing-file and bad-parse errors must be recorded
    # against the subscription rather than crashing the run.
    for sub in subscriptions:
        try:
            dest = cache_path(sub.name, sub.format)
            if _is_stale(sub, stamp):
                fetch_source(sub.source, sub.format, dest)
            pairs = parse_filter_list(dest.read_bytes(), sub.format)
        except (FetchError, FormatError, OSError, ValueError) as exc:
            errors.append((sub.name, str(exc)))
            continue
        union_pairs.extend(pairs)

    # Deduplicate across lists. Order matches first appearance; later
    # duplicates with the same id but a different name keep the first
    # name, since names from two sources do not have a meaningful
    # ordering.
    name_for_id: dict[str, str] = {}
    order: list[str] = []
    for tidal_id, name in union_pairs:
        if tidal_id not in name_for_id:
            name_for_id[tidal_id] = name
            order.append(tidal_id)

    # Step 3. The live blocklist is the strict variant, so a failed
    # read propagates rather than returning empty. The named variant
    # is the same read but keeps the artist names; the unblock prompt
    # cannot ask a useful question from bare ids.
    order_of_blocked = await fetch_blocked_artists_named(session)
    blocked_names: dict[str, str] = {bid: name for bid, name in order_of_blocked}
    blocked_set = set(blocked_names)

    # Step 4. Partition into the three sets the CLI will print. An id
    # present in both the subscription union and the blocklist keeps
    # the subscription's name where available, and falls back to the
    # blocklist's name when the subscription did not carry one, so the
    # operator still sees something rather than a bare id.
    to_block: list[tuple[str, str]] = []
    already_blocked: list[tuple[str, str]] = []
    for tidal_id in order:
        if tidal_id in blocked_set:
            name = name_for_id[tidal_id] or blocked_names.get(tidal_id, "")
            already_blocked.append((tidal_id, name))
        else:
            to_block.append((tidal_id, name_for_id[tidal_id]))

    union_id_set = set(order)
    unlisted: list[tuple[str, str]] = [
        (bid, blocked_names.get(bid, ""))
        for bid, _name in order_of_blocked
        if bid not in union_id_set
    ]

    return ApplyPlan(
        to_block=to_block,
        already_blocked=already_blocked,
        unlisted=unlisted,
        errors=errors,
    )


async def execute_apply(
    session: tidalapi.Session,
    plan: ApplyPlan,
    *,
    unblock_ids: list[str],
) -> ApplyOutcome:
    """Apply a previously computed ``ApplyPlan`` to Tidal.

    The batch ceiling is enforced inside ``block_artists`` itself, so
    exceeding it raises ``BatchTooLarge`` here rather than returning a
    capped outcome.

    ``unblock_ids`` is the caller's decision, not the engine's. Passing an
    explicit list rather than a ``prune`` flag is what makes "this engine
    never unblocks on its own initiative" a property of the signature
    instead of a comment: there is no value of any argument that makes it
    choose.
    """
    blocked: UploadOutcome | None = None
    if plan.to_block:
        blocked = await block_artists(session, [pair[0] for pair in plan.to_block])

    unblocked: UploadOutcome | None = None
    if unblock_ids:
        unblocked = await unblock_artists(session, unblock_ids)

    return ApplyOutcome(blocked=blocked, unblocked=unblocked)
