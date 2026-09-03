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

"""Pin the dependency direction the architecture document states.

``docs/architecture.md`` says dependencies point downward only: the CLI
depends on the engine, the engine depends on the domain. Nothing enforced
it, and an engine module acquired a CLI import while a four-line
timestamp helper was being moved. A grep is cheap; the review that
caught it was not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ENGINE = Path(__file__).parent.parent / "src" / "tidal_sync" / "engine"
CLI_IMPORT = re.compile(r"^\s*(from|import)\s+.*\bcli(_\w+)?\b", re.MULTILINE)


@pytest.mark.parametrize("module", sorted(ENGINE.glob("*.py")), ids=lambda p: p.name)
def test_no_engine_module_imports_the_cli_layer(module: Path) -> None:
    """The engine may not depend on the CLI, in either direction of naming."""
    match = CLI_IMPORT.search(module.read_text(encoding="utf-8"))
    assert match is None, (
        f"{module.name} imports the CLI layer: {match.group(0).strip()!r}. "
        "Dependencies point downward only; move the shared value into the "
        "engine or the domain and let the CLI reach down for it."
    )
