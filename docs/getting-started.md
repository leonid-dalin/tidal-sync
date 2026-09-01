# Getting Started with tidal-sync

## Purpose and scope

This guide takes you from a fresh checkout to a working backup and restore of your Tidal library. By the end you will have installed tidal-sync, authenticated your account, and run your first export, import, and clear. It also covers how profiles work so you can manage or clone multiple accounts.

## Prerequisites

- Python 3.12 or newer
- A Tidal account with an active subscription
- git, to clone the repository

## Installation

Clone the repository and install the package in editable mode with the development extras:

```bash
git clone https://github.com/leonid-dalin/tidal-sync
cd tidal-sync
pip install -e ".[dev]"
```

If you prefer [uv](https://docs.astral.sh/uv/), run `uv sync` from the repo root instead. Either route installs the `tidal-sync` console script, so the commands below are available directly from your shell:

```bash
tidal-sync --help
```

On Windows, run the package from the virtual environment's interpreter rather than relying on a global `tidal-sync` on PATH:

```powershell
.venv\Scripts\python.exe -m tidal_sync --help
```

## Authentication

Log in to create and store a session. Use a profile name so you can keep several accounts separate:

```bash
tidal-sync login --profile default
```

This opens an OAuth window in your browser. On success the token is written to `~/.tidal_sync` (for example `~/.tidal_sync/default.json`). The file is created with strict permissions (mode 600) so only your user account can read or write it.

To log out and remove a profile's credentials, run:

```bash
tidal-sync logout --profile default
```

`logout` does not simply delete the token file. It zero-fills the file on disk before unlinking it, which makes recovery of the credentials much harder. If the secure wipe cannot be verified, the file is left in place and you are told to remove it manually.

Profile names let you run two accounts side by side for cloning. Log in to each under a distinct alias, for example `source` and `destination`.

> Diagram: auth lifecycle (load, OAuth, collision check, save, secure wipe) (see architecture.md).

## First export

Back up your library to a folder of CSV files:

```bash
tidal-sync export --out ./exports --profile default
```

This writes the following files into `./exports`:

- `Liked Songs.csv`
- `Liked Albums.csv`
- `Followed Artists.csv`
- `Blocked Artists.csv`
- `Playlists/` (a tree of your playlists; V2 folder structure is preserved)

If a category is empty, tidal-sync writes no file for it and prints nothing. There is no error and no empty placeholder.

## First import

Restore from an export directory:

```bash
tidal-sync import ./exports --profile default
```

You can also point `import` at a single CSV file instead of a directory. Use `--name` to set the name of the target playlist when importing one:

```bash
tidal-sync import ./exports/Playlists/My_Mix.csv --name "Restored Mix" --profile default
```

Import writes to your Tidal account. Existing items are skipped to avoid duplicates, but the writes themselves are not reversible. Review the export before running an import against an account you care about.

## Clearing data

`clear` is the only command that changes your Tidal account itself. Export and import read or write local files, and an import skips what already exists, but `clear` permanently removes a category of data from the remote account. Those removals are made on Tidal's servers, not on your disk, and cannot be undone. Valid targets are `all`, `tracks`, `albums`, `artists`, and `playlists`.

Always run a dry run first to see what would be deleted:

```bash
tidal-sync clear playlists --dry-run
```

When you are certain, run it for real. Without `--force`, tidal-sync prompts you to type the profile name to confirm:

```bash
tidal-sync clear playlists --profile default
```

To skip the confirmation prompt, pass `--force`:

```bash
tidal-sync clear playlists --force --profile default
```

There is no undo. A dry run is the only safe preview.

## Profiles

List every authenticated profile stored locally:

```bash
tidal-sync profiles
```

Each line shows the profile name and its Tidal user ID. The same Tidal account cannot be saved under two profile names; logging in under a second alias for an account you already saved is rejected to stop a `clear --profile` from wiping the wrong account.

To clone one account into another, log in to both, export from the source profile, then import into the destination profile. The full data-flow walkthrough is in [data-flow.md](data-flow.md).

## Related docs

- [architecture.md](architecture.md) (auth lifecycle and module layout)
- [data-flow.md](data-flow.md) (export, import, and clone workflows)
- [cli-reference.md](cli-reference.md) (every command and option)
- [telemetry.md](telemetry.md) (logging and audit reports)

## Next steps

```text
1. Run `tidal-sync login --profile default` and complete the OAuth prompt.
2. Run `tidal-sync export --out ./exports --profile default` for your first backup.
3. Inspect ./exports to confirm the CSVs and Playlists/ tree.
4. Read data-flow.md to plan a clone, or cli-reference.md for the full command set.
```
