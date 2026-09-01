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
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class TrackRow(BaseModel):
    """
    Parses an individual music track.

    This model maps legacy export formats (like Exportify or TuneMyMusic) to
    our required fields. It also computes clean text queries when direct
    database ID matching fails.

    Attributes:
        track_name (str): The name of the track.
        artist_name (str): The track artist. Multiple artists are comma-separated.
        album (str | None): The album name.
        playlist_name (str | None): The destination or source playlist.
        isrc (str | None): The International Standard Recording Code for high-fidelity matching.
        tidal_id (str | None): A direct Tidal database ID.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The parser folds every header to snake_case, so one canonical alias
    # per field covers Exportify, TuneMyMusic and this tool's own export.
    track_name: str = Field(
        validation_alias=AliasChoices(
            "Track name", "Track Name", "track name", "track_name", "title"
        )
    )
    artist_name: str = Field(
        validation_alias=AliasChoices("Artist name", "Artist Name(s)", "artist name", "artist_name")
    )
    album: str | None = Field(
        default=None,
        validation_alias=AliasChoices("Album", "album", "album name", "album_name"),
    )
    playlist_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("Playlist name", "playlist name", "playlist_name"),
    )

    # High-fidelity matching fields
    isrc: str | None = Field(default=None, validation_alias=AliasChoices("ISRC", "isrc"))
    tidal_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("Tidal - id", "tidal id", "tidal_id", "id"),
    )


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
    tidal_id: str | None = Field(
        default=None, validation_alias=AliasChoices("Tidal - id", "tidal_id")
    )


class ArtistRow(BaseModel):
    """
    Schema for parsing a followed artist entry.

    Attributes:
        artist_name (str): The name of the artist.
        tidal_id (str | none): A direct Tidal database ID.
    """

    model_config = ConfigDict(populate_by_name=True)
    artist_name: str = Field(validation_alias=AliasChoices("Artist name", "artist_name"))
    tidal_id: str | None = Field(
        default=None, validation_alias=AliasChoices("Tidal - id", "tidal_id")
    )
