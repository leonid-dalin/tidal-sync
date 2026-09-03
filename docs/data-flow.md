# Data Flow

This document traces the full lifecycle of a tidal-sync backup: how the export step serialises a Tidal library into local CSV files, and how the import step reads those same files back and reconstructs the library on another account. The two halves are designed as a verified round trip, and an offline round-trip test (`tests/test_roundtrip.py`) confirms that every file written by the exporter is parsed back by the importer in the same shape.

## 1. Purpose and scope

tidal-sync treats your local disk as a faithful snapshot of a Tidal account. The export steps pull the live library down; the import steps push a snapshot back up. The round trip preserves:

- Liked Songs, Liked Albums, and Followed Artists (the core static library).
- The Blocked Artists list (no import path is implemented; Tidal does expose the unblock verb, so a future import is in scope, just not built today).
- User-created playlists, including their V2 folder placement.
- Algorithmic mixes and radios, captured as point-in-time snapshots under `Mixes & Radios`.

What is preserved and what is not:

- Track, album, and artist identity is carried either by a direct `tidal_id` or by an ISRC (tracks) plus a text fallback built from the name and primary artist. If neither resolves on the target account (region lock, removal, or a name that cannot be matched), that item is recorded as failed, never silently invented.
- Folder placement survives the round trip only up to filename sanitisation. Two distinct Tidal folders whose names collapse to the same sanitised string merge into one directory on disk (see section 5).
- Per-playlist track order is not guaranteed to survive a re-import. The snapshot captures the set of tracks; the target account receives them as a staged batch.

## 2. Export flow

The CLI export command drives three exporter coroutines, each fetching from Tidal through the network gate and writing CSV to disk. All three run their sub-tasks concurrently through an `asyncio.TaskGroup`.

1. `export_user_favourites_to_disk` writes four root-level files in the backup directory:

   - `Liked Songs.csv` (tracks)
   - `Liked Albums.csv` (albums)
   - `Followed Artists.csv` (artists)
   - `Blocked Artists.csv` (artists)

2. `export_user_playlists_to_disk` writes each playlist as `Playlists/<folder>/<name>.csv`, preserving the V2 folder structure. Playlists with no folder land directly under `Playlists/`. V1 playlists are de-duplicated because the Tidal API otherwise yields folder contents twice. A `UniquePathAllocator` hands out collision-free paths, so two playlists with the same name become `Name.csv` and `Name-2.csv` rather than overwriting each other.

3. `export_algorithmic_mixes_to_disk` writes snapshot CSVs under `Mixes & Radios/`, one file per station.

Writes are atomic: each file is built under a `.part` sibling and moved into place only once fully flushed, so a crash mid-write leaves the previous backup untouched rather than a truncated one. Files are encoded `utf-8-sig` so Excel and other tools do not inject a stray Byte Order Mark on the way back in.

Resulting directory layout:

```
backup/
  Liked Songs.csv
  Liked Albums.csv
  Followed Artists.csv
  Blocked Artists.csv
  Playlists/
    <folder A>/
      Playlist One.csv
      Playlist Two.csv
    <folder B>/
      Playlist Three.csv
    Unfiled Playlist.csv        # no folder
  Mixes & Radios/
    My Daily Discovery.csv
    My Mix 1.csv
```

```mermaid
sequenceDiagram
  PLACEHOLDER
  participant CLI
  participant Exporter
  participant Network as network gate
  participant Tidal
  participant Disk

  CLI->>Exporter: export_user_favourites_to_disk(session, base_dir)
  Exporter->>Network: fetch_all_async(user.favorites.tracks)
  Network->>Tidal: GET favourites
  Tidal-->>Network: track objects
  Network-->>Exporter: tracks
  Exporter->>Disk: write_tracks_csv_sync(Liked Songs.csv, tracks)
  Note over Exporter,Disk: same pattern for albums, artists, blocked
  CLI->>Exporter: export_user_playlists_to_disk(session, base_dir)
  Exporter->>Network: build_playlist_folder_map + fetch_all_async(playlists)
  Network->>Tidal: list playlists + V2 folders
  Tidal-->>Exporter: playlists, folder map
  loop per playlist
    Exporter->>Disk: Playlists/<folder>/<name>.csv
  end
  CLI->>Exporter: export_algorithmic_mixes_to_disk(session, base_dir)
  Exporter->>Disk: Mixes & Radios/<station>.csv
```

## 3. CSV schema

