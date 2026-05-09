# Architectural Overview

## Overview

Tidal Sync uses a layered architecture to separate the terminal interface from the core synchronisation logic and the underlying data structures. It relies heavily on concurrent I/O to bypass Tidal's slow sequential API responses, cutting bulk import times from minutes to seconds.

## Core Application Layer

These top-level modules manage the execution flow, state, and network operations.

### `cli.py` (Terminal Interface)
Acts as the entry point. It uses Typer to route terminal commands, validate arguments, and handle dual-account profile routing. It also acts as the final error handler, catching domain exceptions to ensure the application exits cleanly instead of printing raw stack traces to the user.

### `auth.py` (State & Security)
Manages the OAuth lifecycle and local storage.
* **Collision Detection:** Checks if a user is trying to log into the same Tidal account under two different aliases, preventing redundant API calls.
* **Strict Storage:** Writes session tokens to `~/.tidal_sync` and immediately applies strict POSIX permissions (`chmod 600`).
* **Secure Wiping:** When a user logs out, the system performs a logical zero-fill overwrite on the token file before deleting it, mitigating disk data recovery risks.

### `sync.py` (Synchronisation Engine)
Handles the heavy network operations and fault tolerance.
* **Concurrency:** Uses `concurrent.futures.ThreadPoolExecutor` to match and upload tracks in parallel.
* **Chunking:** Groups matched tracks into batches of 50 to avoid HTTP 413 (Payload Too Large) errors during batch uploads.
* **Rate Limit Backoff:** Wraps network-bound functions in a `@retry_on_429` decorator. If Tidal flags the tool with an HTTP 429 error, the thread sleeps for the exact duration requested by the API's `retry_after` header, falling back to exponential backoff if no header exists.
* **Recursive Bisection:** Tidal fails entire batch uploads if a single track is geographically locked. The `_bisect_upload` function catches this, splits the array in half, and retries both halves recursively to isolate and drop the "poison" track.

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