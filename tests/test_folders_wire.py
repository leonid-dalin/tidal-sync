"""V2 folder endpoints, asserted exactly.

These tests pin the wire: verb, path, params, and the empty body Tidal's
firewall requires. A change to any of them against a real account would be
silent, so the contract lives here instead.
"""

from tidal_sync.engine import folders

_PLAYLIST_TRN = "trn:playlist:12345"


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True

    def json(self):
        return self._payload


class RecordingSession:
    """Captures every V2 call and returns queued payloads.

    The session records (method, path, params, data) for each call so a
    test can assert the exact request that was sent. api_v2_location and
    country_code mirror the real session shape the code reads. The request
    callable is synchronous: execute_network runs it inside a worker thread.
    """

    class _Config:
        api_v2_location = "https://api.tidal.com/v2"

    config = _Config()
    country_code = "GB"

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls: list[tuple] = []

    @property
    def request(self):
        session = self

        class _Req:
            def request(self, method, path, params=None, data=None, base_url=None):
                session.calls.append((method, path, dict(params or {}), data, base_url))
                return _Resp(session._pages.pop(0))

        return _Req()


def _folder_page(folder_id, name, uuid=None, folder_trn=None, playlist_uuids=None):
    return {
        "items": [
            {
                "data": {
                    "id": folder_id,
                    "uuid": uuid or folder_id,
                    "name": name,
                    "title": name,
                },
                "id": folder_id,
                "uuid": uuid or folder_id,
            }
        ]
    }


def _full_folder_page(prefix, count):
    return {
        "items": [
            {
                "data": {"id": f"{prefix}{i}", "uuid": f"{prefix}{i}", "name": f"{prefix}{i}"},
                "id": f"{prefix}{i}",
                "uuid": f"{prefix}{i}",
            }
            for i in range(count)
        ]
    }


def _playlist_page(playlist_uuids):
    return {
        "items": [
            {"data": {"uuid": pid, "id": pid}, "id": pid, "uuid": pid} for pid in playlist_uuids
        ]
    }


async def test_fetch_v2_folders_returns_short_page_in_one_call():
    session = RecordingSession([_folder_page("f1", "Rock")])

    folders_found = await folders.fetch_v2_folders(session)

    assert folders_found == [("f1", "Rock")]
    assert len(session.calls) == 1


async def test_fetch_v2_folders_paginates_full_pages():
    session = RecordingSession([_full_folder_page("a", 50), _folder_page("b1", "Jazz")])

    folders_found = await folders.fetch_v2_folders(session)

    assert ("a0", "a0") in folders_found
    assert ("b1", "Jazz") in folders_found
    assert len(session.calls) == 2


async def test_fetch_v2_folders_stops_on_duplicate_full_page():
    page = _full_folder_page("a", 50)
    session = RecordingSession([page, page])

    folders_found = await folders.fetch_v2_folders(session)

    assert len(folders_found) == 50
    assert len(session.calls) == 2


async def test_fetch_v2_folders_skips_entries_with_no_name():
    session = RecordingSession(
        [
            {
                "items": [
                    {"data": {"uuid": "f1", "name": "Rock"}},  # missing id, kept via uuid
                    {"data": {"id": "f2", "uuid": "f2"}},  # missing name, dropped
                    {"data": {"id": "f3", "uuid": "f3", "name": "Pop"}},
                ]
            }
        ]
    )

    folders_found = await folders.fetch_v2_folders(session)

    assert folders_found == [("f1", "Rock"), ("f3", "Pop")]


async def test_create_folder_reproduces_web_player_call():
    session = RecordingSession([{"data": {"uuid": "new-id", "id": "new-id"}}])

    result = await folders.create_folder(session, "My Folder")

    assert result == "new-id"
    method, path, params, data, base_url = session.calls[0]
    assert method == "PUT"
    assert path == "my-collection/playlists/folders/create-folder"
    assert params == {
        "deviceType": "BROWSER",
        "locale": "en_US",
        "countryCode": "GB",
        "folderId": "root",
        "name": "My Folder",
        "trns": "",
    }
    assert data is None
    assert base_url == "https://api.tidal.com/v2"


async def test_assign_playlist_prefixes_trn_once_and_sends_empty_body():
    session = RecordingSession([{"data": {"uuid": "p1", "id": "p1"}}])

    assert await folders.assign_playlist(session, "12345", "f1") is True

    method, path, params, data, _ = session.calls[0]
    assert method == "PUT"
    assert path == "my-collection/playlists/folders/move"
    assert params["trns"] == _PLAYLIST_TRN
    assert params["folderId"] == "f1"
    assert data == b""


async def test_remove_folder_uses_folder_trn_and_empty_body():
    session = RecordingSession([{"data": {"uuid": "f1", "id": "f1"}}])

    assert await folders.remove_folder(session, "f1") is True

    method, path, params, data, _ = session.calls[0]
    assert method == "PUT"
    assert path == "my-collection/playlists/folders/remove"
    assert params["trns"] == "trn:folder:f1"
    assert data == b""


async def test_create_folder_returns_none_on_failure():
    class FailingSession:
        class _Config:
            api_v2_location = "https://api.tidal.com/v2"

        config = _Config()
        country_code = "GB"

        @property
        def request(self):
            class _Req:
                def request(self, *a, **k):
                    raise RuntimeError("boom")

            return _Req()

    assert await folders.create_folder(FailingSession(), "x") is None


async def test_build_playlist_folder_map_normalises_uuids():
    session = RecordingSession([_folder_page("f1", "Rock"), _playlist_page(["UUID-1", "UUID-2"])])

    mapping = await folders.build_playlist_folder_map(session)

    assert mapping == {"uuid-1": "Rock", "uuid-2": "Rock"}
    assert all(k == k.lower() for k in mapping)
