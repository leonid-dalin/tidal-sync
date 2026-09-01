"""Curation engine: favourites and artist blocks."""

import pytest

from tests.fakes import FakeSession
from tidal_sync.domain.exceptions import TidalRateLimitError, TidalTransientError
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine import curation


def test_upload_outcome_is_importable_from_domain():
    """Two engines share this type, so it lives in domain, not in the importer."""
    outcome = UploadOutcome(applied=["1"], rejected=["2"])

    assert outcome.applied == ["1"]
    assert outcome.rejected == ["2"]


async def test_every_id_is_sent_individually():
    """Favorites.add_* returns one bool for a comma-joined batch, so batching
    would make per-id reporting a lie. One request per id is the contract."""
    session = FakeSession()

    outcome = await curation.like_tracks(session, ["1", "2", "3"])

    assert outcome.applied == ["1", "2", "3"]
    assert outcome.rejected == []
    assert session.user.favorites.added_tracks == ["1", "2", "3"]


async def test_one_rejected_id_does_not_sink_the_rest():
    session = FakeSession()
    session.user.favorites.reject = {"2"}

    outcome = await curation.like_tracks(session, ["1", "2", "3"])

    assert outcome.applied == ["1", "3"]
    assert outcome.rejected == ["2"]


async def test_results_are_ordered_by_input_not_completion():
    """Fan-out is concurrent, so the report must be sorted back into input
    order or the CLI prints a different sequence run to run."""
    session = FakeSession()
    session.user.favorites.reject = {"1"}

    outcome = await curation.like_tracks(session, ["1", "2", "3", "4"])

    assert outcome.applied == ["2", "3", "4"]
    assert outcome.rejected == ["1"]


async def test_a_transient_failure_is_recorded_per_id():
    session = FakeSession()
    session.user.favorites.raise_for = {"2": TidalTransientError("gave up")}

    outcome = await curation.like_tracks(session, ["1", "2", "3"])

    assert outcome.applied == ["1", "3"]
    assert outcome.rejected == ["2"]


async def test_an_account_level_failure_aborts_the_whole_run():
    """A rate limit or abuse lock is not this id's fault. Counting it as a
    per-id rejection would report a throttled account as hundreds of missing
    items and keep hammering the gate."""
    session = FakeSession()
    session.user.favorites.raise_for = {"2": TidalRateLimitError("locked")}

    with pytest.raises(BaseExceptionGroup) as excinfo:
        await curation.like_tracks(session, ["1", "2", "3"])
    assert excinfo.group_contains(TidalRateLimitError)


async def test_unlike_sends_one_id_at_a_time():
    """Favorites.remove_* returns False for a list rather than raising, so a
    list must never reach it."""
    session = FakeSession()

    outcome = await curation.unlike_tracks(session, ["1", "2"])

    assert outcome.applied == ["1", "2"]
    assert session.user.favorites.removed_tracks == ["1", "2"]


async def test_empty_input_makes_no_requests():
    session = FakeSession()

    outcome = await curation.like_tracks(session, [])

    assert outcome == curation.UploadOutcome(applied=[], rejected=[])
    assert session.user.favorites.added_tracks == []


# --- Artist blocks: wire tests pinned to the probe-confirmed contract. ---
#
# The probe ran 2026-09-01 against a throwaway account and confirmed:
#   block = POST users/{user_id}/blocks/artists, data={"artistId": <id>}, 200
#   unblock = DELETE users/{user_id}/blocks/artists/<id>, 204
# The bodies are empty in both directions. Tidal returns no per-id payload on
# a block write, so success is recorded per-id by the helper: a 200 means
# applied, anything else means rejected.


class _FakeResponse:
    """A requests.Response-like object that knows whether the call succeeded.

    `ok=True` for the 200/204 the probe confirmed. `ok=False` stands in for
    a 4xx: the engine records that id as rejected without aborting siblings.
    `__bool__` mirrors `requests.Response` so `_apply_per_id`'s `bool(ok)`
    sees `False` for a 4xx.
    """

    def __init__(self, ok: bool):
        self.ok = ok

    def __bool__(self) -> bool:
        return self.ok


class _BlockWireSession:
    """Captures the raw V1 calls for block/unblock and fetch.

    Records (method, path, params, data) for every call so a test can assert
    the exact tuple the probe produced. The request callable is synchronous
    because execute_network runs it inside a worker thread.
    """

    def __init__(self, user_id: int = 4242, ok: bool = True):
        self.user_id = user_id
        self.calls: list[tuple] = []
        self._ok = ok

    @property
    def user(self):
        return _FakeUser(self.user_id)

    @property
    def request(self):
        session = self

        class _Req:
            def request(self, method, path, params=None, data=None, base_url=None):
                session.calls.append((method, path, params, data, base_url))
                return _FakeResponse(session._ok)

        return _Req()


