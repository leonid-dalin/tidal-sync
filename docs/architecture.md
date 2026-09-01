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

## Engine Layer (`/engine/`)

The engine handles concurrent operations, rate limiting, data ingestion, and synchronisation logic.

### `importer.py`
Orchestrates data ingestion, translates local CSV metadata into Tidal database IDs, and stages chunked uploads. It automatically refreshes playlist ETags before each chunk to prevent server-side version collisions (HTTP 412).

### `exporter.py`
Handles network fetching, captures point-in-time snapshots of dynamic algorithmic mixes, and serialises Tidal structures (including V2 folders) back to local CSV files.

### `wiping.py`
Manages destructive account clears via headless worker groups. It safely absorbs isolated 404/500 errors and uses raw HTTP bypasses to eradicate undocumented V2 ghost folders left behind by standard playlist deletion.

### `upload_recovery.py`
Isolates tracks Tidal refuses to accept during a batch upload. The module uploads a chunk, reads the `UploadOutcome` to drop tracks Tidal rejected server-side, and falls back to a per-item scan only when the whole chunk is refused. Auth, rate-limit, and server errors propagate rather than being blamed on a track.

### `folders.py`
Provides raw HTTP interventions (mimicking browser/web-player headers) to manage V2 API folder creation, assignment, and fetching. `Requests.request()` returns the raw `Response` and never parses JSON, so the module calls the V2 endpoints directly rather than routing them through `tidalapi`. The empty body these calls send is the `Content-Length: 0` that Tidal's firewall requires, not a crash guard against parser failures.

### `network.py`
Centralises all Tidal API calls. It uses a `GlobalTidalGate` to monitor for rate limits (429) or abuse flags (403). If Tidal throttles the tool, the gate instantly pauses all sibling workers, preventing account bans.

### `workers.py`
Manages the asynchronous matching tasks. It bounds network throughput using an `asyncio.Semaphore`. It provides an `ImportStats` dataclass with async-safe locks to track the number of skipped, failed, and added items without race conditions. Note that `cli.export_all` and `export_user_favourites_to_disk` still create bare `TaskGroup` fan-outs outside the semaphore (three to four tasks each), so the semaphore bounds the importer, not every export path.

### `parser.py`
Reads and validates CSV files into Pydantic models. It tests multiple encodings (UTF-8-SIG, CP1252, Latin-1) to strip Byte Order Marks and scrubs null-byte corruption common in legacy music exports.

## Domain Layer (`/domain/`)

The domain layer enforces type safety, validates external data, and manages telemetry before execution begins.

### `models.py` (Data Validation)
Uses Pydantic to sanitise legacy Exportify or TuneMyMusic CSV formats into standardised models (`TrackRow`, `AlbumRow`). It drops malformed rows and computes fallback text queries when direct database IDs are missing from the source files.

### `protocols.py` (Structural Typing)
Defines structural `Protocol` classes for external `tidalapi` objects (currently `TidalUser`). This maintains strict static analysis boundaries and eliminates wildcard `Any` casts without relying on unmaintained third-party type stubs.

### `enums.py` (Strict Constraints)
Stores `StrEnum` classes, such as `ClearTarget`. By tying Typer arguments directly to these enums, the CLI gets native validation and terminal autocomplete, preventing users from passing unsupported category strings.

### `exceptions.py` (Error Hierarchy)
Defines the custom domain exceptions (`TidalSyncError`, `TidalAuthenticationError`). This allows the core logic to raise specific, expected errors that the CLI layer can catch and format neatly, rather than relying on abrupt `SystemExit` calls.

## Infrastructure Layer (`/infrastructure/`)

Cross-cutting concerns that are not domain logic and not engine flow.

### `logger.py` (Telemetry)
Manages high-speed, thread-safe JSONL telemetry using loguru and `orjson`. 
* **Template Safety:** It pre-serialises JSON objects and stashes them inside the log's `extra` dictionary, bypassing loguru template injection vulnerabilities when handling untrusted track names. 
* **Redaction:** A regex filter actively scrubs Tidal session IDs from all outputs before they reach the disk.