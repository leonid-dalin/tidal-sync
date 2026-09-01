# Telemetry and Audit Logging

`tidal-sync` keeps a local audit trail of the operations you run against your Tidal
library. This page explains what is logged, where the files land, how sensitive
values are scrubbed, and how the logging lifecycle is wired into the CLI.

## Purpose and scope

tidal-sync emits **no remote telemetry**. Nothing leaves your machine. The only
network traffic the tool makes is to the Tidal API itself, which is the service
you are backing up or modifying. There is no phone-home, no analytics endpoint,
no usage beacon.

The log output exists for you, the operator. It gives you a durable,
machine-readable record of what each import, export, and clear run actually did,
so you can answer questions like "which tracks were skipped as duplicates" or
"what did the dry-run say it would delete" after the fact.

Two kinds of log are produced:

- **Console log** (rich, human readable) on stderr, driven by
  `setup_global_logging()`. It shows warnings and above, and deliberately filters
  out the audit stream so the terminal is not flooded with JSON.
- **Audit log** (machine readable, JSONL) written to disk by
  `setup_audit_logging()`. This is the durable record described on this page.

## What is logged

When an audit session is active, records are written only when a log call is
bound with `audit=True` (via `logger.bind(audit=True)`). The audit filter
(`audit_filter`) drops anything that is not part of the audit stream, so
ordinary console warnings never reach the JSONL file.

### Audit file location

The audit sink writes to a directory that the **caller** chooses. There is no
fixed global path. The CLI passes the following locations (all relative to the
current working directory):

| Command   | Directory passed to `setup_audit_logging` | Source |
|-----------|-------------------------------------------|--------|
| `import`  | `./import_reports`                        | `cli.py` |
| `export`  | `<output_dir>/reports` (default `./exports/reports`) | `cli.py` |
| `clear`   | `./import_reports`                        | `cli.py` |

The target directory is created if it does not already exist
(`report_dir.mkdir(parents=True, exist_ok=True)`).

### Filename format

Each audit session writes a single file named:

```
audit_{YYYYMMDD_HHMMSS}_{random}.jsonl
```

For example: `audit_20260510_143201_a1b2c3d4.jsonl`.

The `secrets.token_hex(4)` suffix (8 hex characters) makes the name
collision-proof. On Windows the timestamp alone is not unique within a single
clock tick because the system clock lacks microsecond precision, so two sessions
started in the same tick would otherwise overwrite each other. The random suffix
removes that risk while keeping the timestamp for human readability.

### Sink behaviour

The audit handler is configured with:

- **Level:** `DEBUG` (everything bound to the audit stream is captured).
- **Rotation:** rolls to a new file at 10 MB.
- **Retention:** keeps rotated files for 7 days, then prunes them.
- **Compression:** rotated files are gzipped.
- **Enqueue:** writes are backgrounded (`enqueue=True`) so they never block the
  sync engine.

## Redaction

Every audit record is passed through `redact()` before it is serialised. The
goal is to keep token material and session identifiers out of the file system.

`redact()` runs recursively over strings, mappings (dicts), and sequences
(lists, tuples). It applies these regex substitutions to any string value:

- `sessionId=<value>` becomes `sessionId=[REDACTED]` (case insensitive).
- `Bearer <token>` becomes `Bearer [REDACTED]` (case insensitive).
- `access_token=<value>` and `refresh_token=<value>` become
  `access_token=[REDACTED]` / `refresh_token=[REDACTED]`.
- `"access_token": "<value>"` and `"refresh_token": "<value>"` (JSON form)
  become `"access_token": "[REDACTED]"` / `"refresh_token": "[REDACTED]"`.

Additionally, when `redact()` walks a mapping, any key whose name matches
`token|secret|password|authorization|bearer` (case insensitive) has its value
replaced wholesale with `[REDACTED]`, regardless of the value's shape.

### Why template safety matters

The audit formatter (`json_formatter`) does not use loguru's normal message
templating. Music metadata is untrusted input: track titles routinely contain
braces and brackets such as `Song Name {Remix} [feat. Artist]`. If such a string
were passed through loguru's `{}` template parser it would trigger a
`KeyError` (template injection) and crash the sink.

Instead, `json_formatter` pre-serialises the whole record with `orjson` and
stashes the resulting JSON string in the record's `extra` dictionary under the
key `serialized`. The sink template is then the static literal
`"{extra[serialized]}\n"`, which contains no user data and therefore cannot be
injected. Log calls should pass structured data through `extra={...}`, never
embed it in f-strings inside the message.

One further guard: `orjson.dumps(..., default=str)` is used so that values which
are not natively JSON-serialisable (a `Path`, a `set`, a tidalapi object) are
coerced to their string form rather than raising and causing loguru to silently
drop the record.

## The JSONL record shape

