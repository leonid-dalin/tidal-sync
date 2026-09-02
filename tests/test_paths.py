"""Filename sanitisation and collision-free path allocation.

Two collections can share a name, and sanitisation can map different
names onto the same string. Either way two concurrent writers opening
one path will truncate each other.
"""

from pathlib import Path

import pytest

from tidal_sync.engine.parser import (
    UniquePathAllocator,
    extract_tidal_id,
    sanitize_filename,
)


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


def test_leading_dots_are_stripped():
    # The caret anchors at the start, so leading dots (a traversal component
    # once separators are replaced) are removed rather than treated literally.
    assert sanitize_filename("...hidden") == "hidden"
    assert sanitize_filename(".git") == "git"
    # Separators are replaced with underscores first, so a dotted path
    # collapses to a single flat name with no traversal component surviving.
    assert sanitize_filename(".../.../secret") == "_..._secret"
    assert not sanitize_filename("...").startswith(".")
    assert sanitize_filename("....") == "untitled"


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


def test_extract_tidal_id_accepts_a_bare_id():
    """Operators paste ids from the URL bar or copy them from the share sheet,
    so the parser must accept both the bare id and the full URL form.
    """
    assert extract_tidal_id("12345") == "12345"


def test_extract_tidal_id_accepts_a_browse_url():
    """The browse.tidal.com URL pattern ends in /track/<id>."""
    assert extract_tidal_id("https://listen.tidal.com/track/12345") == "12345"


def test_extract_tidal_id_accepts_a_listen_url():
    """The listen.tidal.com URL pattern is /track/<id>, same shape as browse."""
    assert extract_tidal_id("https://listen.tidal.com/track/67890") == "67890"


def test_extract_tidal_id_strips_query_and_trailing_slash():
    """Real share links carry tracking query strings and a trailing slash;
    both must be stripped without dropping the id.
    """
    assert extract_tidal_id("https://tidal.com/track/12345/?utm_source=x") == "12345"


def test_extract_tidal_id_raises_on_unparseable_input():
    """A string that contains no id at all is a usage error: the CLI surfaces
    it as Click exit 2 rather than silently dropping it.
    """
    with pytest.raises(ValueError):
        extract_tidal_id("not-a-tidal-thing")
