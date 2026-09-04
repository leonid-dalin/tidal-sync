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

"""Pin the two dependency-direction rules the architecture document states.

``docs/architecture.md`` says dependencies point downward only, and that
the domain layer depends on nothing inside the package. Both held on
inspection but neither was enforced, which is how the engine acquired a
CLI import during an earlier refactor. A grep is cheap; the review that
caught the slip was not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "src" / "tidal_sync"
ENGINE = PKG / "engine"
DOMAIN = PKG / "domain"

CLI_IMPORT = re.compile(r"^\s*(from|import)\s+.*\bcli(_\w+)?\b", re.MULTILINE)
INTERNAL_IMPORT = re.compile(r"^\s*(from|import)\s+tidal_sync\b", re.MULTILINE)


@pytest.mark.parametrize("module", sorted(ENGINE.glob("*.py")), ids=lambda p: p.name)
def test_no_engine_module_imports_the_cli_layer(module: Path) -> None:
    """The engine may not depend on the CLI, in either direction of naming."""
    match = CLI_IMPORT.search(module.read_text(encoding="utf-8"))
    assert match is None, (
        f"{module.name} imports the CLI layer: {match.group(0).strip()!r}. "
        "Dependencies point downward only; move the shared value into the "
        "engine or the domain and let the CLI reach down for it."
    )


@pytest.mark.parametrize("module", sorted(DOMAIN.glob("*.py")), ids=lambda p: p.name)
def test_domain_layer_imports_nothing_internal(module: Path) -> None:
    """The domain layer depends on nothing inside the package.

    The doc states this as a rule; without a check it is one review away
    from the same drift the engine rule guards against.
    """
    match = INTERNAL_IMPORT.search(module.read_text(encoding="utf-8"))
    assert match is None, (
        f"{module.name} imports the package: {match.group(0).strip()!r}. "
        "The domain layer must stay free of internal dependencies."
    )
