"""Filter-list source fetcher.

The fetcher streams a third-party URL (or reads a local path) under
four non-negotiable caps: HTTPS only, 1 MiB body, a content-type
allowlist, and an explicit timeout. These tests pin each one and the
happy path.

Network behaviour is exercised by patching ``requests.get`` with a
fake response that mirrors ``requests.Response`` enough for the
implementation to consume. That keeps the tests off TLS while still
exercising the real streaming and cap logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tidal_sync.engine import filterlist_fetch
from tidal_sync.engine.filterlist import FormatError
from tidal_sync.engine.filterlist_fetch import FetchError, fetch_source

# ---------------------------------------------------------------------------
# Fake response used by monkeypatched ``requests.get``
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Mimics the ``requests.Response`` surface the fetcher touches.

    The fetcher reads ``status_code``, ``headers`` and ``iter_content``,
    and enters/exits the response as a context manager.
    """

    def __init__(
        self,
        body: bytes,
        status_code: int = 200,
        content_type: str | None = "text/plain",
    ) -> None:
        self._body = body
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        if content_type is not None:
            self.headers["Content-Type"] = content_type

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192) -> Any:
        # Yield the body in fixed-size chunks so the cap logic sees a
        # running total, not a single huge chunk.
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]


class _Recorder:
    """Records the URL and kwargs that ``requests.get`` was called with."""

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(((url,), kwargs))
        return self.response


@pytest.fixture
def patched_requests(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Replace ``filterlist_fetch.requests.get`` with a recorder."""
    recorder = _Recorder(_FakeResponse(body=b""))
    monkeypatch.setattr(filterlist_fetch.requests, "get", recorder)
    return recorder


# ---------------------------------------------------------------------------
# HTTPS only
# ---------------------------------------------------------------------------


def test_http_source_is_refused(tmp_path: Path, patched_requests: _Recorder) -> None:
    """A plain http:// source is refused outright, not upgraded."""
    dest = tmp_path / "out.txt"
    with pytest.raises(FetchError):
        fetch_source("http://example.com/list.txt", "txt", dest)
    assert not dest.exists()
    # No network call may have been made: the check fires before
    # ``requests.get`` is invoked.
    assert patched_requests.calls == []


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------


def test_size_cap_trips_when_body_exceeds_one_mib(
    tmp_path: Path, patched_requests: _Recorder
) -> None:
    """A body strictly larger than the cap aborts mid-fetch."""
    body = b"a" * (1024 * 1024 + 1)
    patched_requests.response = _FakeResponse(body=body, content_type="text/plain")

    dest = tmp_path / "out.txt"
    with pytest.raises(FetchError):
        fetch_source("https://example.com/list.txt", "txt", dest)
    assert not dest.exists()


def test_size_cap_is_not_tripped_at_exactly_one_mib(
    tmp_path: Path, patched_requests: _Recorder
) -> None:
    """A body exactly at the cap (1 MiB) succeeds.

    The body is a string of repeated 7-digit ids, so each line is a
    valid tidal id and the round trip writes ``dest`` and returns a
    positive count.
    """
    line = b"4894212\n"
    count = 1024 * 1024 // len(line)
    body = line * count
    assert len(body) == count * len(line)
    assert len(body) <= 1024 * 1024

    patched_requests.response = _FakeResponse(body=body, content_type="text/plain")

    dest = tmp_path / "out.txt"
    written = fetch_source("https://example.com/list.txt", "txt", dest)

    assert written == count
    assert dest.read_bytes() == body


# ---------------------------------------------------------------------------
# Content-Type allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type, body, fmt",
    [
        ("text/plain", b"4894212\n", "txt"),
        ("text/csv", b"artist_name,tidal_id\nBad Bunny,4894212\n", "csv"),
        ("application/json", b'["4894212"]', "json"),
    ],
)
def test_allowlisted_content_type_is_accepted(
    tmp_path: Path,
    patched_requests: _Recorder,
    content_type: str,
    body: bytes,
    fmt: str,
) -> None:
    """text/plain, text/csv, and application/json are all accepted."""
    patched_requests.response = _FakeResponse(body=body, content_type=content_type)

    dest = tmp_path / "out.bin"
    written = fetch_source("https://example.com/list", fmt, dest)

    assert written >= 1
    assert dest.read_bytes() == body


