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

"""Read-only live checks against a real Tidal account.

Marked `live`, so they never run in the default suite. Run them from the
live-tests workflow, or locally with the profile named in the environment:

    TIDAL_TEST_PROFILE=test_acc pytest -m live

every test here only reads. None of them block, unblock, add or remove
anything on the account. They exist to catch auth, network and
deserialisation breakage the offline suite cannot reach.

`addopts` excludes the `live` marker by default, so pointing pytest at this
directory alone reports "no tests ran" with no explanation. Pass `-m live`.
"""

from __future__ import annotations

from typing import Any

import pytest

from tidal_sync.auth import get_session
from tidal_sync.engine.curation import fetch_blocked_artist_ids

pytestmark = pytest.mark.live


def test_authentication_resolves_a_user(session: Any) -> None:
    """The account authenticates and resolves to a numeric user id."""
    assert session.user.id > 0


async def test_blocked_artists_read_returns_string_ids(session: Any) -> None:
    """fetch_blocked_artist_ids answers and yields string ids.

    This is the engine contract every filter-list apply depends on. The
    count is account state, not contract, so only the shape is asserted;
    an empty blocklist is a legitimate result.
    """
    blocked = await fetch_blocked_artist_ids(session)
    assert isinstance(blocked, list)
    assert all(isinstance(item, str) and item.isdigit() for item in blocked)


def test_favourite_tracks_deserialise(session: Any) -> None:
    """Every favourite row carries an id, so deserialisation held."""
    tracks = session.user.favorites.tracks(limit=10)
    assert isinstance(tracks, list)
    assert all(getattr(track, "id", None) for track in tracks)


def test_profile_round_trips_through_get_session(session: Any, live_profile: str) -> None:
    """A second get_session for the same profile reuses the stored token."""
    again = get_session(live_profile)
    assert again.user is not None
    assert again.user.id == session.user.id
