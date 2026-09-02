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

from __future__ import annotations

import os
from typing import Any

import pytest

from tidal_sync.auth import get_session


@pytest.fixture(scope="session")
def live_profile() -> str:
    name = os.environ.get("TIDAL_TEST_PROFILE")
    if not name:
        pytest.fail("TIDAL_TEST_PROFILE is not set; the live suite has no account to run against")
    return name


@pytest.fixture(scope="session")
def session(live_profile: str) -> Any:
    sess = get_session(live_profile)
    if sess.user is None:
        pytest.fail(f"profile {live_profile!r} did not authenticate; the stored token is stale")
    return sess
