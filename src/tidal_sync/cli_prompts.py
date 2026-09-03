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
"""Interactive checkbox prompt for unblocking artists.

This module is the boundary between the operator's intent and the
account-mutating call. The invariant, owned here, is that nothing is
unblocked unless the operator explicitly ticks a candidate and
confirms a non-empty selection. Every failure path (non-tty, timeout,
exception, interrupt, empty selection, force flag) returns an empty
list. Skipping is always safe; unblocking is never accidental.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

_TIMEOUT_SECONDS: float = 90.0


def _label_for(candidate_id: str, name: str) -> str:
    """Name first: an operator scans names, not ids."""
    if name:
        return f"{name} ({candidate_id})"
    return candidate_id


def _print_candidates(candidates: list[tuple[str, str]]) -> None:
    from .cli import console

    for cid, name in candidates:
        console.print(_label_for(cid, name))


def _reset_terminal() -> None:
    """Return the terminal to cooked mode after an abandoned prompt.

    The timeout leaves the worker thread inside prompt_toolkit, which
    holds the tty in raw mode. Nothing else restores it, so the operator's
    shell would stay broken after a skip.
    """
    if not sys.stdout.isatty():
        return
    try:
        from prompt_toolkit.output.defaults import create_output

        create_output().reset_attributes()
    except Exception:
        # A terminal we cannot reset is not worth crashing a skip path
        # over; the skip itself is still correct.
        pass


def prompt_unblock(
    candidates: list[tuple[str, str]],
    *,
    force: bool,
    timeout: float = _TIMEOUT_SECONDS,
) -> list[str]:
    """Ask the operator which blocked artists to unblock.

    The invariant at this layer is that a returned list of ids
    represents an explicit, confirmed, non-empty selection. Every
    other path returns ``[]``.
    """

    if force:
        return []

    if not sys.stdin.isatty():
        _print_candidates(candidates)
        return []

    box: dict[str, Any] = {}

    def worker() -> None:
        import questionary

        try:
            answer = questionary.checkbox(
                "Unblock which artists?",
                choices=[{"name": _label_for(cid, name), "value": cid} for cid, name in candidates],
            ).unsafe_ask()
        except BaseException:
            # BaseException is intentional: a KeyboardInterrupt raised
            # by prompt_toolkit must collapse to the safe empty
            # selection, matching every other failure path.
            box["answer"] = []
        else:
            box["answer"] = answer

    thread = threading.Thread(target=worker, name="prompt-unblock", daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        _reset_terminal()
        _print_candidates(candidates)
        return []

    answer = box.get("answer", [])
    if not answer:
        return []
    return [str(item) for item in answer]
