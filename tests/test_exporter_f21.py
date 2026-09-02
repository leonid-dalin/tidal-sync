"""Pin the behaviour of the F-21 mixes path in export_algorithmic_mixes_to_disk.

The redundant-expr error at exporter.py line 246 was caused by a dead
``callable()`` guard on ``session.mixes``. Closing F-21 removed the guard
and the TODO comment above it; these tests pin what that branch actually
does so a future refactor cannot silently bypass ``execute_network`` or
swap the mixes callable for a non-callable payload.

Each test builds a session whose ``user`` does not expose ``favorites``
so the favourites branches at exporter.py:228-241 are skipped and the
mixes branch at exporter.py:243-248 is reached directly. ``execute_network``
itself is monkeypatched to a recording stub so no real tidalapi call is
made.
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
    is required to reach the ``session.mixes`` branch at line 244.
    ``session.mixes`` is a bound method on the real tidalapi.Session; for
    a fake we expose a plain callable that closes over ``mixes_value``
    so the executor can call it.
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


async def test_session_mixes_path_calls_execute_network_with_the_callable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mixes branch must hand the session.mixes callable to execute_network.

    After F-21 the guard is gone, but the branch still has to route the
    mixes fetch through execute_network and use the bound method as the
    payload source. This test pins both behaviours: execute_network is
    called once, and the callable passed is the very one exposed as
    ``session.mixes`` (verified via ``__self__`` and ``__func__`` because
    bound methods are created fresh on every attribute access).
    """

    captured: dict[str, Any] = {}

    async def fake_execute_network(func: Any, *args: Any, **kwargs: Any) -> Any:
        captured["func"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs
        return func(*args, **kwargs)

    monkeypatch.setattr(exporter, "execute_network", fake_execute_network)

    expected_mixes = ["mix-a", "mix-b"]
    session = _session_with_mixes(expected_mixes)

    await exporter.export_algorithmic_mixes_to_disk(session, tmp_path)

    assert "func" in captured, "execute_network was never reached on the mixes branch"
    func = captured["func"]
    assert callable(func), "execute_network must be handed a callable"
    # Bound methods are created on each attribute access, so identity is
    # ``__self__`` (the underlying object) plus ``__func__`` (the method).
    assert getattr(func, "__self__", None) is session, (
        "execute_network must receive the bound method of the session itself"
    )
    assert getattr(func, "__func__", None) is type(session).mixes, (
        "execute_network must receive the bound method named mixes"
    )


async def test_session_mixes_result_flows_into_all_stations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The result of execute_network must reach the serialisation pipeline.

    Pinning only the call shape is not enough: the returned list must be
    handed to ``fetch_and_serialise_tracks`` once per item so a future
    refactor cannot accidentally drop the result or short-circuit it.
    The station fakes here expose a ``tracks`` callable so the inner
    branch at exporter.py:269-272 picks it up and reaches line 305.
    """

    written: list[str] = []
    serialised_per_station: list[Any] = []

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
        serialised_per_station.append(fetch_items_coro)
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


def test_session_mixes_fake_exposes_a_real_bound_method() -> None:
    """Sanity check: ``_session_with_mixes`` builds a bound method.

    If a future refactor weakens the helper so ``session.mixes`` is no
    longer a bound method, the tests above would silently degrade. This
    assertion keeps the contract honest.
    """

    session = _session_with_mixes([])
    mixes_attr = session.mixes
    assert callable(mixes_attr), "session.mixes must be callable"
    assert getattr(mixes_attr, "__self__", None) is session
    assert getattr(mixes_attr, "__func__", None) is type(session).mixes
