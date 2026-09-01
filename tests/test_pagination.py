"""Pagination must not degrade to a single page without saying so.

A silently truncated export is data loss dressed up as success.
"""

import pytest

from tidal_sync.engine.network import _fetch_all_sync


def test_internal_type_error_propagates_instead_of_truncating():
    """A TypeError raised on the second page must propagate, not be hidden."""

    calls = {"n": 0}

    def api(limit=None, offset=None, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise TypeError("internal tidalapi bug")
        return [object()] if offset == 0 else []

    with pytest.raises(TypeError):
        _fetch_all_sync(api)


def test_unpaginated_endpoint_still_supported():
    def api(**kwargs):
        return [1, 2, 3]

    assert _fetch_all_sync(api) == [1, 2, 3]


def test_pagination_exhausts_all_pages():
    """A 150-item endpoint served in three full 50-row pages returns all 150."""

    def api(limit=None, offset=None, **kwargs):
        if offset >= 150:
            return []
        return list(range(offset + 1, offset + limit + 1))

    assert _fetch_all_sync(api) == list(range(1, 151))
