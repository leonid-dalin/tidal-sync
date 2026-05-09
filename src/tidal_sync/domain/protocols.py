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
from typing import Protocol, Any

CHUNK_SIZE = 50  # Array chunking for large playlists to prevent HTTP 413 Payload Too Large


class TidalArtist(Protocol):
    """Defines the structure of a Tidal artist object."""
    id: int
    name: str

class TidalAlbum(Protocol):
    """Defines the structure of a Tidal album object."""
    id: int
    name: str
    artist: TidalArtist | None

class TidalTrack(Protocol):
    """Defines the structure of a Tidal track object."""
    id: int
    name: str
    artist: TidalArtist | None
    album: TidalAlbum | None
    isrc: str | None

class TidalPlaylist(Protocol):
    """
    Defines the structure of a Tidal playlist and its operations.
    """
    id: str
    name: str
    def tracks(self, limit: int | None = None, offset: int = 0, **kwargs: Any) -> list[TidalTrack]:
        """Fetches tracks belonging to this playlist."""
        ...
    def add(self, media_ids: list[str], **kwargs: Any) -> list[int]:
        """Adds tracks to the playlist using their media IDs."""
        ...
    def delete(self) -> bool:
        """Deletes the playlist from the user's account."""
        ...

class TidalFavorites(Protocol):
    """
    Defines the interface for managing a user's liked content.
    Includes methods for fetching and modifying tracks, albums, and artists.
    """
    def tracks(self, limit: int = CHUNK_SIZE, offset: int = 0, **kwargs: Any) -> list[TidalTrack]: ...
    def albums(self, limit: int = CHUNK_SIZE, offset: int = 0, **kwargs: Any) -> list[TidalAlbum]: ...
    def artists(self, limit: int = CHUNK_SIZE, offset: int = 0, **kwargs: Any) -> list[TidalArtist]: ...
    def add_track(self, track_id: list[str] | str) -> bool: ...
    def remove_track(self, track_id: str) -> bool: ...
    def add_album(self, album_id: list[str] | str) -> bool: ...
    def remove_album(self, album_id: str) -> bool: ...
    def add_artist(self, artist_id: list[str] | str) -> bool: ...
    def remove_artist(self, artist_id: str) -> bool: ...

class TidalUser(Protocol):
    """Defines a Tidal user session and their top-level library operations."""
    id: int
    favorites: TidalFavorites
    def playlists(self) -> list[TidalPlaylist]:
        """Fetches all playlists created or saved by the user."""
        ...
    def create_playlist(self, title: str, description: str, **kwargs: Any) -> TidalPlaylist:
        """Creates a new empty playlist on the user's account."""
        ...