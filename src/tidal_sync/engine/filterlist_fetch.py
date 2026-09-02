# tidal-sync: A high-performance tool for backing up and cloning Tidal libraries.
# Copyright (C) 2026 Leonid Dalin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: infoLeonid@protonMail.com
"""Filter-list source fetcher.

Layer rule (gate 3): a filter-list host is a third party, not Tidal.
This module must therefore stay off the Tidal network gate: it does
not import or call into the ``engine.network`` module. A slow or
hostile filter-list host must not be able to arm the 1800-second
Tidal abuse lock. The invariant lives at this layer because the
ownership of "what counts as a Tidal call" is here, and the cap that
makes that ownership safe is the four-cap envelope below.

Caps, all non-negotiable and each pinned by a test:

1. HTTPS only. ``http://`` is refused without retry.
2. 1 MiB per fetch. Streamed; abort the moment the running total
   exceeds the cap rather than buffering the whole body first.
3. Content-Type allowlist: ``text/plain``, ``text/csv``,
   ``application/json``. Comparison ignores any ``;charset=...``
   parameter and folds case. A missing header is a refusal: we never
   accept a response whose type we cannot verify.
4. An explicit timeout. A hung fetch raises ``FetchError`` rather
   than hanging the CLI.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import requests

from .filterlist import parse_filter_list


class FetchError(Exception):
    """Raised when a source cannot be fetched under the four caps."""


# 1 MiB. 1024 * 1024, not 1000 * 1000: the brief says 1 MiB and the
# gates want the binary unit.
_MAX_BYTES: int = 1024 * 1024
# Network read timeout in seconds. Hung hosts must not hang the CLI.
_TIMEOUT: float = 10.0
# Chunk size used to stream the response body.
_CHUNK: int = 8192
# The allowlist. Compared case-insensitively on the media type only;
# any ``;charset=...`` is stripped before comparison.
_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({"text/plain", "text/csv", "application/json"})


def _is_local_file(source: str) -> bool:
    """Return True if ``source`` names an existing local file.

    ``Path.is_file`` does not touch the network, which keeps the
    local-path branch off any I/O gate. A path that does not exist
    falls through to URL parsing, so a typo never silently becomes
    a network call.
    """
    try:
        return Path(source).is_file()
    except OSError:
        return False


def _read_local(source: str) -> bytes:
    """Read a local file. The brief requires no network call here."""
    return Path(source).read_bytes()


def _normalise_content_type(raw: str | None) -> str | None:
    """Return the media-type half of a Content-Type header, lowercased.

    Anything after the first ``;`` is dropped. ``None`` in means
    ``None`` out: the caller treats a missing header as not on the
    allowlist.
    """
    if raw is None:
        return None
    head = raw.split(";", 1)[0].strip().lower()
    return head or None


def _check_content_type(raw: str | None) -> str:
    """Return the normalised media type or raise ``FetchError``."""
    normalised = _normalise_content_type(raw)
    if normalised is None or normalised not in _ALLOWED_CONTENT_TYPES:
        raise FetchError(
            f"Refused content type {raw!r}; expected text/plain, text/csv or application/json"
        )
    return normalised


def _stream_to_dest(response: requests.Response, dest: Path) -> None:
    """Stream ``response`` into ``dest`` under the size cap.

    Writes to a ``.part`` sibling and renames atomically on success,
    so a cap abort leaves no half-written ``dest`` on disk. The
    running total is checked per chunk so an oversized body is
    rejected without ever buffering it all.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        running = 0
        with open(part, "wb") as out:
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if not chunk:
                    continue
                running += len(chunk)
                if running > _MAX_BYTES:
                    raise FetchError("Refused fetch: body exceeds 1 MiB cap")
                out.write(chunk)
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def fetch_source(source: str, fmt: str, dest: Path) -> int:
    """Fetch ``source`` under the four caps and write it to ``dest``.

    ``source`` is either an HTTPS URL or a path to a local file. The
    function streams the response, enforces the size and
    content-type caps, writes the body to ``dest``, parses it through
    ``parse_filter_list`` and returns the id count.

    Network traffic here uses plain ``requests``. It deliberately does
    NOT go through the Tidal network gate (see module docstring).
    """
    # Local file wins over URL parsing. Windows paths like
    # ``C:\\Users\\foo\\list.txt`` parse to ``scheme='c'`` under
    # ``urlparse``, so we cannot rely on that to distinguish them;
    # the filesystem is the source of truth for "is this a path?".
    if _is_local_file(source):
        data = _read_local(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(parse_filter_list(data, fmt))

    parsed = urlparse(source)
    if parsed.scheme != "https":
        # Do not upgrade the scheme. http:// is refused outright.
        raise FetchError(f"Refused non-HTTPS source: {parsed.scheme}://")

    try:
        with requests.get(source, stream=True, timeout=_TIMEOUT) as response:
            if response.status_code != 200:
                raise FetchError(f"Fetch failed: HTTP {response.status_code}")

            _check_content_type(response.headers.get("Content-Type"))

            _stream_to_dest(response, dest)
    except requests.exceptions.Timeout as exc:
        raise FetchError(f"Fetch timed out after {_TIMEOUT}s") from exc
    except requests.exceptions.ConnectionError as exc:
        raise FetchError(f"Fetch connection error: {exc}") from exc

    data = dest.read_bytes()
    return len(parse_filter_list(data, fmt))
