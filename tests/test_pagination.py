"""Pagination must not degrade to a single page without saying so.

A silently truncated export is data loss dressed up as success.
"""

import pytest

from tidal_sync.engine.network import _fetch_all_sync


def test_internal_type_error_propagates_instead_of_truncating():
    """A TypeError raised inside page 2 must not degrade to 'return page 1'."""

    def api(limit=None, offset=None, **kwargs):
        if offset and offset >= 50:
            raise TypeError("internal tidalapi bug")
        return [object()] if offset == 0 else []

    with pytest.raises(TypeError):
        _fetch_all_sync(api)


def test_unpaginated_endpoint_still_supported():
    def api(**kwargs):
        return [1, 2, 3]

    assert _fetch_all_sync(api) == [1, 2, 3]


def test_pagination_exhausts_all_pages():
    pages = {0: [1, 2], 50: [3, 4], 100: []}

    def api(limit=None, offset=None, **kwargs):
        return pages.get(offset, [])

    assert _fetch_all_sync(api) == [1, 2, 3, 4]
