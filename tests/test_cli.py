"""End-to-end CLI behaviour through typer's runner.

Unit tests reach the engine functions directly, so they cannot see the
confirmation ordering, whether the account id is shown, or whether a
mismatched confirmation leaves the account untouched.
"""

import pytest
from typer.testing import CliRunner

from tidal_sync import cli as cli_module
from tidal_sync.cli import app
from tidal_sync.domain.exceptions import BackupFileError

runner = CliRunner()


class FakeUser:
    id = 4242


@pytest.fixture
def fake_session(monkeypatch):
    """Replaces authentication so a test never touches the network."""

    calls: list[str] = []

    def _get_session(profile="default"):
        calls.append(profile)
        return type("S", (), {"user": FakeUser()})()

    async def _purge(session, target, dry_run=False):
        calls.append(f"purge:{target}:dry_run={dry_run}")
        return type("R", (), {"requested": 2, "deleted": 2, "failed": 0, "failures": []})()

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module, "purge_target_category_async", _purge)
    return calls


def test_mismatched_confirmation_never_purges(fake_session):
    result = runner.invoke(app, ["clear", "tracks"], input="wrong\n")

    assert "Confirmation did not match" in result.output
    assert not any(str(c).startswith("purge:") for c in fake_session)


def test_matching_confirmation_purges(fake_session):
    result = runner.invoke(app, ["clear", "tracks"], input="default\n")

    assert result.exit_code == 0, result.output
    assert any(str(c).startswith("purge:tracks") for c in fake_session)


def test_force_skips_the_prompt_and_clears(fake_session):
    result = runner.invoke(app, ["clear", "tracks", "--force"])

    assert result.exit_code == 0, result.output
    assert any(str(c).startswith("purge:tracks") for c in fake_session)


def test_account_id_is_shown_before_the_prompt(fake_session):
    result = runner.invoke(app, ["clear", "tracks"], input="wrong\n")

    # F9: the operator must be told which account they are destroying
    # before they confirm, and that requires authenticating first.
    assert "4242" in result.output
    assert "About to permanently delete" in result.output


def test_profile_name_appears_in_the_prompt(fake_session):
    result = runner.invoke(app, ["clear", "tracks", "--profile", "second"], input="wrong\n")

    assert "second" in result.output


def test_dry_run_reports_counts_without_deleting(fake_session):
    result = runner.invoke(app, ["clear", "tracks", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert any("dry_run=True" in str(c) for c in fake_session)


def test_import_value_error_is_caught_and_exits_cleanly(monkeypatch, tmp_path):
    # MAJ-1: parse_csv raises BackupFileError on an all-invalid CSV; the
    # single-file branch must catch it as TidalSyncError and exit 1, not
    # blow up.
    bad = tmp_path / "file.csv"
    bad.write_text("garbage\n")

    def _get_session(profile="default"):
        return type("S", (), {"user": FakeUser()})()

    def _boom(session, target_path, target_playlist_name=None):
        raise BackupFileError(f"no valid rows in {target_path.name}")

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module, "import_collection_from_disk", _boom)

    result = runner.invoke(app, ["import", str(bad)])

    # MAJ-1: the BackupFileError is caught and reported through the summary
    # path (exit 1 with a friendly message), not left as an uncaught
    # exception.
    assert result.exit_code == 1, result.output
    assert not isinstance(result.exception, ValueError), result.output
    assert bad.name in result.output
    assert "tidal-sync could not complete" in result.output
