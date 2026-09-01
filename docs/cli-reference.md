# Command Line Reference

This document details the available commands, arguments, and flags for the `tidal-sync` CLI.

## Global Options

Most commands accept a profile flag. This allows you to manage multiple Tidal accounts simultaneously without constantly logging in and out. 

* `--profile`, `-p`: Specifies the account profile to use. If omitted, the tool defaults to the `default` profile.

---

## Commands

### `login`

Authenticates a Tidal account and saves the session token to a local profile. Running this command opens your default web browser for standard OAuth approval.

**Usage:**
```bash
uv run tidal-sync login [OPTIONS]
```

### `logout`

Securely deletes session credentials for a specific profile. It overwrites the local token file with null bytes before deleting it from the disk, preventing standard data recovery.

**Usage:**
```bash
uv run tidal-sync logout [OPTIONS]
```

### `import`

Imports CSV files into your Tidal library. If you point it at a directory, it recursively finds and imports all CSV files. The tool checks your existing library first and automatically skips tracks you already own.

**Usage:**
```bash
uv run tidal-sync import TARGET_PATH [OPTIONS]
```

**Arguments:**
* `TARGET_PATH` _(Required)_: The path to a specific CSV file or a directory containing multiple CSVs.

**Options:**
* `--name`, `-n`: Overrides the target playlist name. This only applies when importing a single file; directory imports use the filenames automatically.

### `export`

Downloads your entire Tidal library (playlists, liked songs, saved albums, and followed artists) to local CSV files. It builds a categorised folder structure at the target destination.

`Followed Artists.csv` is restored on import. `Blocked Artists.csv` is written for reference only; Tidal exposes no API to restore blocked artists, so the importer does not read it.

**Usage:**
```bash
uv run tidal-sync export [OPTIONS]
```

**Options:**
* `--out`, `-`o: The directory where the backup folders will be created. Defaults to `./exports`.

### `clear`

Destructively wipes data from a Tidal account. Unless you provide the force flag, the tool asks for manual confirmation before making any permanent deletions.

**Usage:**
```bash
uv run tidal-sync clear TARGET [OPTIONS]
```

**Arguments:**
* `TARGET` _(Required)_: The category to clear. Valid options are `all`, `tracks`, `albums`, `artists`, or `playlists`.

**Options:**
* `--force`, `-f`: Skips the manual confirmation prompt. Useful for automated scripts.

### `profiles`

Lists all authenticated Tidal profiles currently saved on your machine, displaying the profile names alongside their associated Tidal User IDs.

**Usage:**
```bash
uv run tidal-sync profiles
```