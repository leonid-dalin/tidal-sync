# Acceptance checklist

Run against a throwaway account. Every step here is destructive to that
account by design.

Automated tests run against fakes, so they cannot reach real OAuth, real
pagination, the V2 folder transport, or the confirmation ordering. This
checklist covers what the fakes cannot.

## Setup

1. `tidal-sync login --profile acc-a`
2. `tidal-sync login --profile acc-b`

Both profiles must point at different accounts.

## Export

3. `tidal-sync export --profile acc-a --out ./acc-a`
4. Open each CSV and compare the row count against the account.
5. Check that playlist folders are named, not flattened into one list.

## Import

6. `tidal-sync import --profile acc-b ./acc-a`
7. Re-export acc-b: `tidal-sync export --profile acc-b --out ./acc-b`
8. Diff `./acc-a` against `./acc-b`. Counts must match.
9. Confirm followed artists were restored. Followed artists were not
   restored before this change.

## Clear

10. `tidal-sync clear tracks --profile acc-b --dry-run`
11. Repeat without `--dry-run`. It must prompt and name the profile.
12. Decline the prompt. Nothing may change.
13. `tidal-sync clear all --profile acc-b --force`
14. Re-export. Every category must be empty.

## Failure handling

15. Put one corrupt CSV in the directory and import. The run must finish,
    report the file by name, and still import the rest.
