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
Structural types for the tidalapi objects the engine touches.

tidalapi ships no type information, so these describe the narrow surface the
engine actually uses. They are structural rather than nominal: any object
with the right attributes satisfies them.
"""

from typing import Any, Protocol


class Favorites(Protocol):
    """The subset of tidalapi's favourites surface the engine touches.

    Each method declares the narrow ``str`` form the engine passes. The real
    tidalapi signatures are wider (e.g. ``add_track`` accepts ``list[str] | str``);
    the engine fans out per id because a batched call returns one boolean
    and cannot say which id failed.
    """

    # The engine reads these as paginated collection callables (exporter and
    # wiping both hand them to fetch_all_async). They are Any because tidalapi
    # types them loosely and the engine only ever paginates them.
    @property
    def tracks(self) -> Any: ...

    @property
    def albums(self) -> Any: ...

    @property
    def artists(self) -> Any: ...

    def add_track(self, item_id: str) -> bool: ...
    def add_artist(self, item_id: str) -> bool: ...
    def add_album(self, item_id: str) -> bool: ...
    def remove_track(self, item_id: str) -> bool: ...
    def remove_artist(self, item_id: str) -> bool: ...
    def remove_album(self, item_id: str) -> bool: ...


class TidalUser(Protocol):
    """The subset of tidalapi's logged-in user the engine depends on."""

    id: int

    @property
    def favorites(self) -> Favorites: ...

    @property
    def playlists(self) -> Any: ...

    def create_playlist(self, title: str, description: str, parent_id: str = "root") -> Any: ...
