"""The shared paginate() helper covers the four former hand-rolled loops.

These tests pin the page-signature guard (a repeated page or an A,B,A,B cycle
stops pagination), the short-page stop, the duplicate-survival rule (a track
legitimately repeated across pages stays in the export), and the offset
advancing by len(page) so a server that drops region-locked rows does not
silently skip the next page.
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


def test_paginate_sync_advances_by_len_page_and_cycles_out():
    """The sync twin pages by len(page) and exits when a page repeats."""
    page_a = [_Item(1), _Item(2)]
    page_b = [_Item(3), _Item(4)]
    fetch_page, calls = _pages(page_a, page_b, page_a)

    result = paginate_sync(fetch_page, page_size=2, key=_id_key)

    assert [item.id for item in result] == [1, 2, 3, 4]
    # Advance is by len(page): 0, 2, 4; the repeated page A at offset 4 stops it.
    assert calls == [0, 2, 4]


def test_a_legitimately_repeated_item_survives_pagination():
    """A playlist may hold the same track twice. Export must not collapse it.

    Pages advance by len(page) (0, 2, 4). At offset 4 the server is empty
    and pagination ends. id 1 must appear twice in the result, in the order
    the server delivered it.
    """
    pages = {
        0: [_Item(1), _Item(2)],
        2: [_Item(1), _Item(3)],
        4: [],
    }

    result = paginate_sync(lambda offset, limit: pages.get(offset, []), page_size=2)

    assert [item.id for item in result] == [1, 2, 1, 3]


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


def test_offset_advances_by_len_page_so_dropped_rows_do_not_skip():
    """When the server drops a row each page, offset must track delivered rows.

    The fake serves ids 1..30 and drops id 3 (region-locked) from every
    page that contains it. Advancing by the page size (10) would skip past
    ids 11 onward once id 3 is dropped; advancing by len(page) walks the
    whole space and the server returns empty once offset passes 30.
    """
    offsets_seen: list[int] = []

    def fetch_page(offset: int, limit: int) -> list[_Item]:
        offsets_seen.append(offset)
        if offset >= 30:
            return []
        page = [_Item(i) for i in range(offset + 1, offset + 11) if i != 3 and i <= 30]
        return page

    result = asyncio.run(paginate(fetch_page, page_size=10, key=_id_key))

    # id 3 is region-locked. id 10 sits at the boundary: page 0 returns it
    # (the last of its 9 rows) and the probe at offset 9 returns it again.
    # Comparing lists (not sets) catches a dropped duplicate: a set would
    # hide the bug.
    assert [item.id for item in result] == [
        1,
        2,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
    ]
    # First page drops id 3 and yields 9 rows, so the next probe is offset 9.
    # Subsequent pages are full (10 rows), advancing 19, 29. Offset 29 keeps
    # only id 30 (4 ids past the filter), advancing to 30. Offset 30 empties.
    assert offsets_seen == [0, 9, 19, 29, 30]


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
