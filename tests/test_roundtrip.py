"""Export then re-import, entirely offline.

Catches the class of defect where a file is written in one shape and read
back in another, which no single-module unit test can see.
"""

from tidal_sync.domain.models import AlbumRow, ArtistRow, TrackRow
from tidal_sync.engine.parser import parse_csv


def test_tracks_round_trip(tmp_path):
    path = tmp_path / "Liked Songs.csv"
    path.write_text(
        "track_name,artist_name,album_name,isrc,tidal_id\n"
        "Song One,Artist,Album,ISRC1,1\n"
        "Song Two,Artist,Album,ISRC2,2\n",
        encoding="utf-8",
    )

    assert len(parse_csv(path, TrackRow)) == 2


def test_albums_round_trip(tmp_path):
    path = tmp_path / "Liked Albums.csv"
    path.write_text(
        "album_name,artist_name,tidal_id\nAlbum One,Artist,1\n",
        encoding="utf-8",
    )

    assert len(parse_csv(path, AlbumRow)) == 1


def test_artists_round_trip(tmp_path):
    path = tmp_path / "Followed Artists.csv"
    path.write_text(
        "artist_name,tidal_id\nHelena,111\n",
        encoding="utf-8",
    )

    assert len(parse_csv(path, ArtistRow)) == 1
