"""Pin the behaviour of the F-21 mixes path in export_algorithmic_mixes_to_disk.

The redundant-expr error in the exporter was caused by a dead
``callable()`` guard on ``session.mixes``. Closing F-21 removed the guard
and the TODO comment above it; these tests pin what that branch actually
does so a future refactor cannot silently bypass ``execute_network`` or
swap the mixes callable for a non-callable payload.

Each test builds a session whose ``user`` does not expose ``favorites``
so the favourites branches are skipped and the session.mixes fallback
branch is reached directly. ``execute_network`` itself is monkeypatched
to a recording stub so no real tidalapi call is made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tidal_sync.engine import exporter


def _session_with_mixes(mixes_value: list[Any]) -> Any:
    """Build a session-shaped object whose ``user`` lacks ``favorites``.

    The exporter first checks ``hasattr(user, "favorites")``. A user
    object with no ``favorites`` attribute skips that whole block, which
    is required to reach the session.mixes fallback branch. ``session.mixes``
    is a bound method on the real tidalapi.Session; for a fake we expose
    a plain callable that closes over ``mixes_value`` so the executor
    can call it.
    """

    class _User:
        pass

    class _Session:
        def __init__(self, value: list[Any]) -> None:
            self.user = _User()
            self._value = value

        def mixes(self) -> list[Any]:
            return self._value

    return _Session(mixes_value)


async def test_session_mixes_is_fetched_through_execute_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mixes branch must fetch through execute_network exactly once."""

    calls = []

    async def fake_execute_network(func: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(exporter, "execute_network", fake_execute_network)

    session = _session_with_mixes(["mix-a", "mix-b"])

    await exporter.export_algorithmic_mixes_to_disk(session, tmp_path)

    assert len(calls) == 1, "the mixes branch must fetch through execute_network exactly once"
    assert calls[0]() == ["mix-a", "mix-b"], "the callable handed over must yield the mixes"


async def test_session_mixes_result_flows_into_all_stations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The result of execute_network must reach the serialisation pipeline.

    Pinning only the call shape is not enough: the returned list must be
    handed to ``fetch_and_serialise_tracks`` once per item so a future
    refactor cannot accidentally drop the result or short-circuit it.
    The station fakes here expose a ``tracks`` callable so the inner
    per-station branch picks it up and reaches the serialiser.
    """

    written: list[str] = []

    class _FakeStation:
        def __init__(self, label: str, tracks: list[Any]) -> None:
            self.name = label
            self.title = label
            self.id = label
            self._tracks = tracks

        def tracks(self, **_kwargs: Any) -> list[Any]:
            return self._tracks

    async def fake_execute_network(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    async def fake_fetch_and_serialise_tracks(
        name: str,
        target_dir: Any,
        fetch_items_coro: Any,
        log_type: str,
        allocator: Any,
    ) -> int:
        written.append(name)
        return 0

    monkeypatch.setattr(exporter, "execute_network", fake_execute_network)
    monkeypatch.setattr(exporter, "fetch_and_serialise_tracks", fake_fetch_and_serialise_tracks)

    expected_stations = [
        _FakeStation(label="mix-a", tracks=["t1"]),
        _FakeStation(label="mix-b", tracks=["t2"]),
    ]
    session = _session_with_mixes(expected_stations)

    await exporter.export_algorithmic_mixes_to_disk(session, tmp_path)

    assert written == ["mix-a", "mix-b"], (
        "every station returned by execute_network must be serialised exactly once"
    )
