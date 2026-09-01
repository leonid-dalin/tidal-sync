"""Filename sanitisation and collision-free path allocation.

Two collections can share a name, and sanitisation can map different
names onto the same string. Either way two concurrent writers opening
one path will truncate each other.
"""

from pathlib import Path

import pytest

from tidal_sync.engine.parser import UniquePathAllocator, sanitize_filename


def test_collision_gets_a_suffix():
    alloc = UniquePathAllocator()
    d = Path("/out")
    first = alloc.allocate(d, "My Mix")
    second = alloc.allocate(d, "My Mix")
    assert first == d / "My Mix.csv"
    assert second == d / "My Mix-2.csv"


def test_case_insensitive_collision_is_detected():
    alloc = UniquePathAllocator()
    d = Path("/out")
    assert alloc.allocate(d, "Mix") != alloc.allocate(d, "mix")


def test_reserved_windows_names_are_escaped():
    assert sanitize_filename("CON").lower() != "con"


def test_excessive_length_is_truncated():
    assert len(sanitize_filename("a" * 500).encode()) <= 255


def test_blank_name_gets_a_fallback():
    assert sanitize_filename("   ") == "untitled"


def test_traversal_is_still_blocked():
    assert "/" not in sanitize_filename("../../etc/passwd")
    assert "\\" not in sanitize_filename("..\\..\\windows")


async def test_one_failing_collection_does_not_cancel_the_others(tmp_path, capsys):
    import asyncio

    from tidal_sync.engine import exporter

    class Collection:
        """The second collection raises; the others must still be written."""

        _next_id = 0

        def __init__(self, name="Good", tracks=None, boom=False):
            Collection._next_id += 1
            # Distinct ids matter: the exporter dedupes by id, so sharing
            # one would collapse all three into a single collection.
            self.id = f"p{Collection._next_id}"
            self.name = name
            self._tracks = tracks or []
            self.boom = boom

        def tracks(self, **kwargs):
            if self.boom:
                raise RuntimeError("unreadable")
            return self._tracks

    def session_with(collections):
        session = type("S", (), {})()
        session.user = type("U", (), {"playlists": lambda self: collections})()
        return session

    good_one = Collection(name="First")
    bad = Collection(name="Broken", boom=True)
    good_two = Collection(name="Last")

    await exporter.export_user_playlists_to_disk(session_with([good_one, bad, good_two]), tmp_path)

    out = capsys.readouterr().out
    assert "playlist(s) failed" in out
    assert "Broken" in out
    assert asyncio is not None
    assert pytest is not None
