"""Hand-written fakes for the tidalapi objects the engine touches.

The engine only ever reaches for a narrow surface: .id, .name, .artist,
.album, .isrc, and a handful of methods. Fakes are cheaper and more
readable than mocks, and they fail loudly when the engine starts using
something new.

Search results are dicts on purpose. Session.search() returns a
SearchResults TypedDict, which is a plain dict at runtime, so any fake
that models search output as an object hides the F40 class of defect.

Fakes passed to execute_network must be synchronous. It runs them through
asyncio.to_thread, which never awaits a coroutine.
"""

import tidalapi


def make_search_results(tracks=None, albums=None, artists=None):
    """Builds a search result shaped exactly like tidalapi.Session.search()."""
    return {
        "tracks": list(tracks or []),
        "albums": list(albums or []),
        "artists": list(artists or []),
        "videos": [],
        "playlists": [],
        "top_hit": None,
    }


class FakeArtist:
    def __init__(self, name="Artist"):
        self.name = name
        self.id = 1


class FakeAlbum:
    def __init__(self, name="Album"):
        self.name = name
        self.id = 2


class FakeTrack:
    def __init__(self, name="Track", artist=None, album=None, isrc="ISRC0001", id=100):
        self.name = name
        self.artist = artist or FakeArtist()
        self.album = album or FakeAlbum()
        self.isrc = isrc
        self.id = id


class FakeSession:
    """Minimal stand-in for tidalapi.Session.

    Records every call so tests can assert on request counts. search()
    returns the same dict shape the real library returns.
    """

    def __init__(self, search_results=None):
        self.calls: list[tuple[str, tuple, dict]] = []
        self._search_results = (
            search_results if search_results is not None else make_search_results()
        )
        self.country_code = "US"
        self.access_token = "fake-access-token"
        self.token_type = "Bearer"
        self.refresh_token = "fake-refresh-token"
        self.expiry_time = None

    def search(self, query, **kwargs):
        self.calls.append(("search", (query,), kwargs))
        return self._search_results

    def playlist(self, playlist_id):
        self.calls.append(("playlist", (playlist_id,), {}))
        raise NotImplementedError("override in tests that need it")


def real_search_results(tracks=None, albums=None):
    """Builds search results through the real tidalapi SearchResults type.

    Used by the contract test to prove the engine's access pattern matches
    what the library actually hands back.
    """
    return tidalapi.session.SearchResults(
        tracks=list(tracks or []),
        albums=list(albums or []),
        artists=[],
        videos=[],
        playlists=[],
        top_hit=None,
    )