class _SelectiveBlockWireSession:
    """Per-id response control for the 4xx test.

    Each id is reported as ok or not by the `ok_for` mapping; ids absent
    from the map default to ok=True so the helper proceeds.
    """

    def __init__(self, user_id: int = 4242, ok_for: dict[str, bool] | None = None):
        self.user_id = user_id
        self.calls: list[tuple] = []
        self._ok_for = ok_for or {}

    @property
    def user(self):
        return _FakeUser(self.user_id)

    @property
    def request(self):
        session = self

        class _Req:
            def request(self, method, path, params=None, data=None, base_url=None):
                session.calls.append((method, path, params, data, base_url))
                artist_id = (data or {}).get("artistId")
                return _FakeResponse(session._ok_for.get(artist_id, True))

        return _Req()


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.favorites = FakeSession().user.favorites


class _BlockedArtist:
    def __init__(self, artist_id: int):
        self.id = artist_id


async def test_block_artists_posts_form_with_artist_id_field():
    """The probe showed POST users/{user_id}/blocks/artists with the form
    field `artistId` returns 200; that is the write verb, not a query param."""
    session = _BlockWireSession(user_id=4242)

    outcome = await curation.block_artists(session, ["123", "456"])

    assert outcome.applied == ["123", "456"]
    assert outcome.rejected == []

    # Two ids, two POSTs, same collection path, same form field. The fan-out
    # is concurrent, so order is not guaranteed; compare as sorted multisets
    # by an indexable key.
    calls = sorted(
        session.calls,
        key=lambda c: (c[0], c[1], c[3]["artistId"]),
    )
    assert calls == [
        ("POST", "users/4242/blocks/artists", None, {"artistId": "123"}, None),
        ("POST", "users/4242/blocks/artists", None, {"artistId": "456"}, None),
    ]


async def test_unblock_artists_deletes_per_artist_path():
    """The probe showed DELETE users/{user_id}/blocks/artists/{artist_id}
    returns 204; per-id path, no body. Confirming by a follow-up GET is the
    network's job, not this engine's."""
    session = _BlockWireSession(user_id=4242)

    outcome = await curation.unblock_artists(session, ["123", "456"])

    assert outcome.applied == ["123", "456"]
    assert outcome.rejected == []

    assert sorted(session.calls, key=lambda c: c[1]) == [
        ("DELETE", "users/4242/blocks/artists/123", None, None, None),
        ("DELETE", "users/4242/blocks/artists/456", None, None, None),
    ]


async def test_one_block_4xx_records_that_id_as_rejected():
    """A 4xx on one id is this id's outcome: rejected, but the others must
    still be applied. The TaskGroup must not be cancelled by a single bad
    block write."""
    session = _SelectiveBlockWireSession(user_id=4242, ok_for={"456": False})

    outcome = await curation.block_artists(session, ["123", "456", "789"])

    assert outcome.applied == ["123", "789"]
    assert outcome.rejected == ["456"]

    # Three calls went out regardless of the 4xx on id "456".
    assert len(session.calls) == 3
    sent_ids = sorted(data["artistId"] for _, _, _, data, _ in session.calls)
    assert sent_ids == ["123", "456", "789"]
    assert all(method == "POST" for method, _, _, _, _ in session.calls)
    assert all(path == "users/4242/blocks/artists" for _, path, _, _, _ in session.calls)


async def test_fetch_blocked_artist_ids_returns_string_ids():
    """fetch_blocked_artist_ids is a thin caller over fetch_blocked_artists
    that maps each object to its string id. The engine must not grow its own
    paginator."""

    class _FakeUserInner:
        def __init__(self):
            self.id = 4242

    class _FetchSession:
        def __init__(self):
            self.calls = 0
            self.user = _FakeUserInner()

    fake = _FetchSession()

    # fetch_blocked_artists lives in network.py; the engine imports it and
    # passes the session as its only argument. The fake function ignores the
    # session, but the engine still has to feed it through execute_network.
    def fake_fetch(session):
        fake.calls += 1
        return [_BlockedArtist(29002266), _BlockedArtist(4894212)]

    import tidal_sync.engine.curation as cur_module

    original = cur_module.fetch_blocked_artists
    cur_module.fetch_blocked_artists = fake_fetch  # type: ignore[assignment]
    try:
        ids = await cur_module.fetch_blocked_artist_ids(fake)  # type: ignore[arg-type]
    finally:
        cur_module.fetch_blocked_artists = original

    # Order preserved, ints stringified, no set() collapse.
    assert ids == ["29002266", "4894212"]
    assert fake.calls == 1
