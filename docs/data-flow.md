# Data Flow

This document traces the lifecycle of a track import operation in `tidal-sync`, from reading a raw CSV file to generating the final JSONL audit log. 

It explains how the tool moves data through the thread pool, interacts with the external `tidalapi` library, and recovers from batch upload failures.

## 1. Ingestion and validation (`parse_csv`)

he process begins when the CLI routes a file to the `import_tracks_category_async` function within `engine/importer.py`. The tool opens the file using `utf-8-sig` encoding to safely strip Byte Order Marks (BOM) commonly injected by Windows or Excel exports.

The `csv.DictReader` parses the rows, passing them into the Pydantic `TrackRow` model. This model standardises the data:
* It maps legacy column headers (like "Artist Name(s)") to strict internal properties.
* It drops malformed rows entirely, logging a validation error.
* It computes a `search_query` property (stripping out secondary featured artists) to use as a fallback if strict database IDs fail.

## 2. Concurrent matching engine

The tool does not upload tracks blindly. It must translate the local CSV data into valid Tidal database IDs.

The tool fetches the target playlist (or the user's "Liked Songs") via `tidalapi` and builds a local `set` of `existing_track_ids`. It then spins up an `asyncio.TaskGroup`. 

For each track, an asynchronous task runs `_resolve_track_metadata_to_id`, attempting to find a match in this specific order:
1. **Direct ID:** If the CSV contains a `tidal_id`, it uses it immediately.
2. **ISRC Match:** If an International Standard Recording Code is present, it queries the Tidal API via `session.search(f"isrc:{track.isrc}")`. This ensures 1-to-1 high-fidelity matching regardless of region or naming variations.
3. **Text Fallback:** It falls back to querying the API using the computed `search_query`.

If the matched ID is already in the `existing_track_ids` set, the worker drops the track to prevent duplication. If it is a new match, the worker uses an `asyncio.Lock()` to safely append the ID to a shared `track_ids_to_add` list and increments the session's `added` counter. The lock strictly isolates local state changes, allowing actual network calls to run concurrently outside the lock.

## 3. Chunked uploads

Once all tracks are matched, the tool moves to the upload phase. The `tidalapi` limits bulk additions, and pushing too many IDs at once triggers an HTTP 413 (Payload Too Large) error.

The tool slices the `track_ids_to_add` list into arrays of 50 (`CHUNK_SIZE`). It passes these chunks to either `user.favorites.add_track(batch)` or `playlist.add(batch)`. The `tidalapi` formats these lists into a comma-separated string and executes a single POST request per chunk.

## 4. Upload recovery (Fault recovery)

Tidal rejects an entire chunk if one track in it is region-locked or removed, and it also answers 200 while silently skipping tracks it will not accept. `upload_batch_with_recovery` (in `engine/upload_recovery.py`) handles both cases:

1. It uploads the chunk and reads the `UploadOutcome`. Tracks Tidal rejected server-side are dropped directly, because Tidal has already named them.
2. If the whole chunk is refused with an exception, it classifies the error by status: a 412 (version collision) is retried once, retryable server errors (500/502/503/504) are retried, and auth or rate-limit errors propagate.
3. Only a true poison status (403/404) or `ObjectNotFound` triggers a per-item scan: each track is retried alone and dropped if it still fails, so the rest of the batch uploads.
4. The isolated track is logged as "Dropped Track (Region Locked)" and the rest of the batch completes.

## 5. Telemetry and audit logging

Throughout this process, the `logger.py` module tracks every state change (matched, skipped, added, failed). 

Because the tool handles untrusted string data (like user-generated track names), it bypasses standard logging templates. It serialises the data directly into a JSON object using `orjson` and stashes it inside the log record's `extra` dictionary. 

A background thread dequeues these records, passes them through a regex filter to scrub any leaked Tidal OAuth session IDs, and writes them to a local `.jsonl` audit file.

## 6. Folder identity (export to import round trip)

Folder names are sanitised before they become on-disk directory names. `build_playlist_folder_map` runs each raw Tidal folder name through `sanitize_filename`, so `'AC/DC Mixes'` becomes the directory `'AC_DC Mixes'`. On import the sanitised directory name is passed to `ensure_v2_folder_exists`, which matches it against the *sanitised* form of each raw Tidal name rather than the raw name, so an existing folder is reused instead of duplicated.

Folder identity survives the round trip only up to sanitisation. Two distinct Tidal folders whose raw names collapse to the same sanitised string (for example `'A/B'` and `'A_B'`) map to the same directory and will be merged on import. This is an accepted limitation of using the file system as the folder key.