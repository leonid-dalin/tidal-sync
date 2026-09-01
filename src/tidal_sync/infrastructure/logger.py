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
"""
Centralised telemetry and logging configuration.

This module manages application-wide logging using loguru. It separates standard
console warnings from machine-readable JSONL audit logs. It also scrubs sensitive
session tokens before they reach the file system.

Features:
    - High-speed JSONL serialization using orjson
    - Regex-based redaction for Tidal session IDs
    - Thread-safe background processing via loguru enqueuing
    - Automatic log rotation (10 MB limits) and 7-day retention

Example:
    Initialise the base console logger, start an audit session, and write a
    secure log line. The exact audit file path comes back from
    audit_log_path() and is needed to stop the sink later::

        from pathlib import Path
        from tidal_sync.infrastructure.logger import (
            setup_audit_logging,
            setup_global_logging,
            logger,
        )

        setup_global_logging()
        handler_id = setup_audit_logging(Path("./exports/reports"))
        logger.bind(audit=True).info("Processing playlist", id=123)

Note:
    The `json_formatter` bypasses loguru template injection bugs by
    pre-serializing the JSON and stashing it inside the record's `extra`
    dictionary before yielding it to the sink.
"""

import contextlib
import re
import secrets
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson
from loguru import logger

REDACT_PATTERNS = [
    (re.compile(r"sessionId=[a-zA-Z0-9-]+", re.IGNORECASE), "sessionId=[REDACTED]"),
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"(access_token|refresh_token)=[^\s&\"']+", re.IGNORECASE), r"\1=[REDACTED]"),
    (
        re.compile(r'"(access_token|refresh_token)"\s*:\s*"[^"]*"', re.IGNORECASE),
        r'"\1": "[REDACTED]"',
    ),
]

_SENSITIVE_KEY_HINT = re.compile(r"token|secret|password|authorization|bearer", re.IGNORECASE)


def redact(value: Any) -> Any:
    """Strips credentials out of strings, mappings, and sequences, recursively."""
    if isinstance(value, str):
        for pattern, replacement in REDACT_PATTERNS:
            value = pattern.sub(replacement, value)
        return value

    if isinstance(value, Mapping):
        return {
            k: ("[REDACTED]" if _SENSITIVE_KEY_HINT.search(str(k)) else redact(v))
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)

    return value


def audit_filter(record: Any) -> bool:
    return record["extra"].get("audit", False)


def json_formatter(record: Any) -> str:
    """Serialises one audit record as JSONL.

    `default=str` is load-bearing: without it, an unserialisable value in
    `extra` makes orjson raise inside the sink thread and loguru discards the
    record, which is exactly the record needed to explain a dropped track.
    """
    message = redact(record["message"])

    clean_extra = {
        k: redact(v) for k, v in record["extra"].items() if k not in ("audit", "serialized")
    }

    record["extra"]["serialized"] = orjson.dumps(
        {
            "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record["level"].name.lower(),
            "message": message,
            "extra": clean_extra,
        },
        default=str,
    ).decode()

    return "{extra[serialized]}\n"


def setup_global_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="WARNING",
        filter=lambda record: not record["extra"].get("audit", False),
    )


def setup_audit_logging(report_dir: Path) -> int:
    """Starts the JSONL audit sink. Returns the handler id for later removal."""
    report_dir.mkdir(parents=True, exist_ok=True)

    # The clock alone is not unique on Windows, where ticks lack microsecond
    # precision and two calls inside one tick collide. A random suffix makes
    # the path collision-proof while keeping the timestamp for readability.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = report_dir / f"audit_{timestamp}_{secrets.token_hex(4)}.jsonl"

    handler_id = logger.add(
        log_file,
        format=json_formatter,
        level="DEBUG",
        filter=audit_filter,
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        enqueue=True,
    )

    _AUDIT_HANDLERS.append((handler_id, log_file))
    return handler_id


_AUDIT_HANDLERS: list[tuple[int, Path]] = []


def audit_log_path(handler_id: int) -> Path | None:
    """Resolves the file path for an audit handler started by this module."""
    for known_id, path in _AUDIT_HANDLERS:
        if known_id == handler_id:
            return path
    return None


def stop_audit_logging() -> None:
    """Removes every audit sink this module added, flushing the queue."""
    while _AUDIT_HANDLERS:
        handler_id, _ = _AUDIT_HANDLERS.pop()
        with contextlib.suppress(ValueError):
            logger.remove(handler_id)