The importer reads CSV through `parse_csv[P: BaseModel]` in `engine/parser.py`, which decodes with `utf-8-sig` and falls back to `cp1252` then `latin-1`, cleans each row (strips BOM, null bytes, stray un-headered columns), and validates it against a Pydantic model. Rows that fail validation are dropped and logged individually, so one bad row never halts the run. If zero rows validate, `parse_csv` raises `ValueError` rather than returning an empty list, so an empty or mis-headed file fails loudly instead of importing nothing silently.

Three Pydantic row models exist, under `domain/models.py`, and each export produces exactly the corresponding file:

| Model    | Produced by                          | Columns                                             |
|----------|--------------------------------------|-----------------------------------------------------|
| TrackRow | `Liked Songs.csv`, playlist CSVs, mix/radio CSVs | `track_name`, `artist_name`, `album_name`, `isrc`, `tidal_id` |
| AlbumRow | `Liked Albums.csv`                   | `album_name`, `artist_name`, `tidal_id`             |
| ArtistRow| `Followed Artists.csv`, `Blocked Artists.csv` | `artist_name`, `tidal_id`                |

Field notes:

- `TrackRow` accepts legacy header spellings via `AliasChoices`, so `Track Name`, `Artist Name(s)`, `ISRC`, `Tidal - id`, `id`, and their snake_case forms all map to the right field. This is what lets files from Exportify, TuneMyMusic, and tidal-sync itself all parse.
- `isrc` and `tidal_id` are optional. A row with neither still parses; it simply has no high-fidelity key for the import match step (see section 4).
- `album` and `playlist_name` exist on `TrackRow` but are not columns in the exporter's output; they are part of the model so third-party exports round-trip without rejection.

The offline round-trip test writes each of the three root files and asserts `parse_csv` returns the expected row count, locking the export-to-import contract in place.

## 4. Import flow

The CLI import command calls `import_collection_from_disk`. It accepts a single `.csv` file or a directory, and for a directory it walks every `*.csv` beneath it (`rglob`), importing each file independently so one failure does not abort the rest. After the run it prints an audit report: items added, skipped (already owned or duplicates), and failed (not found on Tidal). Machine-readable logs land in `import_reports/`.

Routing happens in `resolve_and_import_playlist`, which inspects the file name and its parent directory:

- `Liked Songs.csv` routes to the favourites importer.
- `Liked Albums.csv` routes to the albums importer.
- `Followed Artists.csv` routes to the artists importer (blocked artists have no import path today; a future import is in scope but not built).
- Any other file defaults to track processing. A parent directory named `Mixes & Radios` (or `Mixes and Radios`) is tagged so, and a file under a `Playlists/<folder>` tree is tagged with that folder name so it can be re-homed on import.

For each file the importer:

1. Parses and validates with `parse_csv` (section 3).
2. Scans the destination (Liked Songs, or the named playlist, creating it if needed) and builds a set of `existing_ids` for de-duplication. If a folder name was detected, `ensure_v2_folder_exists` resolves or creates the V2 folder and the new playlist is assigned to it.
3. Resolves every item to a Tidal id via `resolve_track_to_id` (tracks) or an equivalent inline search (albums, artists).
4. Calls `decide()` to classify the item, which either stages the id, skips a duplicate, or records a failure.
5. Uploads the staged batch through `upload_batch_with_recovery`, which increments the session `added` counter only after the server confirms each chunk.

`resolve_track_to_id` resolves in this order:

1. A direct `tidal_id`, if present, is used immediately.
2. Otherwise, if an `isrc` is present, it queries `isrc:<code>` and takes the first track result.
3. Otherwise it builds a text query from the track name and its primary artist (the first artist when several are comma-separated) and searches that. The `search_query` attribute was removed from the row model; the fallback query is constructed inside `resolve_track_to_id` itself, not carried as a precomputed field.

Albums and artists follow the same shape but with their own search (`<album_name> <artist_name>` for albums, `artist_name` for artists) and a single-item `add_method` passed straight to `decide()`.

```mermaid
sequenceDiagram
  PLACEHOLDER
  participant CLI
  participant Importer
  participant Parser
  participant Match as resolve_track_to_id / decide
  participant Network as network gate
  participant Tidal
  participant Recovery as upload_recovery

  CLI->>Importer: import_collection_from_disk(target_path)
  Importer->>Parser: parse_csv(file, TrackRow)
  Parser-->>Importer: validated rows
  loop per track row
    Importer->>Match: resolve_track_to_id(name, artist, tidal_id, isrc)
    Match->>Network: search isrc:<code> or "name artist"
    Network->>Tidal: search
    Tidal-->>Match: track id or none
    Match->>Match: decide() -> STAGED / SKIPPED / FAILED
  end
  Importer->>Recovery: upload_batch_with_recovery(chunk, uploader)
  Recovery->>Network: playlist.add / favorites.add_track
  Network->>Tidal: upload
  Tidal-->>Recovery: UploadOutcome(applied, rejected)
  Recovery-->>Importer: stats.added incremented for applied
```

