"""Curation engine: favourites and artist blocks."""

import time

import pytest
import requests

from tests.fakes import FakeSession
from tidal_sync.domain.exceptions import (
    BatchTooLarge,
    TidalRateLimitError,
    TidalTransientError,
)
from tidal_sync.domain.results import UploadOutcome
from tidal_sync.engine import curation


def _http_error(status: int, body: str = "") -> requests.HTTPError:
    """Builds an HTTPError carrying a real response, the way tidalapi does.

    The response object is what `classify_error` reads, so the test exercises
    the same code path as a live `raise_for_status()` call.
    """
    response = requests.Response()
    response.status_code = status
    response._content = body.encode()
    return requests.HTTPError(body or str(status), response=response)


class FakeBlockSession:
    """Records per-id block/unblock requests and fails on demand.

    Modelled on `_BlockWireSession` above. `raise_for` triggers a one-shot
    HTTPError on the listed ids; `fail_times` raises the same status the
    listed number of times, then lets the call succeed. The request
    callable is a plain `def` because `execute_network` runs it inside
    `asyncio.to_thread`.
    """

    class _Config:
        api_v2_location = "https://api.tidal.com/v2"

    config = _Config()
    country_code = "GB"

    def __init__(
        self,
        user_id: int = 4242,
        raise_for: dict[str, requests.HTTPError] | None = None,
        fail_times: dict[str, int] | None = None,
        fail_status: int = 503,
        blocked: set[str] | None = None,
        silent_noop: set[str] | None = None,
    ):
        self.user_id = user_id
        self.calls: list[tuple] = []
        self.attempts: dict[str, int] = {}
        self._raise_for = raise_for or {}
        self._fail_times = fail_times or {}
        self._fail_status = fail_status
        self.blocked = set(blocked) if blocked else set()
        self._silent_noop = silent_noop or set()
        # Blocklist read controls for the reconciliation tests: an explicit
        # `blocklist` (list of string ids) is what the GET returns; when
        # `blocklist_raises` is set, the read raises it instead. Both default
        # to "follow the writes", which is the happy path.
        self.blocklist: list[str] | None = None
        self.blocklist_raises: BaseException | None = None

    @property
    def user(self):
        class _U:
            def __init__(self, uid):
                self.id = uid

        return _U(self.user_id)

    @property
    def request(self):
        session = self

        class _Req:
            def request(self, method, path, params=None, data=None, base_url=None):
                artist_id = (data or {}).get("artistId")
                if artist_id is None:
                    parts = path.rsplit("/", 1)
                    artist_id = parts[1] if len(parts) == 2 else path
                session.calls.append((method, path, params, data, base_url))
                session.attempts[artist_id] = session.attempts.get(artist_id, 0) + 1

                if method == "GET":
                    return _FakeResponse(list(session.blocked))

                if artist_id in session._silent_noop:
                    return _FakeResponse(True)

                if artist_id in session._raise_for:
                    raise session._raise_for[artist_id]

                remaining = session._fail_times.get(artist_id, 0)
                if remaining > 0:
                    session._fail_times[artist_id] = remaining - 1
                    raise _http_error(session._fail_status, f"server said {session._fail_status}")

                if method == "POST":
                    session.blocked.add(artist_id)
                elif method == "DELETE":
                    session.blocked.discard(artist_id)
                return _FakeResponse(True)

            def map_request(self, endpoint, params=None, parse=None):
                if session.blocklist_raises is not None:
                    raise session.blocklist_raises
                if session.blocklist is not None:
                    return [session.parse_artist({"id": i}) for i in session.blocklist]
                return [session.parse_artist({"id": item}) for item in sorted(session.blocked)]

        return _Req()

    def parse_artist(self, item):
        class _A:
            id = str(item["id"]) if isinstance(item, dict) else str(item)

        return _A()


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
    assert sorted(session.user.favorites.added_tracks) == ["1", "2", "3"]
    assert len(session.user.favorites.added_tracks) == 3


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

    `ok=True` for the 200/204 the probe confirmed. `__bool__` mirrors
    `requests.Response` so `_apply_per_id`'s `bool(ok)` sees `False` for
    any response the fake flags as failed.
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
        self.blocked: set[str] = set()

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
                if method == "POST" and artist_id:
                    session.blocked.add(artist_id)
                elif method == "DELETE":
                    parts = path.rsplit("/", 1)
                    if len(parts) == 2:
                        session.blocked.discard(parts[1])
                return _FakeResponse(session._ok)

            def map_request(self, endpoint, params=None, parse=None):
                return [session.parse_artist({"id": item}) for item in sorted(session.blocked)]

        return _Req()

    def parse_artist(self, item):
        return _BlockedArtist(int(item["id"]))


