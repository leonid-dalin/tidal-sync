"""Rate-limit gate mechanics.

The gate is shared by every worker, so a lock held across a sleep
serialises the whole run behind one sleeping coroutine.
"""

import asyncio
import time

from tidal_sync.engine import network


async def test_backoff_uses_a_monotonic_clock():
    """The gate must not be observable via a wall clock that can jump."""
    gate = network.GlobalTidalGate()
    await gate.trigger_backoff(0.05, "test")

    assert gate.backoff_until < time.monotonic() + 1


async def test_trigger_backoff_is_not_blocked_by_a_sleeping_worker():
    gate = network.GlobalTidalGate()
    await gate.trigger_backoff(1.0, "test")

    waits: list[float] = []

    async def waiter():
        await gate.pre_flight_check()

    async def second_trigger():
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await gate.trigger_backoff(1.0, "second signal")
        waits.append(time.monotonic() - started)

    await asyncio.gather(waiter(), second_trigger())

    # Holding the lock across the sleep makes the second trigger wait out the
    # whole window before it can extend it, which is the convoy being fixed.
    assert waits, "the second trigger must have run"
    assert waits[0] < 0.5, f"second trigger blocked for {waits[0]:.2f}s"


async def test_pre_flight_sleeps_outside_the_lock():
    """A second trigger landing mid-sleep must be honoured, not queued."""
    gate = network.GlobalTidalGate()
    await gate.trigger_backoff(1.0, "first")

    waits: list[float] = []
    task = asyncio.create_task(gate.pre_flight_check())
    await asyncio.sleep(0.05)

    started = time.monotonic()
    await gate.trigger_backoff(0.5, "second")
    waits.append(time.monotonic() - started)

    await task
    assert waits[0] < 0.5, f"second trigger blocked for {waits[0]:.2f}s"


async def test_trigger_backoff_only_extends_the_window():
    gate = network.GlobalTidalGate()
    await gate.trigger_backoff(5, "long")
    first = gate.backoff_until

    await gate.trigger_backoff(1, "short")
    assert gate.backoff_until == first
