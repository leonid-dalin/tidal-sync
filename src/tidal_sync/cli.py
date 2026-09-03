# tidal-sync: A high-performance tool for backing up and cloning Tidal libraries.
# Copyright (C) 2026 Leonid Dalin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: infoLeonid@protonMail.com

"""
Command-line interface for tidal-sync.

This module defines the terminal commands using Typer. It routes user
inputs to the core authentication, synchronisation, and clearance engines,
and manages safety prompts for destructive actions.

Example:
    To view available commands in the terminal:
    $ tidal-sync --help
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from .auth import _get_all_profiles, get_session, secure_delete_token
from .cli_blocklist import blocklist_app, report_capped, report_store_error
from .cli_shared import BLOCK_RAIL_THRESHOLD, console
from .domain.enums import ClearTarget, FavoriteKind
from .domain.exceptions import TidalAuthenticationError, TidalSyncError
from .engine import curation
from .engine.exporter import (
    export_algorithmic_mixes_to_disk,
    export_user_favourites_to_disk,
    export_user_playlists_to_disk,
)
from .engine.filterlist import FormatError, detect_format
from .engine.filterlist_apply import execute_apply, plan_apply
from .engine.filterlist_store import StoreError, Subscription, load_subscriptions
from .engine.importer import import_collection_from_disk
from .engine.parser import extract_tidal_id
from .engine.wiping import purge_target_category_async
from .infrastructure.logger import (
    setup_audit_logging,
    setup_global_logging,
    stop_audit_logging,
)

app = typer.Typer(
    help="Modern CLI for managing, importing, exporting, and cloning Tidal libraries."
)
setup_global_logging()


@app.command()
def login(
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Profile name for dual-account management")
    ] = "default",
) -> None:
    """
    Authenticates a Tidal account and saves it to a local profile.

    Use the `--profile` flag to keep multiple active logins simultaneously.
    This is necessary if you intend to clone an account.

    Args:
        profile (str): The name for the saved profile. Defaults to 'default'.
    """
    try:
        get_session(profile)
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1) from e


@app.command()
def logout(
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Profile name to wipe")
    ] = "default",
) -> None:
    """
    Securely logs out and wipes session credentials for a specific profile.

    Args:
        profile (str): The name of the profile to wipe. Defaults to 'default'.
    """
    if not secure_delete_token(profile):
        raise typer.Exit(1)


@app.command(name="import")
def import_data(
    target_path: Annotated[
        Path, typer.Argument(help="Path to a CSV file OR a directory", exists=True)
    ],
    name: Annotated[str | None, typer.Option("--name", "-n", help="Target playlist name")] = None,
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to import into")
    ] = "default",
) -> None:
    """
    Ingests CSV metadata and synchronises it with a Tidal account.

    If the target path is a directory, the tool recursively processes all
    contained CSV files. Existing items in the target library are automatically
    skipped to prevent duplication.

    Args:
        target_path (Path): Path to a CSV file or a directory of CSVs.
        name (str | None): Optional name for the target playlist.
        profile (str): The authentication profile to use for the import.
    """
    try:
        session = get_session(profile)
        setup_audit_logging(Path("./import_reports"))
        try:
            asyncio.run(
                import_collection_from_disk(session, target_path, target_playlist_name=name)
            )
        finally:
            stop_audit_logging()
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1) from e
    except TidalSyncError as e:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {e}")
        raise typer.Exit(1) from e


@app.command(name="export")
def export_all(
    output_dir: Annotated[Path, typer.Option("--out", "-o", help="Output directory")] = Path(
        "./exports"
    ),
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to export from")
    ] = "default",
) -> None:
    """
    Backs up the entire Tidal library to local CSV files.

    Generates a categorised folder structure for playlists, liked tracks,
    albums, and followed artists at the specified output path.

    Args:
        output_dir (Path): The directory where the backup will be stored.
        profile (str): The authentication profile to export from.
    """

    async def run_exports():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(export_user_playlists_to_disk(session, output_dir))
            tg.create_task(export_user_favourites_to_disk(session, output_dir))
            tg.create_task(export_algorithmic_mixes_to_disk(session, output_dir))

    try:
        session = get_session(profile)
        setup_audit_logging(output_dir / "reports")
        asyncio.run(run_exports())
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1) from e
    except TidalSyncError as e:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {e}")
        raise typer.Exit(1) from e
    finally:
        stop_audit_logging()


@app.command()
def clear(
    target: Annotated[ClearTarget, typer.Argument(help="What to clear")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to clear")
    ] = "default",
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation prompt")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report counts without deleting")
    ] = False,
) -> None:
    """
    Destructively removes a category of data from a Tidal account.

    This action is irreversible.
    """
    # Authenticate first so the prompt can name the account being destroyed.
    try:
        session = get_session(profile)
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1) from e
    except TidalSyncError as e:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {e}")
        raise typer.Exit(1) from e

    user_id = getattr(getattr(session, "user", None), "id", "unknown")
    console.print(
        f"[bold red]About to permanently delete {target} from Tidal account "
        f"{user_id} (profile '{profile}').[/bold red]"
    )

    setup_audit_logging(Path("./import_reports"))
    try:
        if not force and not dry_run:
            typed = typer.prompt(f"Type '{profile}' to confirm irreversible deletion of {target}")
            if typed != profile:
                console.print("[red]Confirmation did not match. Aborting.[/red]")
                raise typer.Abort()

        report = asyncio.run(purge_target_category_async(session, target, dry_run=dry_run))
    finally:
        stop_audit_logging()

    if dry_run:
        console.print(f"[yellow]Dry run: would attempt {report.requested} deletions.[/yellow]")
        return

    console.print(f"  Deleted: {report.deleted}")
    if report.failed:
        console.print(f"  [red]Failed: {report.failed}[/red]")
        for detail in report.failures[:10]:
            console.print(f"    [dim]{detail}[/dim]")
        raise typer.Exit(1)

    console.print(
        f"[bold green]Successfully cleared '{target.value}' ({report.deleted} items).[/bold green]"
    )


def _run_favourite_command(
    *,
    profile: str,
    verb_name: str,
    kind: FavoriteKind,
    references: list[str],
    verb: Any = None,
    verb_factory: Callable[[FavoriteKind], Any] | None = None,
) -> None:
    """Shared body for the like and unlike commands.

    The engine callable is chosen by ``kind``: ``verb_factory`` is called
    with the enum so the lookup happens at call time and survives the
    monkeypatch tests use to swap ``curation.like_tracks`` and friends.

    Resolves each reference through ``extract_tidal_id`` (bare id or share
    URL), calls the engine verb inside ``asyncio.run``, prints one rich
    line per id, and exits 1 if the engine reported any rejected id.
    Authentication and sync errors share the except pair from
    ``import_data`` so operators see the same one-line red message as the
    rest of the CLI.
    """
    if verb is None:
        assert verb_factory is not None
        verb = verb_factory(kind)
    try:
        session = get_session(profile)
        try:
            ids = [extract_tidal_id(reference) for reference in references]
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e
        outcome = asyncio.run(verb(session, ids))
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1) from e
    except TidalSyncError as e:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {e}")
        raise typer.Exit(1) from e

    for item_id in outcome.applied:
        console.print(f"  [green]{verb_name} {kind.value} {item_id}[/green]")
    for item_id in outcome.rejected:
        console.print(f"  [red]{verb_name} {kind.value} {item_id}[/red]")

    if outcome.rejected:
        raise typer.Exit(1)


# Look up the engine verb by FavoriteKind at call time. The verb resolution
# happens when the like or unlike command runs, so monkeypatching
# curation.like_tracks (the pattern the CLI tests use) takes effect without
# a parallel table to keep in sync.
def _like_verb(kind: FavoriteKind) -> Any:
    return getattr(curation, f"like_{kind.value}s")


def _unlike_verb(kind: FavoriteKind) -> Any:
    return getattr(curation, f"unlike_{kind.value}s")


# Threshold above which `block` asks the operator to retype the profile name
# before a destructive batch proceeds. The figure lives in ``cli_shared`` so
# ``cli_blocklist`` can see the same value without a lazy import from this
# module. Ten is the figure specified in plan-v2 Task 6.

# Single fixed name for the synthetic subscription built from ``--all-from``:
# the same value across invocations keeps one cache entry instead of
# accumulating ``one-off-<filename>`` files in the store.
_ONE_OFF_NAME = "one-off"


def _run_block_command(
    *,
    profile: str,
    verb: Any,
    verb_name: str,
    references: list[str],
    rail: bool,
) -> None:
    """Shared body for the block and unblock commands.

    Resolves each reference through ``extract_tidal_id`` and calls the engine
    verb inside ``asyncio.run``. Prints one rich line per id and exits 1 if
    the engine reported any rejected id. ``block`` is destructive at scale:
    when ``rail`` is set and the resolved id list exceeds the ten-id
    threshold, the operator is asked to retype the profile name and a
    mismatched answer aborts before the engine is called. ``unblock`` passes
    ``rail=False`` because the verb is restorative.
    """
    try:
        session = get_session(profile)
        try:
            ids = [extract_tidal_id(reference) for reference in references]
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e

        if rail and len(ids) > BLOCK_RAIL_THRESHOLD:
            typed = typer.prompt(f"Type '{profile}' to confirm blocking {len(ids)} artists")
            if typed != profile:
                console.print("[red]Confirmation did not match. Aborting.[/red]")
                raise typer.Exit(1)

        outcome = asyncio.run(verb(session, ids))
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1) from e
    except TidalSyncError as e:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {e}")
        raise typer.Exit(1) from e

    for item_id in outcome.applied:
        console.print(f"  [green]{verb_name} artist {item_id}[/green]")
    for item_id in outcome.rejected:
        console.print(f"  [red]{verb_name} artist {item_id}[/red]")

    if outcome.rejected:
        raise typer.Exit(1)


def _resolve_block_lists(
    *,
    from_list: str | None,
    all_from: Path | None,
) -> list[Subscription]:
    """Build the subscription list the engine will run.

    ``--from-list`` loads a single stored subscription by name; an
    unknown name exits 1 with a clear message rather than blocking
    nothing. ``--all-from`` synthesises a one-off subscription whose
    source is the given file path and whose format is read off the
    extension; an unsupported extension exits 1 before the engine
    runs. When neither flag is given, returns an empty list so the
    positional path is the only source of ids.
    """
    subs: list[Subscription] = []
    if from_list is not None:
        try:
            found = [s for s in load_subscriptions() if s.name == from_list]
        except StoreError as exc:
            report_store_error(exc)
        if not found:
            console.print(f"[bold red]No such subscription:[/bold red] {from_list}")
            raise typer.Exit(1)
        subs.append(found[0])
    if all_from is not None:
        try:
            fmt = detect_format(str(all_from))
        except FormatError as exc:
            console.print(f"[bold red]{exc}[/bold red]")
            raise typer.Exit(1) from exc
        if not all_from.is_file():
            console.print(f"[bold red]No such file:[/bold red] {all_from}")
            raise typer.Exit(1)
        subs.append(
            Subscription(
                name=_ONE_OFF_NAME,
                source=str(all_from),
                format=fmt,
                last_fetched=None,
            )
        )
    return subs


def _run_block_with_lists(
    *,
    profile: str,
    positional: list[str],
    subs: list[Subscription],
    force: bool,
) -> None:
    """Run ``plan_apply`` on the union of positional and list ids.

    Routes around ``_run_block_command`` so the new flags do not widen
    its signature and risk changing ``unblock``. The rail fires on
    the union length, matching ``blocklist apply``. The list ids
    flow through ``execute_apply`` exactly once; the positional
    leftover is a separate ``block_artists`` call so its
    per-id classification stays on its own print block.
    """
    try:
        session = get_session(profile)
        plan = asyncio.run(plan_apply(session, subs))
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1) from e
    except TidalSyncError as e:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {e}")
        raise typer.Exit(1) from e

    # Drop positional ids already covered by the subscription union.
    list_ids = {tid for tid, _name in plan.to_block}
    list_ids.update(tid for tid, _name in plan.already_blocked)
    leftover = [i for i in positional if i not in list_ids]

    union_count = len(list_ids) + len(leftover)
    if not force and union_count > BLOCK_RAIL_THRESHOLD:
        typed = typer.prompt(f"Type '{profile}' to confirm blocking {union_count} artists")
        if typed != profile:
            console.print("[red]Confirmation did not match. Aborting.[/red]")
            raise typer.Exit(1)

    list_outcome = asyncio.run(execute_apply(session, plan, unblock_ids=[]))

    if list_outcome.capped:
        report_capped(len(plan.to_block))

    if leftover:
        leftover_outcome = asyncio.run(curation.block_artists(session, leftover))
        for item_id in leftover_outcome.applied:
            console.print(f"  [green]Blocked artist {item_id}[/green]")
        if leftover_outcome.rejected:
            for item_id in leftover_outcome.rejected:
                console.print(f"  [red]block failed {item_id}[/red]")
            raise typer.Exit(1)

    if list_outcome.blocked is not None:
        for item_id in list_outcome.blocked.applied:
            console.print(f"  [green]Blocked artist {item_id}[/green]")
        if list_outcome.blocked.rejected:
            for item_id in list_outcome.blocked.rejected:
                console.print(f"  [red]block failed {item_id}[/red]")
            raise typer.Exit(1)

    for tid, _name in plan.already_blocked:
        console.print(f"  [cyan]Already blocked artist {tid}[/cyan]")

    if plan.errors:
        for sub_name, err in plan.errors:
            console.print(f"  [red]error {sub_name}: {err}[/red]")
        raise typer.Exit(1)


@app.command()
def block(
    ids: Annotated[
        list[str] | None,
        typer.Argument(help="One or more artist ids or Tidal share URLs"),
    ] = None,
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to block on")
    ] = "default",
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Skip the confirmation prompt for large batches")
    ] = False,
    from_list: Annotated[
        str | None,
        typer.Option(
            "--from-list",
            help="Block every id in the stored subscription with this name",
        ),
    ] = None,
    all_from: Annotated[
        Path | None,
        typer.Option(
            "--all-from",
            exists=False,
            help="Block every id in a one-off filter-list file (parsed by extension)",
        ),
    ] = None,
) -> None:
    """Blocks one or more artists on the named profile.

    With ``--from-list`` or ``--all-from``, the union of positional and
    list ids is sent through the apply engine; the rail still fires on
    the union length, matching ``blocklist apply``.
    """
    ids = ids or []
    if from_list is None and all_from is None and not ids:
        raise typer.BadParameter("give at least one id, or use --from-list or --all-from")
    if from_list is None and all_from is None:
        _run_block_command(
            profile=profile,
            verb=curation.block_artists,
            verb_name="Blocked",
            references=ids,
            rail=not force,
        )
        return

    subs = _resolve_block_lists(from_list=from_list, all_from=all_from)
    # Resolve positional ids here so the union seen by the rail and the
    # engine is clean.
    try:
        positional = [extract_tidal_id(reference) for reference in ids]
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    _run_block_with_lists(
        profile=profile,
        positional=positional,
        subs=subs,
        force=force,
    )


@app.command()
def unblock(
    ids: Annotated[list[str], typer.Argument(help="One or more artist ids or Tidal share URLs")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to unblock on")
    ] = "default",
) -> None:
    """Unblocks one or more artists on the named profile."""
    _run_block_command(
        profile=profile,
        verb=curation.unblock_artists,
        verb_name="Unblocked",
        references=ids,
        rail=False,
    )


@app.command()
def like(
    kind: Annotated[FavoriteKind, typer.Argument(help="Which kind of favourite to add")],
    ids: Annotated[list[str], typer.Argument(help="One or more ids or Tidal share URLs")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to like into")
    ] = "default",
) -> None:
    """Likes one or more items on the named profile.

    The kind must be one of track, artist, or album; anything else is
    rejected by Typer before any request goes out (clear <target> is the
    same positional-enum idiom).
    """
    _run_favourite_command(
        profile=profile,
        verb_factory=_like_verb,
        verb_name="Liked",
        kind=kind,
        references=ids,
    )


@app.command()
def unlike(
    kind: Annotated[FavoriteKind, typer.Argument(help="Which kind of favourite to remove")],
    ids: Annotated[list[str], typer.Argument(help="One or more ids or Tidal share URLs")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to unlike from")
    ] = "default",
) -> None:
    """Removes one or more items from the favourites on the named profile."""
    _run_favourite_command(
        profile=profile,
        verb_factory=_unlike_verb,
        verb_name="Unliked",
        kind=kind,
        references=ids,
    )


@app.command(name="profiles")
def list_profiles() -> None:
    """
    Displays a list of all authenticated Tidal profiles stored locally.
    """
    profiles = _get_all_profiles()

    if not profiles:
        console.print("[yellow]No profiles found. Use 'tidal-sync login' to authenticate.[/yellow]")
        return

    console.print("\n[bold cyan]🌊 Saved Tidal Profiles:[/bold cyan]")
    for profile_name, user_id in profiles.items():
        console.print(f"  • [bold green]{profile_name}[/bold green] (User ID: {user_id})")
    console.print("")


app.add_typer(blocklist_app, name="blocklist")

if __name__ == "__main__":
    app()