class _SelectiveBlockWireSession:
    """Per-id response control for the 4xx test.

    Each id is reported as ok or not by the `ok_for` mapping; ids absent
    from the map default to ok=True so the helper proceeds.
    """

    def __init__(self, user_id: int = 4242, ok_for: dict[str, bool] | None = None):
        self.user_id = user_id
        self.calls: list[tuple] = []
        self._ok_for = ok_for or {}
        self.blocked: set[str] = set()

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
                ok = session._ok_for.get(artist_id, True)
                if ok and method == "POST" and artist_id:
                    session.blocked.add(artist_id)
                elif method == "DELETE":
                    parts = path.rsplit("/", 1)
                    if len(parts) == 2:
                        session.blocked.discard(parts[1])
                return _FakeResponse(ok)

            def map_request(self, endpoint, params=None, parse=None):
                return [session.parse_artist({"id": item}) for item in sorted(session.blocked)]

        return _Req()

    def parse_artist(self, item):
        return _BlockedArtist(int(item["id"]))


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

    # fetch_blocked_artist_ids is a strict caller over fetch_blocked_artists_strict
    # in network.py; the engine imports it and passes the session as its only
    # argument. The fake function ignores the session, but the engine still has
    # to feed it through execute_network.
    def fake_fetch(session):
        fake.calls += 1
        return [_BlockedArtist(29002266), _BlockedArtist(4894212)]

    import tidal_sync.engine.curation as cur_module

    original = cur_module.fetch_blocked_artists_strict
    cur_module.fetch_blocked_artists_strict = fake_fetch  # type: ignore[assignment]
    try:
        ids = await cur_module.fetch_blocked_artist_ids(fake)  # type: ignore[arg-type]
    finally:
        cur_module.fetch_blocked_artists_strict = original

    # Order preserved, ints stringified, no set() collapse.
    assert ids == ["29002266", "4894212"]
    assert fake.calls == 1


# --- Network gate visibility: the block writes must surface HTTP failures
# so classify_error can engage the abuse lock, retry a 5xx, and treat a
# non-retryable 4xx as a per-id rejection. Catching HTTPError inside the
# action hides all three behaviours.


async def test_a_403_abuse_lock_is_not_swallowed_as_a_per_id_rejection(gate, monkeypatch):
    """classify_error engages a 1800s global lock on an abuse 403. Catching
    HTTPError inside the action would hide it and keep writing.
    """
    from tidal_sync.engine import network

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(network.asyncio, "sleep", _no_sleep)
    session = FakeBlockSession(raise_for={"2": _http_error(403, "abuse detected 11003")})

    with pytest.raises(BaseExceptionGroup) as excinfo:
        await curation.block_artists(session, ["1", "2", "3"])
    assert excinfo.group_contains(TidalRateLimitError)

    assert gate.backoff_until > time.monotonic(), "the abuse lock was engaged"


async def test_a_500_is_retried_by_the_gate_not_recorded_as_rejected():
    """A 503 is retryable; the gate should retry until the call succeeds."""
    from tidal_sync.engine import network

    gate = network.GlobalTidalGate()
    network.GLOBAL_GATE = gate

    async def _no_sleep(_seconds):
        return None

    original_sleep = network.asyncio.sleep
    network.asyncio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        session = FakeBlockSession(fail_times={"2": 2}, fail_status=503)

        outcome = await curation.block_artists(session, ["1", "2"])

        assert outcome.applied == ["1", "2"]
        assert session.attempts["2"] == 3, "the gate retried rather than giving up"
    finally:
        network.asyncio.sleep = original_sleep  # type: ignore[assignment]
        network.GLOBAL_GATE = network.GlobalTidalGate()


