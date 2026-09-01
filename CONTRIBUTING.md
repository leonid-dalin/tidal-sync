# Contributing to tidal-sync

Thanks for taking the time to contribute. This document explains how to set up a
development environment, the standards your work must meet, and how the review
process works.

## Development setup

tidal-sync requires Python 3.12 or newer.

```bash
# clone, then from the repo root
uv sync            # preferred: creates .venv and installs [dev] extras
# or
pip install -e ".[dev]"
```

On Windows use the virtual environment interpreter directly rather than a global
`tidal-sync` on PATH:

```bash
.venv/Scripts/python.exe -m pytest
```

## Before opening a pull request

Run the full gate locally. CI runs the same checks across Python 3.12, 3.13 and
3.14, so a local pass on one interpreter is the minimum:

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
.venv/Scripts/python.exe -m mypy src
```

All four must be green. The suite runs entirely against fakes, so also exercise
real-account paths (login, export, import, clear) manually when you touch the
network or destructive code.

## Coding standards

- **AGPL headers:** every source file under `src/tidal_sync` carries the AGPL
  header. Keep it on new files.
- **Typing:** `mypy src` reports zero errors. Cross-package imports rely on
  `src/tidal_sync/__init__.py` existing; do not remove it.
- **Comments:** explain non-obvious *why*, never restate what the code shows.

## Review process

- Fork the repository and open a pull request against `main`. `CODEOWNERS` routes
  every PR to the maintainer for review.
- The maintainer reviews all PRs and does not merge without that review.
- The maintainer does not force-push `main`.

If you have write access, branch from `main` and open the PR from the same
repository; the same review-before-merge rule applies.

## Where to learn the system

Start with [docs/README.md](docs/README.md) for the documentation map, then read
[architecture.md](docs/architecture.md) and [data-flow.md](docs/data-flow.md).
Licence obligations for bundled dependencies are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Report security issues through the private channel described in
[SECURITY.md](SECURITY.md), not through public issues.
