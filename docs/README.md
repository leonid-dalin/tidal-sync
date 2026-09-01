# tidal-sync documentation index

## Purpose

`tidal-sync` is a Python command-line tool that backs up, restores, and clones a Tidal music library. It reads a Tidal account's playlists, liked songs, saved albums, and followed artists into local CSV files, and writes them back into the same or a different account using exact Tidal IDs and ISRC codes for 1-to-1 matching. It is aimed at music collectors who want a portable, auditable copy of their library and at operators who need to migrate or rebuild an account without manual reassembly. The tool requires Python 3.12 or newer and is licensed under [AGPL-3.0](../LICENSE).

## Documentation map

| Document | Scope | Read by |
| --- | --- | --- |
| [getting-started.md](getting-started.md) | Installation, authentication, basic export and import workflows, and common troubleshooting. | new contributor, operator |
| [architecture.md](architecture.md) | The layered module layout, the role of each core and engine module, and the three-layer source organisation. | new contributor, maintainer |
| [data-flow.md](data-flow.md) | A step-by-step trace of an import, from CSV parsing through concurrent matching, chunked uploads, recovery, and audit logging. | new contributor, maintainer |
| [cli-reference.md](cli-reference.md) | Every command, argument, and flag exposed by the `tidal-sync` console script. | operator, new contributor |
| [telemetry.md](telemetry.md) | How the JSONL audit logs are produced, rotated, redacted, and parsed. | operator, maintainer |
| [acceptance.md](acceptance.md) | A manual end-to-end checklist run against throwaway accounts, covering export, import, clear, and failure handling. | maintainer |

## Start here

Read the documents in this order to move from concepts to implementation:

1. [getting-started.md](getting-started.md) - install the tool, log in, and run a first export.
2. [architecture.md](architecture.md) - understand the layering and which module owns which responsibility.
3. [data-flow.md](data-flow.md) - follow a single import through parsing, matching, upload, and logging.
4. [cli-reference.md](cli-reference.md) - consult the exact command and flag surface while you work.
5. [telemetry.md](telemetry.md) - learn to read the audit logs the tool emits on every import.
6. [acceptance.md](acceptance.md) - run the manual checklist before shipping a change.

## Repository at a glance

Top-level directories:

- `src/tidal_sync` - the package source (see the three-layer layout below).
- `tests` - the pytest suite; it runs against fakes and cannot reach real OAuth or the live API.
- `docs` - this documentation set.
- `private` - (.gitignored) working notes, briefs, and planning artefacts. This directory is not part of the shipped package and is excluded from the published source where the project's distribution rules require it.

Three-layer source layout under `src/tidal_sync`:

- `domain` - type safety and validation. Contains `models.py` (Pydantic row models), `protocols.py` (structural typing for `tidalapi` objects), `enums.py` (strict CLI constraints such as `ClearTarget`), and `exceptions.py` (the domain error hierarchy).
- `engine` - concurrent synchronisation logic. Contains `importer.py`, `exporter.py`, `wiping.py`, `upload_recovery.py`, `folders.py`, `network.py`, `workers.py`, `parser.py`, and `match_policy.py`.
- `infrastructure` - cross-cutting concerns. Contains `logger.py` (thread-safe JSONL telemetry, redaction, and template-injection safety).

The CLI and auth modules sit at the package root: `cli.py` (Typer routing and the final error handler) and `auth.py` (OAuth lifecycle and local token storage). The package exposes a single console entry point declared in `pyproject.toml`:

```
tidal-sync = "tidal_sync.cli:app"
```

Run it with `uv run tidal-sync <command>` after installing the package in an active virtual environment.


## Repository root documents

- [../README.md](../README.md) - project overview, key features, licence, and disclaimer.
- [../SECURITY.md](../SECURITY.md) - supported versions and how to report a vulnerability or credential leak privately.

## Related documents

- [getting-started.md](getting-started.md)
- [architecture.md](architecture.md)
- [data-flow.md](data-flow.md)
- [cli-reference.md](cli-reference.md)
- [telemetry.md](telemetry.md)
- [acceptance.md](acceptance.md)
- [../README.md](../README.md)
- [../SECURITY.md](../SECURITY.md)
- [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) - licence summaries for bundled dependencies.
