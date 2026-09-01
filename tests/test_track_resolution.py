"""Track resolution: CSV metadata to Tidal IDs.

The resolver is the heart of the import. If it cannot read a search
result, every track lacking a direct Tidal ID is reported as not found,
which is silent data loss dressed up as a clean run.
"""

import pytest

from tidal_sync.engine.importer import resolve_track_to_id
from tests.fakes import FakeSession, FakeTrack, make_search_results


async def test_isrc_match_returns_the_tidal_id():
    """F40: an ISRC hit must resolve. getattr() on a dict made this fail."""
    session = FakeSession(
        make_search_results(tracks=[FakeTrack(id=4242, name="Helena")])
    )

    track_id = await resolve_track_to_id(
        session, track_name="Helena", artist_name="MCR", isrc="ISRC1"
    )

    assert track_id == "4242"


async def test_text_fallback_returns_the_tidal_id():
    """F40: the fallback path has the same defect as the ISRC path."""
    session = FakeSession(
        make_search_results(tracks=[FakeTrack(id=777, name="Helena")])
    )

    track_id = await resolve_track_to_id(
        session, track_name="Helena", artist_name="MCR", isrc=None
    )

    assert track_id == "777"


async def test_direct_id_skips_the_network():
    """A known Tidal ID must not cost a search call."""
    session = FakeSession(make_search_results(tracks=[FakeTrack(id=1)]))

    track_id = await resolve_track_to_id(
        session, track_name="X", artist_name="Y", tidal_id="99"
    )

    assert track_id == "99"
    assert not [call for call in session.calls if call[0] == "search"]


async def test_isrc_is_tried_before_text():
    session = FakeSession(make_search_results(tracks=[FakeTrack(id=5)]))

    await resolve_track_to_id(
        session, track_name="Helena", artist_name="MCR", isrc="ISRC1"
    )

    assert session.calls[0] == ("search", ("isrc:ISRC1",), {})


async def test_no_match_returns_none():
    session = FakeSession(make_search_results(tracks=[]))

    track_id = await resolve_track_to_id(
        session, track_name="Nothing", artist_name="Nobody", isrc=None
    )

    assert track_id is None


async def test_text_fallback_drops_secondary_artists():
    """The search string keeps only the primary artist."""
    session = FakeSession(make_search_results(tracks=[FakeTrack(id=3)]))

    await resolve_track_to_id(
        session,
        track_name="Song",
        artist_name="Lead Artist, Featured Artist",
        isrc=None,
    )

    assert session.calls[-1][1][0] == "Song Lead Artist"


async def test_search_results_missing_a_key_do_not_explode():
    """A short result dict must degrade to None, not KeyError."""

    class ShortSession(FakeSession):
        def search(self, query, **kwargs):
            return {"tracks": []}

    assert (
        await resolve_track_to_id(ShortSession(), track_name="X", artist_name="Y", isrc=None)
    ) is None
