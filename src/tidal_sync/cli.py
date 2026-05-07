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

This module sets up the terminal commands using Typer. It routes user inputs
to the core authentication, synchronisation, and clearing functions, while
managing safety prompts for destructive actions.

Example:
    To view available commands in the terminal:
    $ tidal-sync --help
"""

import typer
from pathlib import Path
from rich.console import Console

from .auth import get_session, secure_delete_token
from .sync import import_target, export_playlists, clear_library

app = typer.Typer(help="Modern CLI for managing, importing, exporting, and cloning Tidal libraries.")
console = Console()


@app.command()
def login(profile: str = typer.Option("default", "--profile", "-p", help="Profile name for dual-account management")):
    """
    Authenticate a Tidal account and save it to a local profile.

    Use the `--profile` flag to maintain multiple active logins simultaneously,
    which is necessary for cloning an account.
    """
    get_session(profile)


@app.command()
def logout(profile: str = typer.Option("default", "--profile", "-p", help="Profile name to wipe")):
    """
    Securely delete session credentials for a specific profile.

    Overwrites the local token file with null bytes before deleting it from the disk.
    """
    secure_delete_token(profile)


@app.command(name="import")
def import_data(
        target_path: Path = typer.Argument(..., help="Path to a CSV file OR a directory", exists=True),
        name: str = typer.Option(None, "--name", "-n", help="Target playlist name"),
        profile: str = typer.Option("default", "--profile", "-p", help="Which account profile to import into")
):
    """
    Import CSVs into Tidal.

    If given a directory, it recursively finds and imports all CSVs.
    It safely checks your existing library and skips tracks you already own.
    """
    session = get_session(profile)
    import_target(session, target_path, target_playlist_name=name)


@app.command(name="export")
def export_all(
        output_dir: Path = typer.Option(Path("./exports"), "--out", "-o", help="Output directory"),
        profile: str = typer.Option("default", "--profile", "-p", help="Which account profile to export from")
):
    """
    Download all Tidal playlists and favourites to CSV files.

    Creates a categorised folder structure at the specified output directory.
    """
    session = get_session(profile)
    export_playlists(session, output_dir)


@app.command()
def clear(
        target: str = typer.Argument(..., help="What to clear: 'all', 'tracks', 'albums', 'artists', 'playlists'"),
        profile: str = typer.Option("default", "--profile", "-p", help="Which account profile to clear"),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt")
):
    """
    Destructively wipe data from a Tidal account.

    WARNING: This action is permanent. Unless the `--force` flag is provided,
    the programme will ask for manual confirmation before proceeding.
    """
    valid_targets = ["all", "tracks", "albums", "artists", "playlists"]
    target = target.lower()

    if target not in valid_targets:
        console.print(f"[red]Invalid target. Must be one of: {', '.join(valid_targets)}[/red]")
        raise typer.Exit(1)

    if not force:
        typer.confirm(
            f"Are you absolutely sure you want to permanently delete {target} from the '{profile}' profile?",
            abort=True
        )

    session = get_session(profile)
    clear_library(session, target)


if __name__ == "__main__":
    app()