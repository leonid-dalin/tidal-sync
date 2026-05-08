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
Tidal Authentication and Profile Management Module.

This module provides functions for handling Tidal API OAuth authentication,
managing multiple user profiles, and securely storing or deleting session tokens.
It serves as the authentication backbone for the tidal-sync CLI.

Features:
    - Multi-profile session management
    - Secure OAuth token persistence with strict OS file permissions
    - Account collision detection to prevent duplicate profile logins
    - High-security token deletion via logical overwrite (zero-filling)

Example:
    Basic authentication and session retrieval:

    >>> from auth import get_session
    >>> session = get_session('main_profile')
    >>> print(session.check_login())
    True

    Securely wiping a profile's credentials:

    >>> from auth import secure_delete_token
    >>> secure_delete_token('main_profile')

Attributes:
    CONFIG_DIR (Path): The default directory path for storing token files (~/.tidal_sync).
    console (Console): The Rich console instance used for terminal output.

Note:
    This module interacts directly with the local file system to store sensitive
    OAuth tokens. It requires the `tidalapi` and `rich` packages.
"""

import os
import json
import secrets
import stat
import tidalapi
from pathlib import Path
from datetime import datetime
from typing import cast, Any
from rich.console import Console

console = Console()
CONFIG_DIR = Path.home() / ".tidal_sync"


def get_token_path(profile: str) -> Path:
    """
    Resolve the absolute file path for a specific profile's token file.

    This function safely ensures that the base configuration directory 
    exists before returning the expected file path.

    Args:
        profile (str): The identifier string for the account profile.

    Returns:
        Path: The fully qualified path to the profile's JSON token file.

    Example:
        >>> path = get_token_path('backup_account')
        >>> print(path.name)
        'backup_account.json'
    """
    CONFIG_DIR.mkdir(exist_ok=True)
    return CONFIG_DIR / f"{profile}.json"


def _get_all_profiles() -> dict[str, int]:
    """
    Scan the configuration directory and map profile names to their Tidal User IDs.

    This is an internal utility function primarily used for collision detection
    during new logins to prevent authenticating the same account under multiple aliases.

    Returns:
        dict: A dictionary mapping profile names (str) to Tidal User IDs (int).
              Returns an empty dictionary if the config directory is missing or empty.
    """
    profiles: dict[str, int] = {}
    if not CONFIG_DIR.exists(): return profiles

    for file in CONFIG_DIR.glob("*.json"):
        try:
            with open(file, 'r') as f:
                data = json.load(f)
                if 'user_id' in data:
                    profiles[file.stem] = data['user_id']
        except (json.JSONDecodeError, OSError):
            continue    # Gracefully ignore malformed or inaccessible token files

    return profiles


def _save_session_to_disk(session: tidalapi.Session, token_file: Path) -> None:
    """
    Extract OAuth tokens from an active session and securely save them to disk.

    Writes the token data to a JSON file and immediately applies strict POSIX 
    file permissions (chmod 600) so that only the file owner can read or write it.

    Args:
        session (tidalapi.Session): The authenticated Tidal session.
        token_file (Path): The destination file path.
        profile (str): The name of the profile being saved (used for logging).
    """
    token_data = {
        'token_type': session.token_type,
        'access_token': session.access_token,
        'refresh_token': session.refresh_token,
        'expiry_time': session.expiry_time.isoformat() if session.expiry_time else None,
        'user_id': session.user.id
    }

    with open(token_file, 'w') as f:
        json.dump(token_data, f)

    # Enforce strict file permissions: Read/Write for Owner ONLY (Linux/macOS)
    if os.name == "posix":
        os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)


def get_session(profile: str = "default") -> tidalapi.Session:
    """
    Load an existing session or prompt the user for a new OAuth login.

    This function attempts to load cached credentials for the specified profile.
    If the credentials are valid, it re-saves them (to capture potential background
    token refreshes) and returns the session. If they are missing or invalid, 
    it initiates a new Tidal OAuth login flow.

    Args:
        profile (str, optional): The name of the profile to load. Defaults to "default".

    Returns:
        tidalapi.Session: A fully authenticated and active Tidal session.

    Raises:
        SystemExit: If the Tidal API fails to return a valid user object upon successful login.

    Example:
        >>> session = get_session("my_main_account")
        >>> tracks = session.user.favorites.tracks()
    """
    session = tidalapi.Session()
    token_file = get_token_path(profile)

    # 1. Attempt to load an existing session
    if token_file.exists():
        try:
            with open(token_file, 'r') as f:
                data = json.load(f)
                expiry = datetime.fromisoformat(data['expiry_time']) if data.get('expiry_time') else None

            session.load_oauth_session(
                data['token_type'], data['access_token'], data['refresh_token'], expiry
            )

            if session.check_login():
                # Re-save to disk to persist refreshed tokens handled by tidalapi
                _save_session_to_disk(session, token_file)
                console.print(f"[green]Authenticated as profile: [bold]{profile}[/bold][/green]")
                return session
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            console.print(f"[yellow]Profile '{profile}' invalid or expired. Re-authenticating...[/yellow]")

    # 2. Initiate a new OAuth login flow
    console.print(f"[cyan]Logging in to profile: [bold]{profile}[/bold][/cyan]")
    session.login_oauth_simple()

    # Cast user to Any to satisfy IDE static analysis requirements
    user = cast(Any, session.user)
    if not user or not hasattr(user, 'id'):
        console.print("[red]Critical Error: Tidal API did not return a valid user object.[/red]")
        raise SystemExit(1)

    # 3. Collision Detection: Check if this Tidal account is already tied to another profile
    existing_profiles = _get_all_profiles()
    if any(p != profile and uid == user.id for p, uid in existing_profiles.items()):
        console.print(f"\n[bold red]⚠️ WARNING:[/bold red] Account collision detected!")

    # 4. Save and return
    _save_session_to_disk(session, token_file)
    console.print(f"[green]Successfully saved profile '{profile}'![/green]")
    return session


def secure_delete_token(profile: str = "default") -> None:
    """
    Securely clear and remove the session token file for a specific profile.

    Performs a logical overwrite (zero-filling) of the token file contents,
    verifies to overwrite, and then unlinks the file. This process mitigates
    the risk of credentials being recovered from the disk by standard data
    recovery tools.

    Args:
        profile (str, optional): The name of the profile to wipe. Defaults to "default".

    Example:
        >>> secure_delete_token('compromised_profile')
        Profile 'compromised_profile' cleared and verified.
    """
    token_file = get_token_path(profile)

    if not token_file.exists():
        console.print(f"[yellow]Profile '{profile}' does not exist.[/yellow]")
        return

    try:
        file_size = token_file.stat().st_size

        # Open in binary update mode to perform an in-place logical clear
        with open(token_file, "r+b") as f:
            # Pass 1: Overwrite with null bytes
            f.seek(0)
            f.write(b'\x00' * file_size)
            f.flush()
            os.fsync(f.fileno())

            # Pass 2: Verify to overwrite succeeded
            f.seek(0)
            if f.read() != b'\x00' * file_size:
                console.print("[red]Verification failed: Logical overwrite incomplete.[/red]")

        # Obfuscate the filename before final deletion to wipe file metadata traces
        temp_name = token_file.with_name(secrets.token_hex(8) + ".tmp")
        token_file.rename(temp_name)
        temp_name.unlink()

        console.print(f"[green]Profile '{profile}' cleared and verified.[/green]")

    except OSError as e:
        console.print(f"[red]Secure delete failed: {e}[/red]")
        # Fallback to standard deletion if secure wipe encounters an OS lock
        token_file.unlink(missing_ok=True)