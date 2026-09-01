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
from enum import StrEnum


class ClearTarget(StrEnum):
    """
    Valid targets for the CLI clear command.

    Using a StrEnum provides native Typer validation and terminal autocomplete,
    preventing users from passing unsupported category strings.
    """

    ALL = "all"
    TRACKS = "tracks"
    ALBUMS = "albums"
    ARTISTS = "artists"
    PLAYLISTS = "playlists"


class FavoriteKind(StrEnum):
    """
    Valid kinds for the CLI like and unlike commands.

    Using a StrEnum provides native Typer validation and terminal autocomplete,
    preventing users from passing unsupported category strings.
    """

    TRACK = "track"
    ARTIST = "artist"
    ALBUM = "album"
