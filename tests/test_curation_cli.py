"""CLI wiring for like and unlike commands.

Engine tests in test_curation.py prove the verbs return the right
UploadOutcome; these tests prove the CLI prints one rich line per id,
exits 1 when any id failed, lets Typer exit 2 on an unknown kind (so an
operator who types 'like playlist 1' is told the kind is wrong rather
than handed an unknown-command error), and forwards --profile to the
session factory.
"""

from typer.testing import CliRunner

from tidal_sync import cli as cli_module
from tidal_sync.cli import app
from tidal_sync.domain.results import UploadOutcome

runner = CliRunner()


class FakeUser:
    id = 4242


def _session():
    return type("S", (), {"user": FakeUser()})()


def test_like_track_prints_one_line_per_id_and_exits_zero(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    def _get_session(profile="default"):
        calls.append(("session", [profile]))
        return _session()

    async def _like_tracks(session, ids):
        calls.append(("like_tracks", list(ids)))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "like_tracks", _like_tracks)

    result = runner.invoke(app, ["like", "track", "1", "2", "3"])

    assert result.exit_code == 0, result.output
    assert ("like_tracks", ["1", "2", "3"]) in calls
    assert "1" in result.output and "2" in result.output and "3" in result.output


def test_like_track_exits_one_when_any_id_is_rejected(monkeypatch):
    def _get_session(profile="default"):
        return _session()

    async def _like_tracks(session, ids):
        return UploadOutcome(applied=["1", "3"], rejected=["2"])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "like_tracks", _like_tracks)

    result = runner.invoke(app, ["like", "track", "1", "2", "3"])

    assert result.exit_code == 1, result.output


def test_invalid_kind_is_a_usage_error_exits_two(monkeypatch):
    """The plan's positional <target> idiom uses FavoriteKind for Typer
    validation, so 'like playlist 1' is rejected before the engine runs.
    Without the enum, an unknown kind would surface as an unknown-command
    error rather than the specific invalid-kind error this pins.
    """

    def _get_session(profile="default"):
        return _session()

    engine_called = False

    async def _like_tracks(session, ids):
        nonlocal engine_called
        engine_called = True
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "like_tracks", _like_tracks)

    result = runner.invoke(app, ["like", "playlist", "1"])

    assert result.exit_code == 2, result.output
    assert engine_called is False, "the engine must not run when the kind is invalid"
    assert "like" in result.output, (
        "the failure message must identify the 'like' command so an unknown-command "
        "error cannot silently satisfy this case"
    )
    assert "Invalid value" in result.output or "playlist" in result.output, (
        "the error must name the bad kind (Typer's invalid-enum message) rather "
        "than a generic 'no such command' message"
    )


def test_profile_is_forwarded_to_get_session(monkeypatch):
    """docs/cli-reference.md makes --profile a contract: every account
    command must thread it through to the session factory.
    """
    captured: list[str] = []

    def _get_session(profile="default"):
        captured.append(profile)
        return _session()

    async def _like_tracks(session, ids):
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "like_tracks", _like_tracks)

    result = runner.invoke(app, ["like", "track", "1", "--profile", "acc-b"])

    assert result.exit_code == 0, result.output
    assert captured == ["acc-b"]


def test_like_resolves_url_references_through_extract_tidal_id(monkeypatch):
    """Operators paste share URLs as often as bare ids, so the CLI must run
    each reference through extract_tidal_id before the engine sees it.
    """
    received: list[list[str]] = []

    def _get_session(profile="default"):
        return _session()

    async def _like_tracks(session, ids):
        received.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "like_tracks", _like_tracks)

    result = runner.invoke(
        app,
        [
            "like",
            "track",
            "https://listen.tidal.com/track/12345",
            "67890",
        ],
    )

    assert result.exit_code == 0, result.output
    assert received == [["12345", "67890"]]


def test_like_rejects_an_unparseable_reference(monkeypatch):
    """extract_tidal_id raises ValueError; the CLI surfaces it as Click
    exit 2 rather than silently dropping the reference.
    """

    def _get_session(profile="default"):
        return _session()

    monkeypatch.setattr(cli_module, "get_session", _get_session)

    result = runner.invoke(app, ["like", "track", "not-a-tidal-thing"])

    assert result.exit_code == 2, result.output


def test_unlike_artist_exits_one_when_any_id_is_rejected(monkeypatch):
    def _get_session(profile="default"):
        return _session()

    async def _unlike_artists(session, ids):
        return UploadOutcome(applied=[], rejected=list(ids))

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "unlike_artists", _unlike_artists)

    result = runner.invoke(app, ["unlike", "artist", "9", "10"])

    assert result.exit_code == 1, result.output


