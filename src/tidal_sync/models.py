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
Data validation schemas for parsing Tidal library CSVs.

This module uses Pydantic to standardise inputs from external platforms
like Exportify or TuneMyMusic. By mapping varying column headers to
consistent field names, it catches malformed data before the Tidal API
ever sees it.

Example:
    >>> from models import TrackRow
    >>> row = TrackRow(**{"Track name": "Helena", "Artist Name(s)": "My Chemical Romance"})
    >>> print(row.search_query)
    'Helena My Chemical Romance'
"""

from pydantic import BaseModel, Field, AliasChoices, ConfigDict


class TrackRow(BaseModel):
    """
    Schema for parsing an individual music track.

    It maps legacy export formats to our required fields and computes
    clean text queries when direct database ID matching fails.

    Attributes:
        track_name (str): The name of the track.
        artist_name (str): The track artist. Can contain multiple artists separated by commas.
        album (str | none): The album name.
        playlist_name (str | none): The destination or source playlist.
        isrc (str | none): The International Standard Recording Code for high-fidelity matching.
        tidal_id (str | none): A direct Tidal database ID.
    """
    model_config = ConfigDict(populate_by_name=True)

    # Aliases handle both Exportify and the TuneMyMusic formats
    track_name: str = Field(validation_alias=AliasChoices("Track name", "Track Name", "track_name"))
    artist_name: str = Field(validation_alias=AliasChoices("Artist name", "Artist Name(s)", "artist_name"))
    album: str | None = Field(default=None, validation_alias=AliasChoices("Album", "album"))
    playlist_name: str | None = Field(default=None, validation_alias=AliasChoices("Playlist name", "playlist_name"))

    # High-fidelity matching fields
    isrc: str | None = Field(default=None, validation_alias=AliasChoices("ISRC", "isrc"))
    tidal_id: str | None = Field(default=None, validation_alias=AliasChoices("Tidal - id", "tidal_id"))

    @property
    def search_query(self) -> str:
        """
        Generate a clean fallback search string.

        Strips out secondary artists to give the Tidal search engine a
        better chance of finding the correct track.

        Returns:
            str: A concatenated string of the track and primary artist.
        """
        primary_artist = self.artist_name.split(", ")[0].strip() if self.artist_name else ""
        return f"{self.track_name} {primary_artist}".strip()


class AlbumRow(BaseModel):
    """
    Schema for parsing an album entry.

    Attributes:
        album_name (str): The name of the album.
        artist_name (str): The primary artist of the album.
        tidal_id (str | none): A direct Tidal database ID.
    """
    model_config = ConfigDict(populate_by_name=True)
    album_name: str = Field(validation_alias=AliasChoices("Album name", "album_name"))
    artist_name: str = Field(validation_alias=AliasChoices("Artist name", "artist_name"))
    tidal_id: str | None = Field(default=None, validation_alias=AliasChoices("Tidal - id", "tidal_id"))

class ArtistRow(BaseModel):
    """
    Schema for parsing a followed artist entry.

    Attributes:
        artist_name (str): The name of the artist.
        tidal_id (str | none): A direct Tidal database ID.
    """
    model_config = ConfigDict(populate_by_name=True)
    artist_name: str = Field(validation_alias=AliasChoices("Artist name", "artist_name"))
    tidal_id: str | None = Field(default=None, validation_alias=AliasChoices("Tidal - id", "tidal_id"))