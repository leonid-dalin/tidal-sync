# Getting Started with 🌊 Tidal Sync

## Overview

Tidal Sync is a command-line tool that lets you back up, restore, and clone your entire Tidal library. It uses exact Tidal IDs and ISRC codes for 1-to-1 matching and runs concurrent operations to handle large libraries quickly.

## Prerequisites

- macOS, Linux, or Windows (**PowerShell**)
- [uv](https://docs.astral.sh/uv/) installed on your system
- An active Tidal account

## Getting Started

1. **Clone the repository:**
   ```bash
   git clone https://github.com/leonid-dalin/tidal-sync
   cd tidal-sync
   ```
2. **Install the package and dependencies:**
    ```bash
    uv venv
    uv pip install -e .
    source .venv/bin/activate # macOS / Linux
   .venv\Scripts\Activate.ps1 # Windows (PowerShell) ONLY
   ```
3. **Authenticate your account:**
   ```bash
    uv run tidal-sync login
   ```
   
This will open a browser window for you to authorise the application.

## Configuration

Tidal Sync requires zero manual configuration.

It automatically creates a hidden directory at `~/.tidal_sync` to store your OAuth session tokens. These files are secured using strict POSIX file permissions (chmod 600) so only your user account can read or write to them.

When you run an import job, the tool automatically generates a machine-readable JSONL audit log in an `import_reports/` directory relative to where you ran the command.

## Examples

### Exporting your library

To back up your custom playlists, liked songs, saved albums, and followed artists to a local folder:
```bash
uv run tidal-sync export --out ./my_tidal_backup
```

### Cloning data to a new account

You can maintain multiple active logins simultaneously using profiles. This makes it easy to migrate your library from one account to another.
```bash
# 1. Log into both accounts
uv run tidal-sync login -p source_account
uv run tidal-sync login -p destination_account

# 2. Export data from the source
uv run tidal-sync export -p source_account -o ./account_transfer

# 3. Import data into the destination
uv run tidal-sync import ./account_transfer -p destination_account
```

## Troubleshooting

* **Rate Limiting (HTTP 429):** If you are importing a massive library, you do not need to throttle the script yourself. The tool has a built-in exponential backoff engine that will automatically pause and resume if Tidal flags you for making too many requests.
* **Missing Tracks During Import:** Tidal occasionally region-locks certain tracks. If a batch upload fails because of a locked track, the tool automatically bisects the batch to isolate and drop the poison track, uploading the rest successfully. You can check the `import_reports/` log to see exactly which IDs were dropped.
* **Account Collisions:** If you try to log in to the same Tidal account under two different profile names, the tool will warn you to prevent redundant API calls.