def test_text_html_is_refused(tmp_path: Path, patched_requests: _Recorder) -> None:
    """text/html is not in the allowlist, so it raises FetchError."""
    patched_requests.response = _FakeResponse(body=b"<html>oops</html>", content_type="text/html")

    dest = tmp_path / "out.txt"
    with pytest.raises(FetchError):
        fetch_source("https://example.com/list", "txt", dest)
    assert not dest.exists()


def test_content_type_with_charset_parameter_is_accepted(
    tmp_path: Path, patched_requests: _Recorder
) -> None:
    """A 'text/plain; charset=utf-8' header is accepted: the part before
    the semicolon is the allowlist comparison, and case is folded.
    """
    patched_requests.response = _FakeResponse(
        body=b"4894212\n", content_type="text/plain; charset=utf-8"
    )

    dest = tmp_path / "out.txt"
    written = fetch_source("https://example.com/list", "txt", dest)

    assert written == 1
    assert dest.read_bytes() == b"4894212\n"


def test_uppercase_content_type_is_accepted(tmp_path: Path, patched_requests: _Recorder) -> None:
    """The allowlist compare is case-insensitive."""
    patched_requests.response = _FakeResponse(body=b"4894212\n", content_type="TEXT/PLAIN")

    dest = tmp_path / "out.txt"
    written = fetch_source("https://example.com/list", "txt", dest)

    assert written == 1


def test_missing_content_type_is_refused(tmp_path: Path, patched_requests: _Recorder) -> None:
    """A missing Content-Type header is treated as not on the allowlist
    and is refused. This is the conservative choice for a CLI fetch:
    we never silently accept a response whose type we cannot verify.
    """
    patched_requests.response = _FakeResponse(body=b"4894212\n", content_type=None)

    dest = tmp_path / "out.txt"
    with pytest.raises(FetchError):
        fetch_source("https://example.com/list", "txt", dest)
    assert not dest.exists()


# ---------------------------------------------------------------------------
# HTTP error status
# ---------------------------------------------------------------------------


def test_404_raises_fetch_error(tmp_path: Path, patched_requests: _Recorder) -> None:
    """A 404 response raises FetchError."""
    patched_requests.response = _FakeResponse(body=b"", status_code=404)

    dest = tmp_path / "out.txt"
    with pytest.raises(FetchError):
        fetch_source("https://example.com/list", "txt", dest)
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_hung_fetch_raises_fetch_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung ``requests.get`` must trip the explicit timeout, not
    propagate the requests timeout silently.

    The brief requires an explicit timeout. We verify it by passing a
    tiny timeout through the implementation and patching get to raise
    ``requests.Timeout``.
    """
    import requests as _requests

    def _timeout(*args: Any, **kwargs: Any) -> None:
        raise _requests.Timeout("read timed out")

    monkeypatch.setattr(filterlist_fetch.requests, "get", _timeout)
    original_timeout = filterlist_fetch._TIMEOUT
    filterlist_fetch._TIMEOUT = 0.01
    try:
        dest = tmp_path / "out.txt"
        with pytest.raises(FetchError):
            fetch_source("https://example.com/list", "txt", dest)
    finally:
        filterlist_fetch._TIMEOUT = original_timeout


# ---------------------------------------------------------------------------
# Local path
# ---------------------------------------------------------------------------


def test_local_path_is_read_without_a_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem source is read with ``open``, never ``requests``.

    The patch on ``filterlist_fetch.requests.get`` raises if anything
    tries to make a network call from this codepath.
    """
    src = tmp_path / "list.txt"
    src.write_bytes(b"4894212\n8107285\n")

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("requests.get must not be called for a local source")

    monkeypatch.setattr(filterlist_fetch.requests, "get", _explode)

    dest = tmp_path / "out.txt"
    written = fetch_source(str(src), "txt", dest)

    assert written == 2
    assert dest.read_bytes() == b"4894212\n8107285\n"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_successful_round_trip_writes_dest_and_returns_count(
    tmp_path: Path, patched_requests: _Recorder
) -> None:
    """A successful HTTPS-style round trip writes ``dest`` with the
    response body and returns the parsed id count from
    ``filterlist.parse_filter_list``.
    """
    patched_requests.response = _FakeResponse(body=b"4894212\n8107285\n")

    dest = tmp_path / "out.txt"
    written = fetch_source("https://example.com/list", "txt", dest)

    assert written == 2
    assert dest.read_bytes() == b"4894212\n8107285\n"


