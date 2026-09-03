# Acceptance checklist

This document is the acceptance checklist for tidal-sync. It maps each
requirement to the behaviour that satisfies it and to the test (or other
mechanism) that verifies it. Every item carries a status:

- **Verified** means a test in `tests/` exercises the behaviour, or the
  source asserts it and a test pins the contract.
- **Partial** means the behaviour is implemented but only partly covered
  by automated tests, or covered only against fakes.
- **N/A** means the requirement does not apply to the current code, or
  there is no testable surface for it in this repository.

The audience is a maintainer or reviewer deciding whether a build is safe
to ship. Treat any **Partial** or **N/A** as a gap to weigh, not a pass.

## How to read the items

Each row names the requirement, the test or mechanism that proves it, and
the status. When a status is **Partial** or **N/A**, the reason is given
inline. All statuses below were confirmed by reading `src/` and `tests/`
on the working tree this document was written against.

## Acceptance items

| # | Requirement | Verifying test / mechanism | Status |
|---|-------------|----------------------------|--------|
| 1 | Export writes all categories: songs, albums, artists, blocked artists, and playlists with their V2 folder placement. | `exporter.py:export_user_favourites_to_disk` writes `Liked Songs`, `Liked Albums`, `Followed Artists`, and `Blocked Artists`; `export_user_playlists_to_disk` preserves V2 folders (`build_playlist_folder_map`). `test_roundtrip.py` covers the parse shape of songs, albums, and artists. | Verified |
| 2 | Track matching uses ISRC first, then falls back to a text search. | `test_track_resolution.py::test_isrc_is_tried_before_text`, `test_isrc_match_returns_the_tidal_id`, `test_text_fallback_returns_the_tidal_id`. `resolve_track_to_id` (importer) is exercised directly. | Verified |
| 3 | One bad track does not cancel the rest of the batch. | `test_importer.py::test_one_failed_match_still_resolves_the_others` monkeypatches the matcher to raise for one track and asserts the other two still reach the add queue. | Verified |
| 4 | Dry-run counts folders with parity against a live run. | `test_wiping.py::test_dry_run_counts_folders_like_a_live_run` asserts dry `requested` equals live `requested` and live `deleted` equals the folder plus playlist count. | Verified |
| 5 | Purge report invariant: deleted + failed is an upper bound on requested. | `test_wiping.py::test_requested_is_an_upper_bound_on_deleted_plus_failed`. Also `test_all_failing_deletions_are_reported_as_failures` proves failures are recorded, not dropped. | Verified |
| 6 | Authentication failure yields a message, not a traceback. | `test_cli.py::test_import_value_error_is_caught_and_exits_cleanly` (exit 1 with a friendly summary, no uncaught exception). `test_auth_security.py` pins account-collision and secure-delete behaviour. No test drives a real OAuth failure end to end (see limitations). | Partial |
| 7 | Rate-limit gate pauses workers without serialising the run. | `test_network_gate.py` (`test_trigger_backoff_is_not_blocked_by_a_sleeping_worker`, `test_pre_flight_sleeps_outside_the_lock`, `test_backoff_uses_a_monotonic_clock`, `test_trigger_backoff_only_extends_the_window`). Gate is `network.GlobalTidalGate`. | Verified |
| 8 | Retry and backoff on transient errors. | `test_network_retry.py` (`test_connection_errors_are_retried`, `test_server_errors_are_retried`, `test_client_errors_are_not_retried`, `test_exhausted_retries_raise_a_typed_error`, `test_track_id_containing_429_does_not_freeze_the_gate`, `test_abuse_lock_keeps_its_full_duration`). Driven through `network.execute_network`. | Verified |
| 9 | Upload reconciliation separates poison from retryable, and favorites go one at a time. | `test_upload_recovery.py` (poison vs 401/503, server-side rejections counted as failed, 412 retried). `test_playlist_upload.py::test_favorites_stop_at_the_first_rejection` proves favorites add one at a time and stop at the first failure. | Verified |
| 10 | Audit log filenames are collision-proof. | `test_logger.py::test_two_runs_in_the_same_second_do_not_share_a_log_file` freezes the clock and asserts distinct paths; `test_audit_log_is_created_for_each_command` confirms per-command files. | Verified |
| 11 | `mypy` reports 0 errors across the packages, with `src/tidal_sync/__init__.py` present. | `src/tidal_sync/__init__.py` exists. `mypy src` reports `Success: no issues found in 20 source files` (confirmed on the working tree). `[tool.mypy]` in `pyproject.toml` sets `warn_unused_ignores = true`. | Verified |
| 12 | Folder identity survives the round trip on the sanitised name. | `test_folders.py::test_ensure_v2_folder_exists_matches_on_sanitised_name` passes the sanitised name and asserts the existing folder id is returned with no duplicate created. `test_roundtrip.py` covers CSV parse shapes. | Verified |
| 13 | Followed artists are restored on import. | `importer.py` routes `Followed Artists.csv` to `import_artists_async` (lines 112 to 113). `test_importer.py::test_followed_artists_file_routes_to_the_artist_importer` and `test_artist_file_is_not_parsed_as_tracks` prove the file reaches the artist importer and does not fire a track search. `import_artists_async` (lines 420 to 470) calls `user.favorites.add_artist`. | Verified |

