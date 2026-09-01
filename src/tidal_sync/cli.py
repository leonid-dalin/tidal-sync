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
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .auth import _get_all_profiles, get_session, secure_delete_token
from .domain.enums import ClearTarget
from .domain.exceptions import TidalAuthenticationError
from .domain.logger import (
    setup_audit_logging,
    setup_global_logging,
    stop_audit_logging,
)
from .engine.exporter import (
    export_algorithmic_mixes_to_disk,
    export_user_favourites_to_disk,
    export_user_playlists_to_disk,
)
from .engine.importer import import_collection_from_disk
from .engine.wiping import purge_target_category_async

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
    session = get_session(profile)
    setup_audit_logging(output_dir / "reports")

    async def run_exports():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(export_user_playlists_to_disk(session, output_dir))
            tg.create_task(export_user_favourites_to_disk(session, output_dir))
            tg.create_task(export_algorithmic_mixes_to_disk(session, output_dir))

    try:
        asyncio.run(run_exports())
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
