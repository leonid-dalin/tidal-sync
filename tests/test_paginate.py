"""The shared paginate() helper covers the four former hand-rolled loops.

These tests pin the duplicate-page guard, the A,B,A,B cycle guard (which the
old last-page-only guard failed), the short-page stop, and advancing the offset
by the rows actually kept rather than the page size.
"""

import asyncio

import pytest

from tidal_sync.engine.network import _id_key, paginate, paginate_sync


class _Item:
    """A minimal item with a stable id, for keying and dedup checks."""

    def __init__(self, item_id: int):
        self.id = item_id


def _pages(*pages: list[_Item]) -> tuple[list[list[_Item]], list[int]]:
    """Builds a fetch_page that replays fixed pages and records offsets.

    Returns (fetch_page, offsets) where offsets is a list populated with every
    offset paginate requested, so callers can assert how far it advanced.
    """
    calls: list[int] = []

    def fetch_page(offset: int, limit: int) -> list[_Item]:
        calls.append(offset)
        index = len(calls) - 1
        return pages[index] if index < len(pages) else []

    return fetch_page, calls


def test_duplicate_page_stops_after_one():
    """A server that returns page A twice must stop after the first page."""
    page_a = [_Item(1), _Item(2)]
    fetch_page, _ = _pages(page_a, page_a)

    result = asyncio.run(paginate(fetch_page, page_size=50, key=_id_key))

    assert [item.id for item in result] == [1, 2]


def test_paginate_sync_advances_by_page_size_and_cycles_out():
    """The sync twin dedupes and cycles out while paging by requested offset."""
    page_a = [_Item(1), _Item(2)]
    page_b = [_Item(3), _Item(4)]
    fetch_page, calls = _pages(page_a, page_b, page_a)

    result = paginate_sync(fetch_page, page_size=2, key=_id_key)

    assert [item.id for item in result] == [1, 2, 3, 4]
    # Advance is by page size: 0, 2, 4; the repeated page A at offset 4 stops it.
    assert calls == [0, 2, 4]


def test_cycle_ab_ab_stops_after_ab():
    """The old last-page-only guard looped forever on A,B,A,B; this must not."""
    page_a = [_Item(1), _Item(2)]
    page_b = [_Item(3), _Item(4)]
    fetch_page, _ = _pages(page_a, page_b, page_a, page_b)

    result = asyncio.run(paginate(fetch_page, page_size=50, key=_id_key))

    assert [item.id for item in result] == [1, 2, 3, 4]


def test_short_page_stops():
    """A page shorter than the page size ends pagination."""
    full = [_Item(i) for i in range(50)]
    short = [_Item(i) for i in range(50, 60)]
    fetch_page, _ = _pages(full, short)

    result = asyncio.run(paginate(fetch_page, page_size=50, key=_id_key))

    assert [item.id for item in result] == list(range(60))


def test_offset_advances_by_rows_kept_not_page_size():
    """When the server drops a row each page, offset must track kept rows.

    The fake serves a finite id space (1..30) and omits id 3 from whichever
    page would contain it (region-locked). Each 10-row page therefore keeps 9
    rows. If paginate advanced by the page size (10) it would overshoot row 10
    and stop early; advancing by len(fresh) it walks the whole space and the
    server returns empty once offset passes 30.
    """
    offsets_seen: list[int] = []

    def fetch_page(offset: int, limit: int) -> list[_Item]:
        offsets_seen.append(offset)
        if offset >= 30:
            return []
        page = [_Item(i) for i in range(offset + 1, offset + 11) if i != 3 and i <= 30]
        return page

    result = asyncio.run(paginate(fetch_page, page_size=10, key=_id_key))

    # id 3 is region-locked, so the server never returns it.
    assert {item.id for item in result} == set(range(1, 31)) - {3}
    # Kept pages advance 0, 9, 18, 27; the trailing probe hits empty.
    assert offsets_seen[:-1] == [0, 9, 18, 27]


def test_stop_on_short_page_false_continues_until_empty():
    """Without stop_on_short_page, a short page alone does not end pagination."""
    full = [_Item(i) for i in range(50)]
    short = [_Item(i) for i in range(50, 60)]
    empty: list[_Item] = []
    fetch_page, _ = _pages(full, short, empty)

    result = asyncio.run(paginate(fetch_page, page_size=50, key=_id_key, stop_on_short_page=False))

    assert [item.id for item in result] == list(range(60))


def test_keyless_items_still_terminate():
    """Items without a usable id fall back to identity and still terminate."""
    # Objects with no .id attribute: key falls back to id(obj), which is unique
    # per object, so every page is "fresh" until an empty page ends it.
    page1 = [object() for _ in range(3)]
    page2: list[object] = []
    fetch_page, _ = _pages(page1, page2)

    result = asyncio.run(paginate(fetch_page, page_size=50, key=_id_key))

    assert len(result) == 3


@pytest.mark.asyncio
async def test_async_fetch_page_is_awaited():
    """A fetch_page that returns a coroutine is awaited before inspection."""
    page = [_Item(1), _Item(2)]
    seen_offsets: list[int] = []

    async def fetch_page(offset: int, limit: int) -> list[_Item]:
        seen_offsets.append(offset)
        await asyncio.sleep(0)
        return page if offset == 0 else []

    result = await paginate(fetch_page, page_size=50, key=_id_key)

    assert [item.id for item in result] == [1, 2]
    # Page 0 keeps 2 rows, so the next probe is offset 2 and returns empty.
    assert seen_offsets == [0, 2]
