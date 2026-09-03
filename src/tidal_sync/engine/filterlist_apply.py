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
from .curation import block_artists, fetch_blocked_artist_ids, unblock_artists
from .filterlist import parse_filter_list
from .filterlist_fetch import FetchError, fetch_source
from .filterlist_store import Subscription, cache_path

# The largest batch we will ever push in a single apply run. Exceeding
# the cap aborts the whole run; we never block a truncated set.
MAX_APPLY_IDS: int = 5000


def _now_iso() -> str:
    """The current UTC time as an ISO string.

    A module-level helper so tests can monkeypatch a deterministic
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
    classification. ``capped`` is set when the plan exceeded the
    batch ceiling; in that case neither field is populated because
    the run aborted before any write.
    """

    blocked: UploadOutcome | None
    unblocked: UploadOutcome | None
    capped: bool = False


def _is_stale(sub: Subscription, now_iso: str) -> bool:
    """A list is stale if it was never fetched or its ``last_fetched`` is older than ``ttl_hours``.

    A missing or unparseable ``last_fetched`` is treated as never
    fetched, so a freshly added subscription refetches on its first
    run rather than silently joining with zero rows.
    """
    if sub.last_fetched is None:
        return True
    try:
        from datetime import datetime

        last = datetime.fromisoformat(sub.last_fetched)
        now = datetime.fromisoformat(now_iso)
        delta_hours = (now - last).total_seconds() / 3600.0
    except ValueError:
        return True
    return delta_hours >= sub.ttl_hours


async def plan_apply(
    session: tidalapi.Session,
    subscriptions: list[Subscription],
) -> ApplyPlan:
    """Compute the apply plan from subscriptions and the live blocklist.

    Pure: the only network read is the live blocklist, and no Tidal
    write is ever issued from here. The cap, the ``block_artists``
    call and the ``unblock_artists`` call live in
    ``execute_apply``. Splitting them keeps the rail in the CLI
    honest: there is no path through the engine that bypasses the
    CLI's confirmation prompt.
    """
    now_iso = _now_iso()

    errors: list[tuple[str, str]] = []
    union_pairs: list[tuple[str, str]] = []

    # Steps 1 and 2. One bad list never stops the others.
    for sub in subscriptions:
        if not _is_stale(sub, now_iso):
            dest = cache_path(sub.name, sub.format)
            data = dest.read_bytes()
            pairs = parse_filter_list(data, sub.format)
        else:
            dest = cache_path(sub.name, sub.format)
            try:
                fetch_source(sub.source, sub.format, dest)
            except FetchError as exc:
                errors.append((sub.name, str(exc)))
                continue
            data = dest.read_bytes()
            pairs = parse_filter_list(data, sub.format)
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
    # read propagates rather than returning empty.
    blocked_ids = await fetch_blocked_artist_ids(session)
    blocked_set = set(blocked_ids)

    # Step 4. Partition into the three sets the CLI will print.
    to_block: list[tuple[str, str]] = []
    already_blocked: list[tuple[str, str]] = []
    for tidal_id in order:
        if tidal_id in blocked_set:
            already_blocked.append((tidal_id, name_for_id[tidal_id]))
        else:
            to_block.append((tidal_id, name_for_id[tidal_id]))

    union_id_set = set(order)
    unlisted: list[tuple[str, str]] = [(bid, "") for bid in blocked_ids if bid not in union_id_set]

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
    prune: bool,
) -> ApplyOutcome:
    """Apply a previously computed ``ApplyPlan`` to Tidal.

    The cap is enforced before any write: exceeding
    ``MAX_APPLY_IDS`` returns an ``ApplyOutcome`` with ``capped=True``
    and both outcomes ``None`` so the CLI can surface the abort
    without issuing a partial block. With the cap honoured the
    block call is made only if there is something to block, and the
    unblock call only when ``prune`` is set and there is something
    to unblock.
    """
    if len(plan.to_block) > MAX_APPLY_IDS:
        return ApplyOutcome(blocked=None, unblocked=None, capped=True)

    blocked: UploadOutcome | None = None
    if plan.to_block:
        blocked = await block_artists(session, [pair[0] for pair in plan.to_block])

    unblocked: UploadOutcome | None = None
    if prune and plan.unlisted:
        unblocked = await unblock_artists(session, [bid for bid, _name in plan.unlisted])

    return ApplyOutcome(blocked=blocked, unblocked=unblocked, capped=False)
