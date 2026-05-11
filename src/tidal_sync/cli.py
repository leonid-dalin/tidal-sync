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

    Args:
        profile (str): The name for the saved profile. Defaults to 'default'.
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
    Securely logs out and wipes session credentials for a specific profile.

    Args:
        profile (str): The name of the profile to wipe. Defaults to 'default'.
    """
    secure_delete_token(profile)


@app.command(name="import")
def import_data(
    target_path: Annotated[Path, typer.Argument(help="Path to a CSV file OR a directory", exists=True)],
    name: Annotated[str | None, typer.Option("--name", "-n", help="Target playlist name")] = None,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Which account profile to import into")] = "default"
) -> None:
    """
    Ingests CSV metadata and synchronises it with a Tidal account.

    If the target path is a directory, the tool recursively processes all
    contained CSV files. Existing items in the target library are automatically
    skipped to prevent duplicates.

    Args:
        target_path (Path): Path to a CSV file or a directory of CSVs.
        name (str | None): Optional name for the target playlist.
        profile (str): The authentication profile to use for the import.
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
    Backs up the entire Tidal library to local CSV files.

    Generates a categorised folder structure for playlists, liked tracks,
    albums, and followed artists at the specified output path.

    Args:
        output_dir (Path): The directory where the backup will be stored.
        profile (str): The authentication profile to export from.
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
    Destructively wipes a specific category of data from a Tidal account.

    Provides a manual confirmation prompt unless the `--force` flag is used.
    This action is irreversible.

    Args:
        target (ClearTarget): The category to wipe (e.g., 'all', 'tracks').
        profile (str): The authentication profile to clear.
        force (bool): Skips the manual safety confirmation. Defaults to False.
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