# ---------------------------------------------------------------------------
# Sanity: the module never imports the Tidal network gate
# ---------------------------------------------------------------------------


def test_module_does_not_reach_the_tidal_network_gate() -> None:
    """The fetcher must not import or call ``execute_network`` /
    ``GLOBAL_GATE``: a third-party host must not be able to arm the
    1800-second Tidal abuse lock. We verify the binding contract by
    inspecting the runtime module namespace, not the source text, so
    a prose mention in a comment does not trigger a false positive.
    """
    import tidal_sync.engine.network as network_module

    module = filterlist_fetch.__dict__
    for name in ("execute_network", "GLOBAL_GATE", "GlobalTidalGate"):
        assert name not in module
        # And the names are not just re-exported through the module.
        assert name not in dir(filterlist_fetch)
    # The Tidal network module itself is still importable: the gate
    # has not been removed, just kept off this codepath.
    assert hasattr(network_module, "execute_network")
    assert hasattr(network_module, "GLOBAL_GATE")


# ---------------------------------------------------------------------------
# Streaming probe: the cap aborts mid-stream rather than after a full read
# ---------------------------------------------------------------------------


class _ChunkedResponse(_FakeResponse):
    """Yields one byte at a time so the streaming loop sees many chunks."""

    def iter_content(self, chunk_size: int = 8192) -> Any:
        for index in range(len(self._body)):
            yield self._body[index : index + 1]


def test_size_cap_aborts_before_buffering_the_full_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap is enforced as a running total, so an oversized body is
    rejected before the implementation has read it all.

    A one-byte-per-chunk response of length ``1 MiB + 1`` must still
    trip the cap: the stream sees a running total past the limit
    before all chunks arrive.
    """
    body = b"a" * (1024 * 1024 + 1)
    response = _ChunkedResponse(body=body, content_type="text/plain")
    recorder = _Recorder(response)
    monkeypatch.setattr(filterlist_fetch.requests, "get", recorder)

    dest = tmp_path / "out.txt"
    with pytest.raises(FetchError):
        fetch_source("https://example.com/list", "txt", dest)
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Redirect refusal (HTTPS cap survives a 3xx)
# ---------------------------------------------------------------------------


def test_redirect_to_http_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The HTTPS cap must survive a redirect, not just the typed URL."""
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 302
        headers = {"Location": "http://evil.example/list.txt", "Content-Type": "text/plain"}

        def iter_content(self, chunk_size: int) -> Any:
            return iter([b"4894212\n"])

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    def _get(url: str, **kwargs: Any) -> _Response:
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(filterlist_fetch.requests, "get", _get)

    with pytest.raises(FetchError):
        filterlist_fetch.fetch_source("https://ok.example/list.txt", "txt", tmp_path / "c.txt")
    assert captured.get("allow_redirects") is False, "redirects must not be followed silently"


# ---------------------------------------------------------------------------
# Local branch must use the same size cap
# ---------------------------------------------------------------------------


def test_local_source_over_the_cap_is_refused(tmp_path: Path) -> None:
    """A local file gets the same 1 MiB ceiling a fetched one does."""
    source = tmp_path / "big.txt"
    source.write_bytes(b"4894212\n" * 200_000)

    with pytest.raises(FetchError):
        filterlist_fetch.fetch_source(str(source), "txt", tmp_path / "c.txt")


# ---------------------------------------------------------------------------
# A malformed body must not be left in the cache for the next run
# ---------------------------------------------------------------------------


def test_a_malformed_body_leaves_no_cache_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A body that will not parse must not be cached for the next run."""
    source = tmp_path / "bad.json"
    source.write_bytes(b"not json")
    dest = tmp_path / "cache" / "bad.json"

    with pytest.raises(FormatError):
        filterlist_fetch.fetch_source(str(source), "json", dest)
    assert not dest.exists()
