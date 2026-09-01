"""Playlist uploads must reconcile what Tidal actually accepted.

UserPlaylist.add() sends onArtifactNotFound=SKIP, so Tidal drops
unavailable tracks server-side and answers 200 with a shortened
addedItemIds list. Nothing raises. Ignoring the return value therefore
reports region-locked tracks as successfully added, which is F41.
"""


from tidal_sync.engine.importer import (
    _build_favorites_uploader,
    build_playlist_uploader,
)


class FakePlaylist:
    def __init__(self, accepted):
        self.id = "pl-1"
        self._accepted = accepted
        self.batches = []
        self.refreshes = []

    def add(self, media_ids, allow_duplicates=False):
        self.batches.append(list(media_ids))
        # Tidal answers 200 and skips whatever it will not accept.
        return [tid for tid in media_ids if tid in self._accepted]


async def test_all_accepted_is_a_clean_upload():
    upload = build_playlist_uploader(FakePlaylist({"t1", "t2"}))

    outcome = await upload(["t1", "t2"])

    assert outcome.applied == ["t1", "t2"]
    assert outcome.rejected == []


async def test_rejected_tracks_are_reported_not_counted_as_added():
    """F41: a short addedItemIds list must surface as rejections."""
    upload = build_playlist_uploader(FakePlaylist({"t1", "t3"}))

    outcome = await upload(["t1", "t2", "t3"])

    assert outcome.applied == ["t1", "t3"]
    assert outcome.rejected == ["t2"]


async def test_every_rejected_track_is_named():
    upload = build_playlist_uploader(FakePlaylist(set()))

    outcome = await upload(["t1", "t2"])

    assert outcome.rejected == ["t1", "t2"]


async def test_empty_added_ids_treats_the_whole_batch_as_rejected():
    playlist = FakePlaylist({"t1"})
    playlist.add = lambda media_ids, allow_duplicates=False: []
    upload = build_playlist_uploader(playlist)

    outcome = await upload(["t1"])

    assert outcome.applied == []
    assert outcome.rejected == ["t1"]


async def test_duplicates_are_allowed_so_they_are_not_misread_as_rejections():
    """B3: with onDupes=SKIP a present track vanishes from addedItemIds."""
    playlist = FakePlaylist({"t1", "t2"})

    seen: list[bool] = []

    def add(media_ids, allow_duplicates=False):
        seen.append(allow_duplicates)
        playlist.batches.append(list(media_ids))
        return [t for t in media_ids if t in playlist._accepted]

    playlist.add = add
    upload = build_playlist_uploader(playlist)

    await upload(["t1", "t2"])

    assert playlist.batches[0] == ["t1", "t2"]
    # onDupes=SKIP drops an already-present track from addedItemIds, which
    # would be misread as a refusal on every re-run.
    assert seen == [True]


async def test_uploader_does_not_refetch_the_playlist():
    """F42: UserPlaylist.add() calls _reparse(), which refreshes the ETag."""
    playlist = FakePlaylist({"t1"})

    def refresh():
        playlist.refreshes.append(1)

    playlist.refresh = refresh
    upload = build_playlist_uploader(playlist)

    await upload(["t1"])

    assert playlist.refreshes == []


async def test_favorites_stop_at_the_first_rejection():
    """F11: favorites are added one at a time, so a failure mid-batch means
    everything before it landed and nothing after was attempted.
    """
    added: list[str] = []

    def add_track(tid):
        added.append(tid)
        if tid == "t3":
            raise RuntimeError("region locked")

    favorites = type("F", (), {"add_track": staticmethod(add_track)})()
    upload = _build_favorites_uploader(favorites)

    outcome = await upload(["t1", "t2", "t3", "t4"])

    assert outcome.applied == ["t1", "t2"]
    assert outcome.rejected == ["t3"]
    assert added == ["t1", "t2", "t3"], "t4 is never attempted after t3 fails"
