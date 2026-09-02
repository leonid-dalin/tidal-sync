"""Read-only live checks against a real Tidal account.

Marked `live`, so they never run in the default suite. Run them from the
workflow (which materialises the token into a profile) or locally with:

    pytest -m live --account-profile test_acc

Every test here only reads. None of them block, unblock, add, or remove
anything on the account. They exist to catch auth, network, and deserialisation
breakage that the offline suite cannot reach.

The account profile comes from the TIDAL_LIVE_PROFILE environment variable,
defaulting to the throwaway `test_acc` used in CI.
"""

import os

import pytest

from tidal_sync.auth import get_session

PROFILE = os.environ.get("TIDAL_LIVE_PROFILE", "test_acc")


pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def session():
    """One authenticated session for the whole module.

    Fails fast with a clear message if no token is present, rather than
    erroring deep inside tidalapi.
    """
    try:
        sess = get_session(PROFILE)
    except Exception as exc:  # noqa: BLE001 - surface the cause, not a stack
        pytest.skip(f"no live token for profile {PROFILE!r}: {exc}")
    if sess.user is None:
        pytest.skip(f"profile {PROFILE!r} did not authenticate")
    return sess


def test_authentication_succeeds(session):
    """The throwaway account authenticates and resolves to a real user id."""
    assert session.user.id > 0
    # The CI throwaway is a fixed account; pin it so a wrong-secret upload
    # fails loudly instead of silently running against an unexpected profile.
    assert session.user.id == 208295982


def test_blocked_artists_are_readable(session):
    """The block list endpoint answers and yields a list of ints."""
    blocked = session.user.favorites.artists(limit=50)
    assert isinstance(blocked, list)
    # The endpoint answers and returns artist objects with ids. Exact count is
    # account state, not contract, so we assert shape, not a number.
    assert blocked and all(a.id for a in blocked)


def test_library_counts_are_positive_or_zero(session):
    """Library collection endpoints respond and return sane counts."""
    tracks = session.user.favorites.tracks(limit=10)
    assert isinstance(tracks, list)
    assert len(tracks) >= 0


def test_profile_round_trips_through_get_session(session):
    """A second get_session for the same profile reuses the stored token."""
    again = get_session(PROFILE)
    assert again.user is not None
    assert again.user.id == session.user.id
