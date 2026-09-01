```text
████████╗██╗ ██████╗  █████╗ ██╗             ███████╗██╗   ██╗███╗   ██╗ ██████╗
╚══██╔══╝██║ ██╔══██╗██╔══██╗██║             ██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝
   ██║   ██║ ██║  ██║███████║██║      █████╗ ███████╗ ╚████╔╝ ██╔██╗ ██║██║
   ██║   ██║ ██║  ██║██╔══██║██║      ╚════╝ ╚════██║  ╚██╔╝  ██║╚██╗██║██║
   ██║   ██║ ██████╔╝██║  ██║███████╗        ███████║   ██║   ██║ ╚████║╚██████╗
   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝        ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝
```

# 🌊 Tidal Sync `CLI`

[![Version](https://img.shields.io/badge/version-1.1.1-blue)](https://github.com/leonid-dalin/tidal-sync)
[![License](https://img.shields.io/badge/license-AGPLv3-green)](LICENSE)
[![Python](https://img.shields.io/badge/python->=3.12-blue)](https://www.python.org)
[![CI](https://img.shields.io/badge/CI-3.12%20%7C%203.13%20%7C%203.14-success)](.github/workflows/ci.yml)

A command-line tool that backs up, restores, and clones Tidal music libraries. It
reads a Tidal account's playlists, liked songs, saved albums, and followed
artists into local CSV files, then writes them back into the same or a different
account using exact Tidal IDs and ISRC codes for one-to-one matching. It is aimed
at music collectors who want a portable, auditable copy of their library and at
operators who need to migrate or rebuild an account without manual reassembly.

## 📖 Documentation

All guides and technical references live in the `docs/` directory:

* **[Getting Started](docs/getting-started.md)**: Installation, authentication, and basic usage (exporting, importing, cloning).
* **[CLI Reference](docs/cli-reference.md)**: Exhaustive list of commands, flags, and arguments.
* **[Architecture](docs/architecture.md)**: Layered overview of the routing, engine, and infrastructure modules.
* **[Data Flow](docs/data-flow.md)**: Step-by-step trace of how a track moves from a local CSV to the Tidal servers and back.
* **[Telemetry](docs/telemetry.md)**: How the JSONL audit logging works and how to read the output.
* **[Documentation Index](docs/README.md)**: Full navigation map across every guide.

## ✨ Key Features

* Backs up your **custom playlists, liked songs, saved albums,** and **followed artists** to local CSV files (V2 folder structure preserved).
* Scans your destination before importing and skips tracks you already own.
* Point the tool at a backup directory and it automatically finds and imports every CSV inside.
* Log into multiple accounts to clone data from a "Source" account to a "Destination" account.
* Matches tracks with exact `Tidal IDs` and `ISRC` codes, falling back to text search only when metadata is missing.
* Runs matching and uploads concurrently through an `asyncio` worker pool with a rate-limit gate, keeping bulk imports fast without tripping Tidal's abuse defences.
* If a batch upload fails because Tidal region-locks a track, it isolates and drops the broken track, uploading the rest.
* Emits machine-readable JSONL audit logs so you can see exactly which tracks were added, skipped, or failed.
* Performs a logical zero-fill overwrite on session tokens before deleting them, mitigating disk data recovery.

## 🚀 Installation

Requires **Python 3.12 or newer**.

```bash
# clone, then from the repo root
git clone https://github.com/leonid-dalin/tidal-sync.git
cd tidal-sync

# install with dev tooling (pytest, ruff, mypy)
uv sync
# or
pip install -e ".[dev]"
```

This installs the `tidal-sync` console script. On Windows use the virtual
environment interpreter directly rather than a global on PATH:

```bash
.venv/Scripts/python.exe -m tidal-sync --help
```

## 🛠️ Usage

Authenticate once, then export or import. Every command accepts `--profile`
(default `default`) for multi-account management.

```bash
# authenticate (opens an OAuth window)
tidal-sync login --profile default

# back up the whole library to ./exports
tidal-sync export --out ./exports --profile default

# restore from a backup directory (irreversible writes to your account)
tidal-sync import ./exports --profile default

# clone: export from source, import into destination
tidal-sync export --out ./source-backup --profile source
tidal-sync import ./source-backup --profile destination

# irreversible: preview, then run for real
tidal-sync clear playlists --dry-run
tidal-sync clear playlists --profile default
```

`clear` is the only command that changes your Tidal account itself; the removals
are made on Tidal's servers and cannot be undone. Always run `--dry-run` first.

See [CLI Reference](docs/cli-reference.md) for the full command list.

## ⚙️ Configuration

* **Profiles:** credentials are stored per profile under `~/.tidal_sync/` with
  strict file permissions. Use distinct profile names to keep several accounts
  separate. The same Tidal account cannot be saved under two profile names.
* **Audit logs:** each command writes a JSONL audit trail. `import` and `export`
  place it under their output directory; `clear` writes to `./import_reports`.
* **Network gate:** a global rate-limit gate pauses all workers on a 429/403 so
  the account is not flagged.

## 🤝 Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup and review process, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
for community standards. Security issues go through the private channel in
[SECURITY.md](SECURITY.md), not public issues.

## ⚖️ Licence & Open Source

This project is licensed under the **GNU Affero General Public Licence v3.0 (AGPLv3)**.

This licence ensures that the software remains free for the public. Crucially, the **Affero** clause dictates that any person or entity using this code to provide a service over a network (such as a hypothetical web-based "Tidal Migration" service) **must** make their full source code available to the community.

The licence summaries for the third-party packages tidal-sync bundles are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

### 🛑 Additional Terms & AI Restriction

**Closed-source commercialisation** of this work is strictly prohibited. Furthermore, I explicitly **withhold consent** for any content in this repository—including, but not limited to code, documentation, and logic—to be used as training data for artificial intelligence (AI) models, large language models (LLMs), or any generative systems. Automated remixing, adaptation, scraping, or building upon this work by AI entities without my explicit written permission is strictly prohibited. `:)`

## ⚠️ Disclaimer & Liability

**`tidal-sync` is an independent, open-source educational tool and is NOT affiliated with, endorsed by, or in any way associated with TIDAL Music AS.**

By using this tool, you agree to the following:

1. **Your Responsibilities:** You are solely responsible for how you use this software. This tool interacts with Tidal's API using standard user credentials. It is your responsibility to ensure your usage complies with [Tidal's Terms and Conditions of Use](https://tidal.com/terms).
2. **Account Risks:** Automated interactions with API endpoints can sometimes be flagged by anti-bot or abuse-prevention systems. The author(s) of `tidal-sync` hold **zero liability** for any account warnings, suspensions, bans, or data loss that may occur as a result of using this tool.
3. **No Warranty:** This software is provided "as is", without warranty of any kind. The author(s) shall not be liable for any claims, damages, or legal repercussions arising from the use of this software.

**Use at your own risk.**
