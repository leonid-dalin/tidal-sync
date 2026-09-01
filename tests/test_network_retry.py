"""Retry classification.

Classification read str(error), so a track id containing "429" froze the
gate for a minute while genuine 5xx and connection errors were never
retried.
"""

import pytest
import requests

from tidal_sync.domain.exceptions import TidalRateLimitError, TidalTransientError
from tidal_sync.engine import network


def _response(status):
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


def _http_403_abuse():
    response = requests.Response()
    response.status_code = 403
    response._content = b'{"userMessage": "abuse"}'
    return response


async def test_track_id_containing_429_does_not_freeze_the_gate():
    """'429' inside an error string must not be read as a rate limit."""

    def api():
        raise RuntimeError("track 429811 not found")

    with pytest.raises(RuntimeError):
        await network.execute_network(api)


async def test_connection_errors_are_retried():
    calls = {"n": 0}

    def api():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("boom")
        return "ok"

    assert await network.execute_network(api, max_retries=5, base_delay=0) == "ok"
    assert calls["n"] == 3


async def test_server_errors_are_retried():
    calls = {"n": 0}

    def api():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _response(503)
        return "ok"

    assert await network.execute_network(api, max_retries=5, base_delay=0) == "ok"


@pytest.mark.parametrize("status", [400, 401, 404, 412])
async def test_client_errors_are_not_retried(status):
    """A 4xx other than 429 cannot succeed on retry.

    HTTPError subclasses RequestException, so a catch-all RequestException
    branch would classify every one of these as transient.
    """
    calls = {"n": 0}

    def api():
        calls["n"] += 1
        raise _response(status)

    with pytest.raises(requests.exceptions.HTTPError):
        await network.execute_network(api, max_retries=5, base_delay=0)

    assert calls["n"] == 1, f"status {status} must not be retried"


async def test_exhausted_retries_raise_a_typed_error():
    def api():
        raise _response(503)

    with pytest.raises(TidalTransientError):
        await network.execute_network(api, max_retries=2, base_delay=0)


async def test_abuse_lock_keeps_its_full_duration():
    """The 403 abuse lock must not collapse to the default backoff."""
    original = network.GLOBAL_GATE
    gate = network.GlobalTidalGate()
    network.GLOBAL_GATE = gate

    try:

        def api():
            response = _http_403_abuse()
            error = requests.exceptions.HTTPError("abuse detected")
            error.response = response
            raise error

        with pytest.raises(TidalRateLimitError):
            await network.execute_network(api, max_retries=1, base_delay=0)

        # A 30 minute lock must survive as a lock, not collapse to a second.
        assert gate.backoff_until > 0
    finally:
        network.GLOBAL_GATE = original
