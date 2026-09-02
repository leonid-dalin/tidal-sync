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
Optionally act on those sets.

The decision about what to unblock lives at this layer, not in the
CLI: this module owns the "what does prune mean" invariant. The CLI
only decides whether to ask the operator to invoke this engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import tidalapi

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
    *,
    dry_run: bool,
    prune: bool,
) -> ApplyPlan:
    """Compute the apply plan and optionally act on it.

    Steps 1 to 4 always run: fetch stale lists, parse cached lists,
    read the live blocklist, partition the union into ``to_block``,
    ``already_blocked`` and ``unlisted``.

    Steps 5 to 8 run only when ``dry_run`` is False. The cap is
    enforced before any write. ``prune`` controls whether the
    ``unlisted`` set is sent to ``unblock_artists``.
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

    # Step 5. Dry run stops here. No network write of any kind.
    if dry_run:
        return ApplyPlan(
            to_block=to_block,
            already_blocked=already_blocked,
            unlisted=unlisted,
            errors=errors,
        )

    # Step 6. Cap check before any write. Chosen semantics: record an
    # error and return without writing. A truncated block is worse
    # than no block at all, because the user expects no partial writes.
    if len(to_block) > MAX_APPLY_IDS:
        msg = f"to_block has {len(to_block)} ids, exceeds MAX_APPLY_IDS={MAX_APPLY_IDS}; aborting"
        errors.append(("<apply>", msg))
        return ApplyPlan(
            to_block=to_block,
            already_blocked=already_blocked,
            unlisted=unlisted,
            errors=errors,
        )

    # Step 7. Block the union minus the live blocklist. An empty
    # to_block is a no-op, and calling through to ``block_artists``
    # would issue zero writes and cost nothing, but the tests pin the
    # contract that no call is made in that case.
    if to_block:
        await block_artists(session, [pair[0] for pair in to_block])

    # Step 8. Prune only on explicit request. The interactive prompt
    # in cli_prompts.py is the other path to an unblock; this engine
    # is not invoked from there.
    if prune and unlisted:
        await unblock_artists(session, [bid for bid, _name in unlisted])

    return ApplyPlan(
        to_block=to_block,
        already_blocked=already_blocked,
        unlisted=unlisted,
        errors=errors,
    )
