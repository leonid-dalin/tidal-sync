# Command Line Reference

This document is the operator reference for the `tidal-sync` command line interface. It lists every command, every option with its default, concrete examples, what each command reads and writes, and the exit codes you can expect. Everything here is verified against `src/tidal_sync/cli.py` and `src/tidal_sync/domain/enums.py`.

## Conventions

How to read the syntax in this document:

* Angle brackets mark a required value you supply, for example `<target_path>`.
* Square brackets mark an optional flag, for example `[--profile NAME]`.
* Where a flag has a short and long form, both are shown, for example `--profile`, `-p`. The two are equivalent.
* Default values are stated explicitly per option.

The command runner is `tidal-sync`. All examples below use that prefix. If you run the project from source without an installed entry point, prepend `uv run` (for example `uv run tidal-sync login`).

### Profile model

`tidal-sync` keeps named local profiles so you can manage more than one Tidal account on the same machine. The default profile is named `default`. The dual-account design exists to support cloning: you log in to two accounts under two profile names, then import from one into the other.

Every command that touches an account accepts `--profile`, `-p`. When omitted it defaults to `default`. The `profiles` command lists every stored profile with its Tidal User ID.

### Exit codes

All commands follow the same three-code contract:

* `0` means the command completed successfully.
* `1` means an operational failure. Authentication failures, sync errors, and other runtime problems are caught and printed as a single red line of the form `Authentication Failed: <detail>` or `tidal-sync could not complete: <detail>`.
* `2` means a usage error. Typer/Click rejects problems such as a missing import path, an unrecognised `clear` target, or an unknown command before any work begins and exits with this Click usage-error code.

Stack traces are unusual during normal operation because the expected failure paths above are caught and printed on one line. An uncaught traceback can still surface from an unforeseen bug; treat it as a defect worth reporting rather than something the CLI promises to suppress.

## Commands

### `login`

**Synopsis**
```bash
tidal-sync login [--profile NAME] [-p NAME]
```

**Description**
Authenticates a Tidal account through the standard OAuth flow and saves the session token to a local profile. The profile name lets you keep several active logins at once, which is required for cloning one account into another.

**Options**

* `--profile`, `-p` NAME: Profile name for dual-account management. Default: `default`.

**Example**
```bash
# Log in to the default account
tidal-sync login

# Log in to a second account for cloning
tidal-sync login -p source
```

**Reads and writes**
Reads: nothing from disk before auth. Writes: the saved session token for the named profile under the local credential store.

---

### `logout`

**Synopsis**
```bash
tidal-sync logout [--profile NAME] [-p NAME]
```

**Description**
Securely logs out and wipes session credentials for the named profile. The local token is overwritten with null bytes before deletion to prevent ordinary data recovery.

**Options**

* `--profile`, `-p` NAME: Profile name to wipe. Default: `default`.

**Example**
```bash
# Log out of the default profile
tidal-sync logout

# Log out of a named profile
tidal-sync logout -p source
```

**Reads and writes**
Reads: the token file for the named profile. Writes: overwrites then deletes that token file.

---

### `import`

**Synopsis**
```bash
tidal-sync import <target_path> [--name NAME] [-n NAME] [--profile NAME] [-p NAME]
```

**Description**
Ingests CSV metadata and synchronises it with a Tidal account. The target path is a required argument and must point to a file or directory that already exists (the CLI rejects a missing path before it starts). If you pass a directory, the tool recursively processes every contained CSV file. Existing items in the target library are skipped automatically to avoid duplication.

**Arguments**

* `target_path` (required): Path to a CSV file OR a directory. The path must exist. Typer enforces `exists=True`, so a non-existent path is an immediate error.

**Options**

* `--name`, `-n` NAME: Target playlist name for the import. Applies when importing a single file. Directory imports use the file names as playlist names. Default: `None` (no override; the source name is used).
* `--profile`, `-p` NAME: Which account profile to import into. Default: `default`.

**Examples**
```bash
# Import a single CSV into the default account
tidal-sync import ./my_playlist.csv

# Import one CSV into a named playlist on a named account
tidal-sync import ./my_playlist.csv -n "Road Trip" -p source

# Import every CSV under a directory
tidal-sync import ./monthly_exports -p target
```

**Reads and writes**
Reads: the CSV file(s) at `target_path`; the session token for the selected profile. Writes: audit logs under `./import_reports`; new items added to the Tidal account (existing items are skipped).

---

### `export`

**Synopsis**
```bash
tidal-sync export [--out DIR] [-o DIR] [--profile NAME] [-p NAME]
```

**Description**
Backs up the entire Tidal library to local CSV files. It produces a categorised folder structure covering playlists, liked tracks, saved albums, and followed artists at the chosen output path.

