"""Contract tests for the tidalapi surfaces the engine depends on.

These do not test tidal-sync. They pin the third-party contract so that a
tidalapi upgrade, or an engine change that drifts from it, fails here
instead of silently losing user data.
"""

import pytest

from tests.fakes import FakeTrack, make_search_results, real_search_results


def test_search_results_are_dicts_not_objects():
    """F40 root cause: Session.search() hands back a dict.

    getattr(result, 'tracks', []) therefore always falls through to the
    default. Any fake modelling search output as an object hides this.
    """
    results = real_search_results(tracks=[FakeTrack(name="Helena")])

    assert isinstance(results, dict)
    assert results["tracks"]
    assert getattr(results, "tracks", []) == []


def test_engine_access_pattern_matches_the_library(monkeypatch):
    """Pins the access the importer must use against a real Session."""
    import tidalapi

    payload = {
        "artists": {"items": []},
        "albums": {"items": []},
        "tracks": {"items": []},
        "videos": {"items": []},
        "playlists": {"items": []},
        "topHit": None,
    }

    class Response:
        ok = True

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr(
        tidalapi.request.Requests,
        "request",
        lambda self, method, path, params=None, data=None, headers=None,
        base_url=None: Response(),
    )

    session = tidalapi.Session()
    results = session.search("isrc:ISRC1")

    assert isinstance(results, dict)
    assert "tracks" in results
    assert getattr(results, "tracks", []) == []


def test_fake_search_results_share_the_real_shape():
    """Guards the fake itself: it must stay a dict."""
    fake = make_search_results(tracks=[FakeTrack()])

    assert isinstance(fake, dict)
    assert set(fake) >= {"tracks", "albums", "artists"}
    assert fake["tracks"][0].name == "Track"


@pytest.mark.parametrize("accessor", ["tracks", "albums"])
def test_subscript_is_the_only_working_access(accessor):
    results = make_search_results(**{accessor: [FakeTrack()]})

    assert results[accessor]
    assert getattr(results, accessor, []) == []