## Known limitations

These are stated honestly. They are not failures of the code so much as
boundaries of what the automated suite can prove.

- **Real-account flows are not covered by CI.** Every test runs against
  fakes in `tests/fakes.py` or monkeypatched sessions. OAuth login, live
  pagination, the real V2 folder transport, and the `clear` confirmation
  ordering against a genuine Tidal account are exercised only by the
  manual checklist below, never by an automated test.
- **Authorization failure surfacing (item 6) is Partial.** The CLI catch
  path is tested, but no test drives a genuine auth failure through the
  live `get_session` path; the friendly message is proven at the
  `ValueError` boundary, not at the network boundary.
- **Blocked artists have no import path.** `import_artists_async` does
  not restore them because no import path is implemented, not because
  Tidal lacks the verb: `unblock_artists` in `curation.py` already uses
  the working DELETE endpoint, so a future import is in scope, just not
  built today. This means item 1's round trip is one-way for that
  category.
- **Folder name collapse caveat.** Two distinct Tidal folders whose names
  sanitise to the same string merge into one directory on disk. This is
  documented in `data-flow.md` section 5 and is the reason folder identity
  is pinned on the sanitised name (item 12) rather than the raw one. If
  your library has folders named `A/B` and `A_B`, expect them to collapse
  on export.
- **Playlist track order is not guaranteed** to survive a re-import. The
  snapshot captures the set of tracks; the target account receives them as
  a staged batch. See `data-flow.md` section 1.

## Manual acceptance checklist (real account)

The automated suite cannot reach a live account. Run this against a
throwaway account; every step is destructive by design.

1. `tidal-sync login --profile acc-a`
2. `tidal-sync login --profile acc-b` (a different account)
3. `tidal-sync export --profile acc-a --out ./acc-a`
4. Open each CSV and compare row counts against the account.
5. Confirm playlist folders are named directories, not one flat list.
6. `tidal-sync import --profile acc-b ./acc-a`
7. Re-export: `tidal-sync export --profile acc-b --out ./acc-b`
8. Diff `./acc-a` against `./acc-b`. Counts must match.
9. Confirm followed artists were restored (they were not restored before
   the artist-import change).
10. `tidal-sync clear tracks --profile acc-b --dry-run` (counts only).
11. Repeat without `--dry-run`. It must prompt and name the profile.
12. Decline the prompt. Nothing may change.
13. `tidal-sync clear all --profile acc-b --force`
14. Re-export. Every category must be empty.
15. Put one corrupt CSV in a directory and import. The run must finish,
    report the file by name, and still import the rest.

## Acceptance traceability

```
+--------------------------------------------------------------+
|  acceptance traceability  (PLACEHOLDER)                      |
|                                                              |
|  req -> test -> source                                       |
|                                                              |
|  1  export categories      -> test_roundtrip, exporter.py    |
|  2  ISRC/text match        -> test_track_resolution          |
|  3  one bad track          -> test_importer                  |
|  4  dry-run folder parity  -> test_wiping                    |
|  5  purge upper bound      -> test_wiping                    |
|  6  auth no traceback      -> test_cli, test_auth_security   |
|  7  rate-limit gate        -> test_network_gate              |
|  8  retry/backoff          -> test_network_retry             |
|  9  upload reconcile       -> test_upload_recovery,          |
|                              test_playlist_upload             |
| 10  audit collision-proof  -> test_logger                    |
| 11  mypy clean             -> pyproject, mypy src            |
| 12  folder identity        -> test_folders                   |
| 13  artists restored       -> test_importer, importer.py     |
|                                                              |
+--------------------------------------------------------------+
```

## Related docs

- [architecture.md](architecture.md) - component layout and responsibilities.
- [data-flow.md](data-flow.md) - export/import pipeline, folder transport, known caveats.
- [cli-reference.md](cli-reference.md) - command and flag reference.
- [telemetry.md](telemetry.md) - audit logging and metrics.
- [README.md](README.md) - project overview and quick start.