**Options**

* `--out`, `-o` DIR: Output directory for the backup. Default: `./exports`.
* `--profile`, `-p` NAME: Which account profile to export from. Default: `default`.

**Example**
```bash
# Export the default account to the default location
tidal-sync export

# Export a named account to a specific directory
tidal-sync export -o /backups/march -p source
```

**Reads and writes**
Reads: the session token for the selected profile; library data from the Tidal account. Writes: CSV files under the output directory; audit logs under `<output_dir>/reports`.

---

### `clear`

**Synopsis**
```bash
tidal-sync clear <target> [--profile NAME] [-p NAME] [--force] [-f] [--dry-run]
```

**Description**
Destructively removes a category of data from a Tidal account. This action is irreversible. The target is a required argument taken from the `ClearTarget` enum, so only the values below are accepted; anything else is rejected by the CLI before any deletion.

**Arguments**

* `target` (required): The category to clear. Must be one of the `ClearTarget` enum values:

  * `all` - every supported category
  * `tracks` - liked and saved tracks
  * `albums` - saved albums
  * `artists` - followed artists
  * `playlists` - user playlists

**Options**

* `--profile`, `-p` NAME: Which account profile to clear. Default: `default`.
* `--force`, `-f`: Skip the confirmation prompt. Useful for automation. Default: `False`.
* `--dry-run`: Report the counts that would be affected without deleting anything. Default: `False`.

**Irreversibility**
Clearing is permanent. Without `--force` or `--dry-run`, the tool prints the account and target, then prompts you to type the profile name to confirm. Typing anything else aborts with no changes made. With `--force` the prompt is skipped and deletion proceeds immediately.

**Dry-run and folder counts**
The dry-run report prints the number of deletions it would attempt. That count includes folders: the report's requested and deleted totals cover both items and the folders that contained them, so the figure you see in a dry run matches the figure reported after a real run.

**Examples**
```bash
# Preview what would be deleted from the default account
tidal-sync clear all --dry-run

# Delete liked tracks from a named account without prompting
tidal-sync clear tracks -p source --force

# Delete playlists after confirming by typing the profile name
tidal-sync clear playlists -p target
```

**Reads and writes**
Reads: the session token for the selected profile. Writes: audit logs under `./import_reports`. With a real run it deletes the requested category from the Tidal account; with `--dry-run` it writes nothing to the account.

Note: the clear audit log is currently routed to the same `./import_reports` directory as the import audit log. The path is correct as documented but mixes destructive and import telemetry in one folder; routing it under a distinct `clear` directory would be cleaner. Tracked as a code-level follow-up.

---

### `like`

**Synopsis**
```bash
tidal-sync like <kind> <ids>... [--profile NAME] [-p NAME]
```

**Description**
Likes one or more items on the named profile. The kind is a required argument taken from the `FavoriteKind` enum, so only the values below are accepted; anything else (including `playlist`) is rejected by Typer before any request goes out as a usage error.

**Arguments**

* `kind` (required): The kind of favourite to add. Must be one of the `FavoriteKind` enum values:
  * `track` - liked tracks
  * `artist` - followed artists
  * `album` - saved albums
* `ids` (required, one or more): One or more ids or Tidal share URLs. Bare numeric strings and Tidal share URLs (`/track/<id>`, `/artist/<id>`, `/album/<id>`) are accepted; anything unparseable is rejected before any request goes out.

**Options**

* `--profile`, `-p` NAME: Which account profile to like into. Default: `default`.

**Examples**
```bash
# Like three tracks on the default account
tidal-sync like track 20019287 19782830 19781477

# Follow two artists on a named account
tidal-sync like artist 4894212 8107285 -p target

# Save three albums parsed from share URLs
tidal-sync like album https://listen.tidal.com/album/20019282 https://tidal.com/album/19781468 19782820
```

**Reads and writes**
Reads: the session token for the selected profile. Writes: nothing on disk; the named items appear as favourites on the Tidal account for the selected profile.

---

### `unlike`

**Synopsis**
```bash
tidal-sync unlike <kind> <ids>... [--profile NAME] [-p NAME]
```

**Description**
Removes one or more items from the favourites on the named profile. The kind is a required argument taken from the `FavoriteKind` enum, so only the values below are accepted; anything else is rejected by Typer before any request goes out as a usage error.

**Arguments**

* `kind` (required): The kind of favourite to remove. Must be one of the `FavoriteKind` enum values:
  * `track` - liked tracks
  * `artist` - followed artists
  * `album` - saved albums
* `ids` (required, one or more): One or more ids or Tidal share URLs. Bare numeric strings and Tidal share URLs are accepted; anything unparseable is rejected before any request goes out.