async def test_a_400_is_this_ids_problem_and_the_others_still_apply():
    """A non-retryable 4xx must reach the per-id boundary, not the TaskGroup."""
    session = FakeBlockSession(raise_for={"2": _http_error(400, "bad request")})

    outcome = await curation.block_artists(session, ["1", "2", "3"])

    assert outcome.applied == ["1", "3"]
    assert outcome.rejected == ["2"]


# --- Verify block writes by re-reading the blocklist, not by the 200 alone.
#
# The probe confirmed a 200 with an empty body. Trusting that status alone
# is what the probe warned against: a future Tidal change could turn the
# 200 into a silent no-op and the engine would report a successful block
# that never happened. The reconciliation reads the blocklist once after
# the write and moves ids that were reported applied but are absent into
# rejected. For unblock the predicate inverts: an id still present was
# not removed.


async def test_a_200_that_did_nothing_lands_in_rejected_for_block():
    """The probe warned that a 200 with no body could turn into a silent
    no-op. The engine reads the blocklist after the writes and moves any
    applied id that is not present into rejected.
    """
    session = FakeBlockSession()

    import tidal_sync.engine.curation as cur_module

    async def _stub(_session):
        return ["1"]

    original = cur_module.fetch_blocked_artist_ids
    cur_module.fetch_blocked_artist_ids = _stub  # type: ignore[assignment]
    try:
        outcome = await curation.block_artists(session, ["1", "2"])
    finally:
        cur_module.fetch_blocked_artist_ids = original  # type: ignore[assignment]

    assert outcome.applied == ["1"]
    assert outcome.rejected == ["2"]


async def test_an_id_still_in_the_blocklist_lands_in_rejected_for_unblock():
    """The mirror case for unblock: a 204 that did not remove the id.
    The engine reads the blocklist and moves any applied id that is still
    present into rejected.
    """
    session = FakeBlockSession()

    import tidal_sync.engine.curation as cur_module

    async def _stub(_session):
        return ["1", "2"]

    original = cur_module.fetch_blocked_artist_ids
    cur_module.fetch_blocked_artist_ids = _stub  # type: ignore[assignment]
    try:
        outcome = await curation.unblock_artists(session, ["1", "2"])
    finally:
        cur_module.fetch_blocked_artist_ids = original  # type: ignore[assignment]

    assert outcome.rejected == ["1", "2"]
    assert outcome.applied == []


async def test_a_failed_confirmation_read_does_not_confirm_an_unblock():
    """The reconciliation exists because a 2xx cannot be trusted. An empty read
    that failed is not evidence of removal, and must not be reported as one.
    """
    session = FakeBlockSession()
    session.blocklist_raises = RuntimeError("blocklist read failed")

    outcome = await curation.unblock_artists(session, ["7", "8"])

    assert outcome.applied == [], "an unverifiable write is not a success"
    assert outcome.rejected == ["7", "8"]


async def test_a_genuinely_empty_blocklist_still_confirms_an_unblock():
    """Distinguishing failure from emptiness is the whole point: an empty
    blocklist after unblocking two ids is exactly the expected outcome.
    """
    session = FakeBlockSession()
    session.blocklist = []

    outcome = await curation.unblock_artists(session, ["7", "8"])

    assert outcome.applied == ["7", "8"]
    assert outcome.rejected == []


async def test_unblock_artists_refuses_a_batch_over_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both write verbs spend the same rate budget, so both are capped.

    ``--prune`` sends the whole unlisted set here, and that set comes from
    the live blocklist rather than from a subscription, so nothing upstream
    bounds it.
    """
    calls: list[str] = []
    monkeypatch.setattr(curation, "_apply_per_id", lambda *a, **k: calls.append("wrote"))

    with pytest.raises(BatchTooLarge):
        await curation.unblock_artists(object(), [str(i) for i in range(5001)])

    assert calls == [], "no write may be attempted once the ceiling is breached"
