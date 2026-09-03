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
"""Values shared by every CLI module.

``console`` and the rail threshold live here rather than in ``cli.py``
because ``cli_blocklist`` needs both and ``cli`` imports
``cli_blocklist``. Before this module existed the cycle was broken by
importing a private name from ``cli`` inside six function bodies.
"""

from __future__ import annotations

from rich.console import Console

from .domain.results import UploadOutcome

console = Console()

# Threshold above which a destructive batch asks the operator to retype
# the profile name. Ten is the figure specified in plan-v2 Task 6.
BLOCK_RAIL_THRESHOLD = 10


def _report_outcome(outcome: UploadOutcome | None, applied: str, rejected: str) -> int:
    """Single reporter for both directions so block and unblock output cannot diverge."""
    if outcome is None:
        return 0
    for tid in outcome.applied:
        console.print(f"  [green]{applied} {tid}[/green]")
    for tid in outcome.rejected:
        console.print(f"  [red]{rejected} {tid}[/red]")
    return len(outcome.rejected)
