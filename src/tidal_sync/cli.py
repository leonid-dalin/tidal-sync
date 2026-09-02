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
from rich.console import Console

from .auth import _get_all_profiles, get_session, secure_delete_token
from .domain.enums import ClearTarget, FavoriteKind
from .domain.exceptions import TidalAuthenticationError, TidalSyncError
from .engine import curation
from .engine.exporter import (
    export_algorithmic_mixes_to_disk,
    export_user_favourites_to_disk,
    export_user_playlists_to_disk,
)
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
console = Console()
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
# before a destructive batch proceeds. Ten is the figure specified in
# plan-v2 Task 6; under it the operator sees one rich line per id and nothing
# else.
_BLOCK_RAIL_THRESHOLD = 10


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

        if rail and len(ids) > _BLOCK_RAIL_THRESHOLD:
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


@app.command()
def block(
    ids: Annotated[list[str], typer.Argument(help="One or more artist ids or Tidal share URLs")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to block on")
    ] = "default",
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Skip the confirmation prompt for large batches")
    ] = False,
) -> None:
    """Blocks one or more artists on the named profile."""
    _run_block_command(
        profile=profile,
        verb=curation.block_artists,
        verb_name="Blocked",
        references=ids,
        rail=not force,
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


if __name__ == "__main__":
    app()
