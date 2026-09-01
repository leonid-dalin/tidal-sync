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
    Initialise the base console logger:

    >>> from logger import setup_global_logging
    >>> setup_global_logging()

    Start an audit session and write a secure log:

    >>> from pathlib import Path
    >>> from logger import setup_audit_logging, logger
    >>> log_file = setup_audit_logging(Path("./exports/reports"))
    >>> logger.bind(audit=True).info("Processing playlist", extra={"id": 123})

Note:
    The `json_formatter` bypasses loguru template injection bugs by
    pre-serializing the JSON and stashing it inside the record's `extra`
    dictionary before yielding it to the sink.
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson
from loguru import logger

REDACT_PATTERNS = [
    (re.compile(r"sessionId=[a-zA-Z0-9-]+"), "sessionId=[REDACTED]"),
]


def audit_filter(record: Any) -> bool:
    return record["extra"].get("audit", False)


def json_formatter(record: Any) -> str:
    message = record["message"]
    error_val = record["extra"].get("error")

    for pattern, replacement in REDACT_PATTERNS:
        message = pattern.sub(replacement, message)
        if isinstance(error_val, str):
            error_val = pattern.sub(replacement, error_val)

    clean_extra = {
        k: v for k, v in record["extra"].items() if k not in ("audit", "serialized", "error")
    }
    if error_val is not None:
        clean_extra["error"] = error_val

    record["extra"]["serialized"] = orjson.dumps(
        {
            "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record["level"].name.lower(),
            "message": message,
            "extra": clean_extra,
        }
    ).decode()

    return "{extra[serialized]}\n"


def setup_global_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr, level="WARNING", filter=lambda record: not record["extra"].get("audit", False)
    )


def setup_audit_logging(report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = report_dir / f"audit_{timestamp}.jsonl"

    logger.add(
        log_file,
        format=json_formatter,
        level="DEBUG",
        filter=audit_filter,
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        enqueue=True,
    )
    return log_file
