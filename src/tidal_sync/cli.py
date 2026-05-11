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
inputs to the core authentication, synchronisation, and clearing functions,
and manages safety prompts for destructive actions.

Example:
    To view available commands in the terminal:
    $ tidal-sync --help
"""

import asyncio
import typer
from pathlib import Path
from typing import Annotated
from rich.console import Console
from loguru import logger

from .domain.logger import setup_global_logging
from .domain.enums import ClearTarget
from .domain.exceptions import TidalAuthenticationError
from .auth import get_session, secure_delete_token, _get_all_profiles
from .sync import import_target_async, export_playlists_async, clear_library_async


app = typer.Typer(help="Modern CLI for managing, importing, exporting, and cloning Tidal libraries.")
console = Console()
setup_global_logging()


@app.command()
def login(
    profile: Annotated[str, typer.Option("--profile", "-p", help="Profile name for dual-account management")] = "default"
) -> None:
    """
    Authenticates a Tidal account and saves it to a local profile.

    Use the `--profile` flag to keep multiple active logins simultaneously.
    This is necessary if you want to clone an account.
    """
    try:
        get_session(profile)
    except TidalAuthenticationError as e:
            console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
            raise typer.Exit(1)

@app.command()
def logout(
    profile: Annotated[str, typer.Option("--profile", "-p", help="Profile name to wipe")] = "default"
) -> None:
    """
    Securely deletes session credentials for a specific profile.

    Overwrites the local token file with null bytes before deleting it
    from the disk.
    """
    secure_delete_token(profile)


@app.command(name="import")
def import_data(
    target_path: Annotated[Path, typer.Argument(help="Path to a CSV file OR a directory", exists=True)],
    name: Annotated[str | None, typer.Option("--name", "-n", help="Target playlist name")] = None,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Which account profile to import into")] = "default"
) -> None:
    """
    Imports CSVs into Tidal.

    If you provide a directory, it recursively finds and imports all CSV files.
    It checks your existing library and automatically skips tracks you already own.
    """
    try:
        session = get_session(profile)
        asyncio.run(import_target_async(session, target_path, target_playlist_name=name))
    finally:
        logger.remove()  # Safely flushes the enqueue=True background threads before the CLI exits

@app.command(name="export")
def export_all(
    output_dir: Annotated[Path, typer.Option("--out", "-o", help="Output directory")] = Path("./exports"),
    profile: Annotated[str, typer.Option("--profile", "-p", help="Which account profile to export from")] = "default"
) -> None:
    """
    Downloads all Tidal playlists and favourites to CSV files.

    Builds a categorised folder structure at the specified output directory.
    """
    session = get_session(profile)
    asyncio.run(export_playlists_async(session, output_dir))


@app.command()
def clear(
    target: Annotated[ClearTarget, typer.Argument(help="What to clear")],
    profile: Annotated[str, typer.Option("--profile", "-p", help="Which account profile to clear")] = "default",
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation prompt")] = False
) -> None:
    """
    Destructively wipes data from a Tidal account.

    WARNING: This action is permanent. Unless you provide the `--force` flag,
    the tool will ask for manual confirmation before proceeding.
    """
    if not force:
        typer.confirm(
            f"Are you absolutely sure you want to permanently delete {target} from the '{profile}' profile?",
            abort=True
        )

    try:
        session = get_session(profile)
        asyncio.run(clear_library_async(session, target))
    except TidalAuthenticationError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(1)


@app.command(name="profiles")
def list_profiles() -> None:
    """
    Lists all authenticated Tidal profiles saved on this machine
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