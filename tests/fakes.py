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


class FakeFavorites:
    """Stands in for tidalapi.user.Favorites.

    The real add_* returns a bare bool and remove_* returns False for a list
    instead of raising, so the fake mirrors both. `reject` marks ids the
    server refuses (a False return); `raise_for` maps an id to an exception
    the gate would raise after exhausting its budget.
    """

    def __init__(self):
        self.added_tracks: list[str] = []
        self.added_artists: list[str] = []
        self.added_albums: list[str] = []
        self.removed_tracks: list[str] = []
        self.removed_artists: list[str] = []
        self.removed_albums: list[str] = []
        self.reject: set[str] = set()
        self.raise_for: dict[str, Exception] = {}

    def _apply(self, sink: list[str], item_id):
        if isinstance(item_id, list):
            return False
        if item_id in self.raise_for:
            raise self.raise_for[item_id]
        if item_id in self.reject:
            return False
        sink.append(item_id)
        return True

    def add_track(self, track_id):
        return self._apply(self.added_tracks, track_id)

    def add_artist(self, artist_id):
        return self._apply(self.added_artists, artist_id)

    def add_album(self, album_id):
        return self._apply(self.added_albums, album_id)

    def remove_track(self, track_id):
        return self._apply(self.removed_tracks, track_id)

    def remove_artist(self, artist_id):
        return self._apply(self.removed_artists, artist_id)

    def remove_album(self, album_id):
        return self._apply(self.removed_albums, album_id)


class FakeUser:
    def __init__(self, user_id=4242):
        self.id = user_id
        self.favorites = FakeFavorites()


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
        self.user = FakeUser()

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
