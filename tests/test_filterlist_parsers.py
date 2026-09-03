"""Filter-list source parsers.

Each parser takes raw bytes plus a format hint and returns a uniform
list of ``(tidal_id, name)`` tuples. The contract is the only thing the
rest of the engine sees, so this file is the executable specification
of that contract.
"""

from __future__ import annotations

import pytest

from tidal_sync.engine.filterlist import (
    SUPPORTED_FORMATS,
    FormatError,
    detect_format,
    parse_filter_list,
)

TXT_FORMATS = ("txt", "TXT")
CSV_FORMATS = ("csv", "CSV")
JSON_FORMATS = ("json", "JSON")


# ---------------------------------------------------------------------------
# txt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", TXT_FORMATS)
def test_txt_bare_ids_emit_pairs_in_order(fmt: str) -> None:
    """Bare numeric ids become pairs with an empty name slot."""
    data = b"4894212\n8107285\n"
    assert parse_filter_list(data, fmt) == [
        ("4894212", ""),
        ("8107285", ""),
    ]


@pytest.mark.parametrize("fmt", TXT_FORMATS)
def test_txt_browse_urls_resolve_to_id(fmt: str) -> None:
    """Browse URLs are reduced to their bare id."""
    data = b"https://tidal.com/browse/artist/4894212\n"
    assert parse_filter_list(data, fmt) == [("4894212", "")]


def test_txt_comment_and_blank_lines_are_skipped() -> None:
    """Lines starting with '#' and blank lines do not emit rows."""
    data = b"# this is a comment\n\n   \n4894212\n"
    assert parse_filter_list(data, "txt") == [("4894212", "")]


def test_txt_trailing_whitespace_is_tolerated() -> None:
    """Trailing whitespace per line is stripped before id extraction."""
    data = b" 4894212 \n"
    assert parse_filter_list(data, "txt") == [("4894212", "")]


def test_txt_malformed_line_raises_format_error_with_line_number() -> None:
    """A line that is neither an id nor a URL is a FormatError at that line."""
    data = b"4894212\nnot-a-number\n"
    with pytest.raises(FormatError) as excinfo:
        parse_filter_list(data, "txt")
    assert "line 2" in str(excinfo.value)


def test_txt_extract_tidal_id_error_carries_line_number() -> None:
    """An unparseable line surfaces the underlying extract_tidal_id error
    as a FormatError with the offending line number."""
    # Two non-blank, non-comment lines: the first is valid, the second
    # is the line that triggers the conversion. Line 2 is the offender.
    data = b"4894212\nhttps://example.com/no-id-here\n"
    with pytest.raises(FormatError) as excinfo:
        parse_filter_list(data, "txt")
    assert "line 2" in str(excinfo.value)


# ---------------------------------------------------------------------------
# csv
# ---------------------------------------------------------------------------


def test_csv_pr1_style_export_loads_through_artist_row() -> None:
    """A PR 1 style ``artist_name,tidal_id`` export loads via ArtistRow."""
    data = b"artist_name,tidal_id\nBad Bunny,4894212\nRosalia,8107285\n"
    assert parse_filter_list(data, "csv") == [
        ("4894212", "Bad Bunny"),
        ("8107285", "Rosalia"),
    ]


def test_csv_rows_with_empty_tidal_id_are_skipped() -> None:
    """Empty-id rows are dropped, not emitted as ``('', name)``."""
    data = b"artist_name,tidal_id\nBad Bunny,4894212\nNo Id,\nRosalia,8107285\n"
    assert parse_filter_list(data, "csv") == [
        ("4894212", "Bad Bunny"),
        ("8107285", "Rosalia"),
    ]


# ---------------------------------------------------------------------------
# json
# ---------------------------------------------------------------------------


def test_json_object_array_with_id_and_name() -> None:
    """An object array with ``tidal_id`` and ``artist_name`` keys."""
    data = b'[{"tidal_id": "1", "artist_name": "A"}]'
    assert parse_filter_list(data, "json") == [("1", "A")]


def test_json_bare_id_array_yields_empty_names() -> None:
    """A bare array of ids becomes a list of pairs with empty name slots."""
    data = b'["1", "2"]'
    assert parse_filter_list(data, "json") == [("1", ""), ("2", "")]


def test_json_top_level_dict_is_rejected() -> None:
    """A top-level dict parses with orjson but is not a list, so it is
    rejected at the parser layer."""
    data = b'{"a": 1}'
    with pytest.raises(FormatError):
        parse_filter_list(data, "json")


def test_json_top_level_scalar_is_rejected() -> None:
    """A top-level scalar parses with orjson but is not a list."""
    data = b"123"
    with pytest.raises(FormatError):
        parse_filter_list(data, "json")


def test_json_non_string_id_is_rejected() -> None:
    """A non-string id in the array is rejected. This is the policy
    chosen for the parser: ids are strings, and anything else is a
    format error rather than a silent coercion."""
    data = b'[{"tidal_id": 1, "artist_name": "A"}]'
    with pytest.raises(FormatError):
        parse_filter_list(data, "json")


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_unsupported_format_raises_format_error() -> None:
    """An unsupported format hint raises FormatError at parse time."""
    with pytest.raises(FormatError):
        parse_filter_list(b"4894212\n", "xml")


# ---------------------------------------------------------------------------
# parser error boundary (Task 7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "fmt"),
    [
        (b"not json", "json"),
        (b"# probe", "json"),
        (b"\xff\xfe\x00bad", "txt"),
        (b"", "csv"),
        (b"artist_name,tidal_id\n", "csv"),
    ],
)
def test_every_malformed_payload_raises_format_error(data: bytes, fmt: str) -> None:
    """FormatError is the documented failure mode; nothing else may escape.

    plan_apply isolates one bad subscription from its siblings by catching
    FormatError. Any other exception type escapes that handler and takes
    the whole run down.
    """
    with pytest.raises(FormatError):
        parse_filter_list(data, fmt)


@pytest.mark.parametrize("fmt", ["txt", "csv", "json"])
def test_ids_are_validated_in_every_format(fmt: str) -> None:
    """A non-numeric id must be refused whichever format carried it.

    unblock_artists interpolates an id into the request path, so an
    unvalidated id from a third-party list reaches a URL.
    """
    payloads = {
        "txt": b"../../evil\n",
        "csv": b"artist_name,tidal_id\nEvil,../../evil\n",
        "json": b'["../../evil"]',
    }
    with pytest.raises(FormatError):
        parse_filter_list(payloads[fmt], fmt)


def test_txt_tolerates_a_utf8_bom() -> None:
    """A list saved by a Windows editor starts with a BOM.

    This project's own CSV exporter writes utf-8-sig, so a blocked-artists
    export is the most likely first filter list a user subscribes to.
    """
    assert parse_filter_list(b"\xef\xbb\xbf4894212\n", "txt") == [("4894212", "")]


def test_detect_format_reads_path_not_query_string() -> None:
    """A cache-busting query string must not change the detected format.

    ``urlparse`` splits the URL at the ``?`` so the extension is read
    from the path only. The format hint the parser sees is the same
    whether or not the operator pinned a version.
    """
    assert detect_format("https://example.com/list.txt?v=2") == "txt"


def test_supported_formats_lists_every_parser() -> None:
    """``SUPPORTED_FORMATS`` is the single source of truth.

    The dispatch table, the CLI's ``--from-list`` flag, and the test
    parametrisation all read from it; a parser that is not in the tuple
    cannot be invoked by name.
    """
    assert set(SUPPORTED_FORMATS) == {"txt", "csv", "json"}
