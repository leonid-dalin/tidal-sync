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

# The largest batch we will ever push in a single apply run. Exceeding
# the cap aborts the whole run; we never block a truncated set.
MAX_APPLY_IDS: int = 5000


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
