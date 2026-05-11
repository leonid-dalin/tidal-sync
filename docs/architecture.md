# Architectural Overview

## Overview

Tidal Sync uses a layered architecture to separate the terminal interface from the synchronisation logic and the underlying data structures. It relies on concurrent I/O to bypass slow sequential API responses, reducing bulk import times.

## Core Application Layer

These top-level modules manage the execution flow and state.

### `cli.py` (Terminal Interface)
Acts as the entry point. It uses Typer to route terminal commands, validate arguments, and handle dual-account profile routing. It also acts as the final error handler, catching domain exceptions to ensure the application exits cleanly instead of printing raw stack traces to the user.

### `auth.py` (State & Security)
Manages the OAuth lifecycle and local storage.
* **Collision Detection:** Checks if a user is trying to log into the same Tidal account under two different aliases, preventing redundant API calls.
* **Strict Storage:** Writes session tokens to `~/.tidal_sync` and immediately applies strict POSIX permissions (`chmod 600`).
* **Secure Wiping:** When a user logs out, the system performs a logical zero-fill overwrite on the token file before deleting it, mitigating disk data recovery risks.

### `sync.py` (Synchronisation Engine)
Orchestrates the library transfers. It uses `asyncio.TaskGroup` to match and upload tracks. It groups matched tracks into batches of 50 to avoid HTTP 413 (Payload Too Large) errors. It automatically refreshes the playlist ETag before each chunk to prevent server-side version collisions (HTTP 412). Tidal fails entire batch uploads if a single track is geographically locked; the `_bisect_upload` function catches this, splits the array in half, and retries both halves recursively to isolate and drop the poison track.

## Engine Layer (`/engine/`)

The engine handles concurrent operations, rate limiting, and data ingestion.

### `network.py`
Centralises all Tidal API calls. It uses a `GlobalTidalGate` to monitor for rate limits (429) or abuse flags (403). If Tidal throttles the tool, the gate instantly pauses all sibling workers, preventing account bans.

### `workers.py`
Manages the asynchronous matching tasks. It bounds network throughput using an `asyncio.Semaphore`. It provides an `ImportStats` dataclass with async-safe locks to track the number of skipped, failed, and added items without race conditions.

### `parser.py`
Reads and validates CSV files into Pydantic models. It tests multiple encodings (UTF-8-SIG, CP1252, Latin-1) to strip Byte Order Marks and scrubs null-byte corruption common in legacy music exports.

## Domain Layer (`/domain/`)

The domain layer enforces type safety, validates external data, and manages telemetry before execution begins.

### `models.py` (Data Validation)
Uses Pydantic to sanitise legacy Exportify or TuneMyMusic CSV formats into standardised models (`TrackRow`, `AlbumRow`). It drops malformed rows and computes fallback text queries when direct database IDs are missing from the source files.

### `protocols.py` (Structural Typing)
Defines structural `Protocol` classes for external `tidalapi` objects (like `TidalTrack` and `TidalPlaylist`). This maintains strict static analysis boundaries and eliminates wildcard `Any` casts without relying on unmaintained third-party type stubs.

### `enums.py` (Strict Constraints)
Stores `StrEnum` classes, such as `ClearTarget`. By tying Typer arguments directly to these enums, the CLI gets native validation and terminal autocomplete, preventing users from passing unsupported category strings.

### `exceptions.py` (Error Hierarchy)
Defines the custom domain exceptions (`TidalSyncError`, `TidalAuthenticationError`). This allows the core logic to raise specific, expected errors that the CLI layer can catch and format neatly, rather than relying on abrupt `SystemExit` calls.

### `logger.py` (Telemetry)
Manages high-speed, thread-safe JSONL telemetry using loguru and `orjson`. 
* **Template Safety:** It pre-serialises JSON objects and stashes them inside the log's `extra` dictionary, bypassing loguru template injection vulnerabilities when handling untrusted track names. 
* **Redaction:** A regex filter actively scrubs Tidal session IDs from all outputs before they reach the disk.