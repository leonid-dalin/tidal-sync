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
Tidal authentication and profile management module.

This module handles Tidal API OAuth authentication, multi-profile session
management, and secure token storage. It acts as the authentication backbone
for the CLI.

Features:
    - Multi-profile session management
    - OAuth token persistence with strict OS file permissions (chmod 600)
    - Account collision detection to prevent duplicate logins
    - Token deletion via logical overwrite (zero-filling)

Example:
    >>> from auth import get_session, secure_delete_token
    >>> session = get_session('main_profile')
    >>> session.check_login()
    True
    >>> secure_delete_token('main_profile')

Attributes:
    CONFIG_DIR (Path): Default directory for storing token files (~/.tidal_sync).
    console (Console): Rich console instance for terminal output.
"""

import json
import os
import re
import secrets
import stat
from datetime import datetime
from pathlib import Path
from typing import cast

import tidalapi
from loguru import logger
from rich.console import Console

from .domain.exceptions import TidalAuthenticationError
from .domain.protocols import TidalUser

console = Console()
CONFIG_DIR = Path.home() / ".tidal_sync"


_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def get_token_path(profile: str) -> Path:
    """
    Resolves the absolute file path for a profile's token file.

    Creates the base configuration directory if it does not already exist.

    The profile name is interpolated straight into a filename and is
    attacker-controllable via --profile, so it is restricted to a safe
    character set. Without this, `--profile ../../tmp/x` escapes the config
    directory and `logout` would then zero-fill an arbitrary user file.

    Args:
        profile (str): The identifier string for the account profile.

    Returns:
        Path: The fully qualified path to the JSON token file.

    Raises:
        TidalAuthenticationError: If the name contains a path separator,
            is empty, or exceeds 64 characters.
    """
    if not _PROFILE_NAME_RE.match(profile):
        raise TidalAuthenticationError(
            f"Invalid profile name {profile!r}. "
            "Use 1-64 characters: letters, digits, '_', '-' or '.'."
        )

    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return CONFIG_DIR / f"{profile}.json"


def _get_all_profiles() -> dict[str, int]:
    """
    Scans the configuration directory and maps profile names to Tidal user IDs.

    This internal utility checks for account collisions during new logins,
    preventing you from authenticating the same account under multiple aliases.

    Returns:
        dict[str, int]: A mapping of profile names to Tidal user IDs. Returns
            an empty dictionary if the config directory is missing or empty.
    """
    profiles: dict[str, int] = {}
    if not CONFIG_DIR.exists():
        return profiles

    for file in CONFIG_DIR.glob("*.json"):
        try:
            with open(file) as f:
                data = json.load(f)
                if "user_id" in data:
                    profiles[file.stem] = data["user_id"]
        except (json.JSONDecodeError, OSError):
            continue

    return profiles


def _save_session_to_disk(session: tidalapi.Session, token_file: Path) -> None:
    """
    Extracts OAuth tokens from an active session and saves them to disk.

    Writes the token data to a JSON file and immediately restricts POSIX
    file permissions (chmod 600) so only the owner can read or write it.

    Args:
        session (tidalapi.Session): The authenticated Tidal session.
        token_file (Path): The destination file path.
    """
    token_data = {
        "token_type": session.token_type,
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
        "user_id": session.user.id,
    }

    # Open with the final mode from the outset. Writing first and chmod-ing
    # afterwards leaves a window where the token is readable by others.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(token_file, flags, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(token_data, f)
        f.flush()
        os.fsync(f.fileno())

    if os.name == "posix":
        os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)


def _check_account_collision(session: tidalapi.Session, profile: str) -> None:
    """Refuses to bind one Tidal account to two profiles.

    A warning was not enough: with two profiles pointing at one account,
    `clear --profile backup` can destroy the account that was exported from.
    """
    user = cast(TidalUser, cast(object, session.user))
    user_id = getattr(user, "id", None)
    if user_id is None:
        return

    collisions = [
        name for name, uid in _get_all_profiles().items() if uid == user_id and name != profile
    ]
    if collisions:
        raise TidalAuthenticationError(
            f"Tidal account {user_id} is already saved as profile(s): "
            f"{', '.join(sorted(collisions))}. "
            "Use a different account, or remove the other profile first."
        )


def get_session(profile: str = "default") -> tidalapi.Session:
    """
    Loads an existing session or prompts the user for a new OAuth login.

    Attempts to load cached credentials for the specified profile. If the
    credentials are valid, it re-saves them to capture potential background
    token refreshes. If they are missing or invalid, it initiates a new
    login flow.

    Args:
        profile (str, optional): The name of the profile to load. Defaults to "default".

    Returns:
        tidalapi.Session: An authenticated Tidal session.

    Raises:
        TidalAuthenticationError: If the API fails to return a valid user object.

    Example:
        >>> session = get_session("main_account")
        >>> tracks = session.user.favorites.tracks()
    """
    session = tidalapi.Session()
    token_file = get_token_path(profile)

    # 1. Attempt to load an existing session
    if token_file.exists():
        try:
            with open(token_file) as f:
                data = json.load(f)
                expiry = (
                    datetime.fromisoformat(data["expiry_time"]) if data.get("expiry_time") else None
                )

            session.load_oauth_session(
                data["token_type"], data["access_token"], data["refresh_token"], expiry
            )

            if session.check_login():
                _save_session_to_disk(session, token_file)
                console.print(f"[green]Authenticated as profile: [bold]{profile}[/bold][/green]")
                return session
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            console.print(
                f"[yellow]Profile '{profile}' invalid or expired. Re-authenticating...[/yellow]"
            )
            logger.debug("Session load failed", error=repr(e))
        except Exception as e:
            console.print(
                f"[yellow]Could not reach Tidal for profile '{profile}'. "
                "Re-authenticating...[/yellow]"
            )
            logger.debug("Session verification failed", error=repr(e))

    # 2. Initiate a new OAuth login flow
    console.print(f"[cyan]Logging in to profile: [bold]{profile}[/bold][/cyan]")
    session.login_oauth_simple()

    user = cast(TidalUser, cast(object, session.user))
    if not user or getattr(user, "id", None) is None:
        raise TidalAuthenticationError(
            "[red]Critical Error: Tidal API did not return a valid user object.[/red]"
        )

    _check_account_collision(session, profile)
    _save_session_to_disk(session, token_file)
    console.print(f"[green]Successfully saved profile '{profile}'![/green]")
    return session


def secure_delete_token(profile: str = "default") -> bool:
    """
    Securely clears and removes the session token file for a profile.

    Performs a logical zero-fill overwrite of the token file on the disk
    before deletion to mitigate data recovery risks and prevent standard
    forensic restoration.

    Returns True only when overwrite, verification and unlink all succeed.
    A failure leaves the file in place: deleting without overwriting defeats
    the purpose, and keeping it lets the user retry.

    Args:
        profile (str, optional): The name of the profile to wipe. Defaults to "default".
    """
    token_file = get_token_path(profile)

    if not token_file.exists():
        console.print(f"[yellow]Profile '{profile}' does not exist.[/yellow]")
        return False

    try:
        file_size = token_file.stat().st_size

        with open(token_file, "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * file_size)
            f.flush()
            os.fsync(f.fileno())

            f.seek(0)
            if f.read() != b"\x00" * file_size:
                console.print("[red]Verification failed: Logical overwrite incomplete.[/red]")
                console.print("[yellow]Token left in place. Retry, or delete it manually.[/yellow]")
                return False

        # Renaming first removes the profile name from the filesystem metadata.
        temp_name = token_file.with_name(secrets.token_hex(8) + ".tmp")
        token_file.rename(temp_name)
        temp_name.unlink()

        console.print(f"[green]Profile '{profile}' cleared and verified.[/green]")
        return True

    except OSError as e:
        console.print(f"[red]Secure delete failed: {e}[/red]")
        console.print(
            "[yellow]Token left in place and NOT overwritten. "
            "Retry, or delete it manually.[/yellow]"
        )
        return False
