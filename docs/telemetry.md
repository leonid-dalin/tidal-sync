# Telemetry and Audit Logging

Tidal Sync generates machine-readable JSONL audit logs during import operations. These logs track exactly which files were processed, which tracks were skipped as duplicates, and which items failed to match. 

The system relies on loguru and `orjson` to handle thousands of concurrent log events without blocking the main synchronisation engine.

## File Lifecycle

When you run an import command, the tool automatically creates an `import_reports/` directory relative to your current terminal path. 

The file sink is configured for long-term safety:
* **Rotation:** Logs rotate automatically when they reach 10 MB.
* **Retention:** The system keeps logs for 7 days before pruning them.
* **Compression:** Rotated logs are automatically compressed into `.gz` archives to save disk space.

## Security Redaction

The tool interacts heavily with the Tidal API, meaning raw requests often contain your active OAuth `sessionId`. 

To prevent credentials from leaking into your local log files, the `logger.py` module passes every log record through a regex gatekeeper (`redact_filter`). This filter scans both the log message and the associated error trace, replacing any active session tokens with `[REDACTED]` before the record reaches the disk.

## Template Injection Safety

Music metadata is untrusted data. Track names frequently contain brackets, braces, and strange characters (e.g., `Song Name {Remix} [feat. Artist]`).

If passed directly into standard logging templates, these characters cause template injection crashes (`KeyError`). To prevent this, the telemetry engine uses an inline stash pattern:
1. It pre-serialises the entire log record into a raw JSON string using `orjson`.
2. It stashes that raw string inside the log record's `extra` dictionary.
3. It passes a safe, static template (`"{extra[serialized]}\n"`) to the loguru sink, bypassing the injection vulnerability entirely.

## Logged Events

The audit logger is bound specifically to the import engine using `logger.bind(audit=True)`. It records the following primary events:

* **Item Staged:** A track was successfully matched to a Tidal ID and queued for upload.
* **Skipped (Duplicate):** A track was matched, but the tool detected it already exists in the target playlist or your liked songs.
* **Failed (Not Found):** The tool could not find a match using the Tidal ID, the ISRC code, or the fallback text search.
* **Item Added:** The track successfully uploaded to the Tidal server.
* **Chunk rejected:** A batch upload failed (usually due to a region-locked track), triggering the bisection algorithm.
* **Dropped Track:** The specific region-locked track isolated by the bisection algorithm that was dropped so the rest of the batch could succeed.

## Example Output

You can parse the `.jsonl` files using standard command-line tools like `jq`. A typical log entry looks like this:

```json
{
  "timestamp": "2026-05-10T14:32:01.123Z",
  "level": "debug",
  "message": "Item Added",
  "extra": {
    "type": "Track",
    "id": "2124179",
    "dest": "Liked Songs"
  }
}
```

```json
{
  "timestamp": "2026-05-10T14:32:05.456Z",
  "level": "error",
  "message": "Dropped Track (Region Locked)",
  "extra": {
    "track_id": "98765432"
  }
}
```