**Options**

* `--profile`, `-p` NAME: Which account profile to unlike from. Default: `default`.

**Examples**
```bash
# Remove three tracks from the default account favourites
tidal-sync unlike track 20019287 19782830 19781477

# Unfollow one artist on a named account
tidal-sync unlike artist 4894212 -p source

# Remove one saved album parsed from a share URL
tidal-sync unlike album https://tidal.com/album/20019282 -p target
```

**Reads and writes**
Reads: the session token for the selected profile. Writes: nothing on disk; the named items disappear from the Tidal account favourites.

---

### `block`

**Synopsis**
```bash
tidal-sync block <ids>... [--profile NAME] [-p NAME] [--force] [-f]
```

**Description**
Blocks one or more artists on the named profile. Each id is sent on its own POST request, so the command reports exactly which artist failed if the run is not clean. The ids accept bare numeric strings and Tidal share URLs (`/artist/<id>`); anything unparseable is rejected before any request goes out.

The command is destructive at scale. When the resolved id list exceeds ten ids and `--force` is absent, the CLI prints one rich line per id then asks the operator to retype the profile name; a mismatched answer aborts before any request goes out. Ten is the threshold under which no prompt appears. Use `--force` in automation to skip the prompt.

**Arguments**

* `ids` (required, one or more): One or more artist ids or Tidal share URLs.

**Options**

* `--profile`, `-p` NAME: Which account profile to block on. Default: `default`.
* `--force`, `-f`: Skip the confirmation prompt for batches above the ten-id rail. Default: `False`.

**Examples**
```bash
# Block two artists on the default account
tidal-sync block 4894212 8107285

# Block an artist from a share URL on a named account
tidal-sync block https://tidal.com/artist/4894212 -p target

# Block a large batch without prompting
tidal-sync block $(cat blocklist.txt) --force
```

**Reads and writes**
Reads: the session token for the selected profile. Writes: nothing on disk; the named artists disappear from the Tidal account discoverable catalogue and appear in the account's block list.

---

### `unblock`

**Synopsis**
```bash
tidal-sync unblock <ids>... [--profile NAME] [-p NAME]
```

**Description**
Unblocks one or more artists on the named profile. Each id is sent on its own DELETE request, so the command reports exactly which artist failed if the run is not clean. The ids accept bare numeric strings and Tidal share URLs (`/artist/<id>`); anything unparseable is rejected before any request goes out.

Unblock is restorative and has no safety rail. There is no `--force` because there is nothing to confirm past the standard one-line-per-id report.

**Arguments**

* `ids` (required, one or more): One or more artist ids or Tidal share URLs.

**Options**

* `--profile`, `-p` NAME: Which account profile to unblock on. Default: `default`.

**Examples**
```bash
# Unblock two artists on the default account
tidal-sync unblock 4894212 8107285

# Unblock an artist from a share URL on a named account
tidal-sync unblock https://tidal.com/artist/4894212 -p source
```

**Reads and writes**
Reads: the session token for the selected profile. Writes: nothing on disk; the named artists are removed from the Tidal account block list.

---

### `profiles`

**Synopsis**
```bash
tidal-sync profiles
```

**Description**
Lists every authenticated Tidal profile stored locally, showing each profile name with its associated Tidal User ID. This is an alias of `list_profiles`. When no profiles exist, it prints a hint to run `tidal-sync login`.

**Options**
None.

**Example**
```bash
tidal-sync profiles
```

**Reads and writes**
Reads: the local profile store. Writes: nothing.

## Error handling

The CLI is built so operators get a clear message, not a crash dump.

* Authentication failure: printed as a single red line of the form `Authentication Failed: <detail>`, then exit code `1`. No traceback.
* Operational failure (for example a sync or export error): printed as a single red line of the form `tidal-sync could not complete: <detail>`, then exit code `1`.
* Single-file import with no valid rows: the error names the file, so you see which input failed rather than a stack trace.

In every case the process returns a non-zero exit code suitable for scripting, and the message stays on one line in red.

## Command dispatch

```
+---------------------------------------------------+
|                  tidal-sync                        |
|                                                    |
|   login  ──► autenticate ──► save profile token   |
|   logout ──► secure wipe profile token             |
|   import ──► read CSV(s) ──► sync to account       |
|   export ──► read library ──► write CSV backup     |
|   clear  ──► confirm ──► purge category (irreversible)|
|   profiles ► list stored profiles + user IDs       |
+---------------------------------------------------+
PLACEHOLDER
```

## Related docs

* [Getting started](getting-started.md)
* [Architecture](architecture.md)
* [Data flow](data-flow.md)
* [Telemetry](telemetry.md)