Each line of the audit file is one JSON object with this shape:

```json
{
  "timestamp": "<YYYY-MM-DDTHH:MM:SS.mmmZ>",
  "level": "<debug|info|warning|error|...>",
  "message": "<the log message, already redacted>",
  "extra": {
    "<any structured fields passed via extra, redacted, minus audit/serialized>"
  }
}
```

- `timestamp`: local time formatted as `YYYY-MM-DDTHH:MM:SS.mmm` with a trailing
  `Z`, produced from the log record's time field.
- `level`: the lower-cased log level name.
- `message`: the redacted message text.
- `extra`: the structured fields you passed through `extra={...}`, with
  `audit` and `serialized` stripped out and all values run through `redact()`.

### Example record

The following is a fabricated example to show the shape. It is not taken from a
real run.

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

If a redacted value had appeared, it would look like this (also fabricated):

```json
{
  "timestamp": "2026-05-10T14:33:10.456Z",
  "level": "warning",
  "message": "Session reused sessionId=[REDACTED]",
  "extra": {
    "user_id": "unknown"
  }
}
```

You can inspect the files with any JSONL tooling, for example `jq`:

```
jq . ./import_reports/audit_20260510_143201_a1b2c3d4.jsonl
```

## Lifecycle

The audit logging is tied to a command run through a small, explicit lifecycle:

1. **Start.** A CLI command calls `setup_audit_logging(report_dir)` and receives
   an integer `handler_id`. This creates the directory if needed, generates the
   collision-proof filename, and registers the JSONL sink.
2. **Resolve path.** `audit_log_path(handler_id)` returns the `Path` of the
   file that handler is writing to, or `None` if the id is not known to this
   module. This is how a command can report exactly where its audit trail went.
3. **Stop.** `stop_audit_logging()` removes every audit sink this module added,
   flushing the background queue. It pops each handler id off an internal list
   and calls `logger.remove(handler_id)`, suppressing the `ValueError` that would
   arise if a handler were already gone.

Each emitting command wraps its work in `try`/`finally` so `stop_audit_logging()`
runs even when the operation raises:

- `import` calls `setup_audit_logging(Path("./import_reports"))`, runs the import
  inside a nested `try`, and calls `stop_audit_logging()` in `finally`.
- `export` calls `setup_audit_logging(output_dir / "reports")`, runs the exports,
  and calls `stop_audit_logging()` in a top-level `finally`.
- `clear` calls `setup_audit_logging(Path("./import_reports"))`, performs the
  purge, and calls `stop_audit_logging()` in `finally`. Note that `clear`
  authenticates (and may exit on `TidalAuthenticationError`) *before* starting
  the audit session, so no audit file is created when authentication fails.

Because `stop_audit_logging()` only removes handlers this module registered, it
is safe to call even if no audit session was started: the internal handler list
is empty, the loop does nothing, and nothing is removed.

## Privacy note

All telemetry in `tidal-sync` is local-only. Audit logs are written to directories
on your own machine and are never transmitted anywhere. The tool makes no network
calls except to the Tidal API that you are explicitly backing up or modifying.

`tidal-sync` is released under the GNU Affero General Public License v3 or later
(AGPL-3.0-or-later). The AGPL's network clause exists precisely because remote
telemetry and SaaS-style hosting carry these type of obligations; `tidal-sync` ships 
none of that. If you self-host or modify the tool, the same local-only behaviour 
holds unless you add egress yourself.

## Log flow

```
+--------------------------- PLACEHOLDER ----------------------------+
|                                                                     |
|   CLI command (import / export / clear)                             |
|        |                                                            |
|        v                                                            |
|   setup_audit_logging(report_dir)  -->  handler_id                 |
|        |                                                            |
|        v                                                            |
|   logger.bind(audit=True).<level>(msg, extra={...})                |
|        |                                                            |
|        v                                                            |
|   audit_filter  -->  json_formatter                                 |
|        |                  |                                         |
|        |                  v                                         |
|        |            redact(message, extra)                          |
|        |                  |                                         |
|        v                  v                                         |
|   JSONL sink  -->  audit_{ts}_{rand}.jsonl  (rotated/compressed)    |
|        |                                                            |
|        v                                                            |
|   stop_audit_logging()  (in finally)  -->  flush + remove handler  |
|                                                                     |
+---------------------------------------------------------------------+
```

## Related docs

- [Architecture](architecture.md): the infrastructure layer (`tidal_sync/infrastructure/logger.py`)
  owns logging and audit configuration.
- [CLI reference](cli-reference.md): which commands (`import`, `export`, `clear`)
  start and stop an audit session, and what arguments they accept.
- [Getting started](getting-started.md): how to authenticate a profile before any
  command can run.
- [Data flow](data-flow.md): how import, export, and clear move data through the
  engine and what each stage records.
