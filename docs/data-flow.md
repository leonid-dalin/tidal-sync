# Data Flow

This document traces the lifecycle of a track import operation in `tidal-sync`, from reading a raw CSV file to generating the final JSONL audit log. 

It explains how the tool moves data through the thread pool, interacts with the external `tidalapi` library, and recovers from batch upload failures.

## 1. Ingestion and validation (`parse_csv`)

The process begins when the CLI routes a file to the `_import_tracks` function. The tool opens the file using `utf-8-sig` encoding to safely strip Byte Order Marks (BOM) commonly injected by Windows or Excel exports.

The `csv.DictReader` parses the rows, passing them into the Pydantic `TrackRow` model. This model standardises the data:
* It maps legacy column headers (like "Artist Name(s)") to strict internal properties.
* It drops malformed rows entirely, logging a validation error.
* It computes a `search_query` property (stripping out secondary featured artists) to use as a fallback if strict database IDs fail.

## 2. Concurrent matching engine

The tool does not upload tracks blindly. It must translate the local CSV data into valid Tidal database IDs.

The tool fetches the target playlist (or the user's "Liked Songs") via `tidalapi` and builds a local `set` of `existing_track_ids`. It then spins up an `asyncio.TaskGroup`. 

For each track, an asynchronous task runs `_match_single_track`, attempting to find a match in this specific order:
1. **Direct ID:** If the CSV contains a `tidal_id`, it uses it immediately.
2. **ISRC Match:** If an International Standard Recording Code is present, it queries the Tidal API via `session.search(f"isrc:{track.isrc}")`. This ensures 1-to-1 high-fidelity matching regardless of region or naming variations.
3. **Text Fallback:** It falls back to querying the API using the computed `search_query`.

If the matched ID is already in the `existing_track_ids` set, the worker drops the track to prevent duplication. If it is a new match, the worker uses an `asyncio.Lock()` to safely append the ID to a shared `track_ids_to_add` list and increments the session's `added` counter. The lock strictly isolates local state changes, allowing actual network calls to run concurrently outside the lock.

## 3. Chunked uploads

Once all tracks are matched, the tool moves to the upload phase. The `tidalapi` limits bulk additions, and pushing too many IDs at once triggers an HTTP 413 (Payload Too Large) error.

The tool slices the `track_ids_to_add` list into arrays of 50 (`CHUNK_SIZE`). It passes these chunks to either `user.favorites.add_track(batch)` or `playlist.add(batch)`. The `tidalapi` formats these lists into a comma-separated string and executes a single POST request per chunk.

## 4. Recursive bisection (Fault recovery)

Tidal occasionally region-locks specific tracks. If a single track within a 50-item chunk is geographically locked, Tidal rejects the entire POST request with an `HTTPError` or `ObjectNotFound` exception. 

Instead of failing the whole batch, the `_bisect_upload` function intercepts the exception and applies a recursive bisection algorithm:
1. It splits the rejected 50-item chunk into two 25-item chunks.
2. It attempts to upload both halves. 
3. The half containing the locked track fails again, triggering another split, while the clean half uploads successfully.
4. This recursion continues until the chunk size is exactly 1. 
5. The tool identifies the single locked "poison" track, drops it from the import, and logs it as a failure.

## 5. Telemetry and audit logging

Throughout this process, the `logger.py` module tracks every state change (matched, skipped, added, failed). 

Because the tool handles untrusted string data (like user-generated track names), it bypasses standard logging templates. It serialises the data directly into a JSON object using `orjson` and stashes it inside the log record's `extra` dictionary. 

A background thread dequeues these records, passes them through a regex filter to scrub any leaked Tidal OAuth session IDs, and writes them to a local `.jsonl` audit file.