## 5. Folder transport

V2 folders are not exposed by tidalapi, so `engine/folders.py` owns the raw endpoints. Folder identity is carried on disk purely by the sanitised directory name.

On export, `build_playlist_folder_map` fetches the live V2 folders and, for each, runs the raw `folder_name` through `sanitize_filename`. That sanitised name becomes the on-disk directory under `Playlists/`. The map key is the normalised playlist UUID, so each playlist file lands in its folder's directory.

On import, `resolve_and_import_playlist` reads the folder name back from the parent directory of the CSV. It then calls `ensure_v2_folder_exists(session, folder_name)`, which compares the incoming name against `sanitize_filename(folder_name)` of every live V2 folder and returns the existing folder's id if one matches, creating a new folder only when none does. This is why the directory must carry the sanitised form: the raw name lives in Tidal, and the comparison is done on the sanitised form so an existing folder is reused instead of duplicated.

Caveat: folder identity survives the round trip only up to sanitisation. `sanitize_filename` replaces path separators and illegal characters with underscores, so two distinct Tidal folders whose raw names collapse to the same sanitised string map to the same directory and are merged on import. For example `A/B` and `A_B` both become `A_B` on disk. This is an accepted limitation of using the file system as the folder key; the live Tidal names are never altered, only the on-disk representation.

## 6. Match policy and reconciliation

`decide()` in `engine/match_policy.py` classifies one matched item and returns a `MatchDecision`:

- `FAILED` when `matched_id` is empty (no Tidal match). Logged with the failure reason and counted against `stats.failed`.
- `SKIPPED` when the id is already in `existing_ids` (duplicate). The duplicate check runs inside the stats lock and holds it across the set-add and list-append, so two workers matching the same item cannot both decide it is new.
- `ADDED` when an `add_method` was supplied (albums, artists) and the single-item add succeeded; `stats.added` is incremented.
- `STAGED` when no `add_method` was supplied (tracks routed to a playlist or favourites). The id is appended to the shared `ids_to_add` list and the session `added` counter is incremented later by `upload_recovery` once the server accepts the upload. Staging separates the safe local decision from the network call, which still runs concurrently outside the lock.

Batch upload and recovery live in `engine/upload_recovery.py`. `upload_batch_with_recovery` sends a chunk (capped at `CHUNK_SIZE = 50`) through a caller-supplied uploader that returns an `UploadOutcome(applied, rejected)`. Tidal answers 200 while silently skipping tracks it will not accept, so applied and rejected are reported separately rather than inferred from the absence of an exception. Applied ids increment `stats.added`; rejected ids are dropped directly, because Tidal has already named them.

Poison versus retryable:

- `is_poison` reports whether one specific item must be dropped. It returns true for `TidalPoisonError` or `ObjectNotFound`, or for HTTP 403/404.
- A whole-chunk exception that is retryable (HTTP 412 version collision, or 500/502/503/504) is retried once with the same batch. Any other error (auth, rate limit) propagates and is never blamed on a track.
- When a chunk is refused wholesale and the error is poison, the recovery path isolates offenders one at a time (`is_poison` per item) and drops each dead track via `_drop_track`, logging `Dropped Track (Region Locked)`, so the rest of the batch still uploads.

Favourites are the exception to batching. `_build_favorites_uploader` adds one track at a time and stops at the first rejection, returning the applied list and the single rejected id. Because favourites return no per-item result, stopping at the first failure lets the caller resume from that track instead of re-sending tracks already on the server. Playlists, by contrast, use `build_playlist_uploader`, which sends the whole chunk with `onArtifactNotFound=SKIP` and reports which ids survived.

## 7. Related documentation

- [Architecture](architecture.md) for the module map and how the engine, domain, and infrastructure layers fit together.
- [CLI reference](cli-reference.md) for the exact export and import commands, flags, and arguments.
- [Getting started](getting-started.md) for authentication setup and your first backup.
- [Telemetry](telemetry.md) for the audit log format and what each `added` / `skipped` / `failed` line means.