def test_unlike_album_forwards_profile(monkeypatch):
    captured: list[str] = []

    def _get_session(profile="default"):
        captured.append(profile)
        return _session()

    async def _unlike_albums(session, ids):
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "unlike_albums", _unlike_albums)

    result = runner.invoke(app, ["unlike", "album", "11", "-p", "alt"])

    assert result.exit_code == 0, result.output
    assert captured == ["alt"]


def test_like_dispatches_by_kind_to_the_engine_verb(monkeypatch):
    """FavoriteKind is real dispatch: 'like artist N' routes to
    like_artists, not like_tracks. The shared body receives the verb
    chosen by the enum.
    """
    seen: list[tuple[str, list[str]]] = []

    def _get_session(profile="default"):
        return _session()

    async def _like_tracks(session, ids):
        seen.append(("like_tracks", list(ids)))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _like_artists(session, ids):
        seen.append(("like_artists", list(ids)))
        return UploadOutcome(applied=list(ids), rejected=[])

    async def _like_albums(session, ids):
        seen.append(("like_albums", list(ids)))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "like_tracks", _like_tracks)
    monkeypatch.setattr(cli_module.curation, "like_artists", _like_artists)
    monkeypatch.setattr(cli_module.curation, "like_albums", _like_albums)

    for kind in ["track", "artist", "album"]:
        result = runner.invoke(app, ["like", kind, "1"])
        assert result.exit_code == 0, (kind, result.output)

    assert seen == [
        ("like_tracks", ["1"]),
        ("like_artists", ["1"]),
        ("like_albums", ["1"]),
    ]


# ---------------------------------------------------------------------------
# block and unblock: the ten-id confirmation rail.
#
# Block is destructive at scale, so over ten ids without --force prompts the
# operator to type the profile name; on a mismatched answer the run aborts
# before the engine is called. Under ten ids, with --force, or in unblock, the
# rail is skipped.
# ---------------------------------------------------------------------------


def test_block_under_ten_ids_skips_the_rail_and_calls_engine(monkeypatch):
    """Block under ten ids is small enough to skip the confirmation rail.

    The engine still runs and prints one rich line per id.
    """
    engine_calls: list[list[str]] = []

    def _get_session(profile="default"):
        return _session()

    async def _block_artists(session, ids):
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    ids = [str(i) for i in range(1, 10)]
    result = runner.invoke(app, ["block", *ids])

    assert result.exit_code == 0, result.output
    assert engine_calls == [ids]
    for item_id in ids:
        assert item_id in result.output


def test_block_over_ten_ids_prompts_and_aborts_on_mismatch_without_engine_call(monkeypatch):
    """Over ten ids without --force prompts; a wrong answer aborts and the
    engine verb is never called. Exit is non-zero.

    Asserts the prompt text appears so a "no such command" exit 2 cannot
    silently satisfy this case.
    """
    engine_calls: list[list[str]] = []

    def _get_session(profile="default"):
        return _session()

    async def _block_artists(session, ids):
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    ids = [str(i) for i in range(1, 12)]
    result = runner.invoke(app, ["block", *ids], input="wrong\n")

    assert "Type 'default' to confirm" in result.output, result.output
    assert engine_calls == [], "engine must not run when the confirmation answer does not match"
    assert result.exit_code != 0, result.output


def test_block_over_ten_ids_with_force_skips_the_prompt(monkeypatch):
    """--force bypasses the rail even when over ten ids, and the engine runs."""
    engine_calls: list[list[str]] = []

    def _get_session(profile="default"):
        return _session()

    async def _block_artists(session, ids):
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    ids = [str(i) for i in range(1, 12)]
    result = runner.invoke(app, ["block", *ids, "--force"])

    assert result.exit_code == 0, result.output
    assert engine_calls == [ids]


def test_unblock_never_prompts_regardless_of_id_count(monkeypatch):
    """Unblock is restorative and carries no rail at any scale."""
    engine_calls: list[list[str]] = []

    def _get_session(profile="default"):
        return _session()

    async def _unblock_artists(session, ids):
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "unblock_artists", _unblock_artists)

    ids = [str(i) for i in range(1, 12)]
    result = runner.invoke(app, ["unblock", *ids])

    assert result.exit_code == 0, result.output
    assert engine_calls == [ids]


def test_block_over_ten_ids_abort_exits_nonzero_with_no_engine_call(monkeypatch):
    """Pinning the abort path's exit code and zero-call guarantee, and that
    the prompt was issued (not a usage error)."""
    engine_calls: list[list[str]] = []

    def _get_session(profile="default"):
        return _session()

    async def _block_artists(session, ids):
        engine_calls.append(list(ids))
        return UploadOutcome(applied=list(ids), rejected=[])

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module.curation, "block_artists", _block_artists)

    ids = [str(i) for i in range(1, 12)]
    result = runner.invoke(app, ["block", *ids], input="wrong\n")

    assert "Type 'default' to confirm" in result.output, result.output
    assert result.exit_code == 1, result.output
    assert engine_calls == []
