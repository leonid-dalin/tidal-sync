"""Folder identity resolves on the sanitised name, never the raw one.

Export sanitises a folder name into a directory (e.g. 'AC/DC Mixes' ->
'AC_DC Mixes'). On import that sanitised name is passed to
ensure_v2_folder_exists, which must match it against the raw Tidal name
rather than creating a needless duplicate.
"""

import tidal_sync.engine.folders as folders


async def test_ensure_v2_folder_exists_matches_on_sanitised_name(monkeypatch):
    # The fake account already holds 'AC/DC Mixes' under a raw name.
    existing = [("folder-uuid-123", "AC/DC Mixes")]

    created: list[str] = []

    async def fake_fetch(session):
        return list(existing)

    async def fake_create(session, name):
        created.append(name)
        return "new-id"

    monkeypatch.setattr(folders, "fetch_v2_folders", fake_fetch)
    monkeypatch.setattr(folders, "create_folder", fake_create)

    # Pass the sanitised form that export produced.
    result = await folders.ensure_v2_folder_exists(object(), "AC_DC Mixes")

    assert result == "folder-uuid-123", "the existing folder id is returned"
    assert created == [], "no duplicate folder is created"
