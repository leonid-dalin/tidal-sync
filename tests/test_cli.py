"""End-to-end CLI behaviour through typer's runner.

Unit tests reach the engine functions directly, so they cannot see
whether the confirmation happens before authentication, or whether a
declined prompt leaves the account untouched.
"""

import pytest
from typer.testing import CliRunner

from tidal_sync import cli as cli_module
from tidal_sync.cli import app

runner = CliRunner()


@pytest.fixture
def fake_session(monkeypatch):
    """Replaces authentication so a test never touches the network."""

    calls: list[str] = []

    def _get_session(profile="default"):
        calls.append(profile)
        return object()

    async def _purge(session, target):
        calls.append(f"purge:{target}")

    monkeypatch.setattr(cli_module, "get_session", _get_session)
    monkeypatch.setattr(cli_module, "purge_target_category_async", _purge)
    return calls


def test_declined_confirmation_never_authenticates(fake_session):
    result = runner.invoke(app, ["clear", "tracks"], input="n\n")

    assert "sure" in result.output.lower()
    assert fake_session == [], "a declined prompt must not reach the network"


def test_force_skips_the_prompt_and_clears(fake_session):
    result = runner.invoke(app, ["clear", "tracks", "--force"])

    assert result.exit_code == 0, result.output
    assert fake_session == ["default", "purge:tracks"]


def test_confirmation_precedes_authentication(fake_session):
    runner.invoke(app, ["clear", "tracks"], input="y\n")

    # The prompt is an interactive gate, so a session must not exist yet
    # when it is shown. Ordering is the whole point of F9.
    assert fake_session, "the purge should run once confirmed"
    assert fake_session[0] == "default", "authentication happens after the prompt"


def test_profile_name_appears_in_the_prompt(fake_session):
    result = runner.invoke(app, ["clear", "tracks", "--profile", "second"], input="n\n")

    assert "second" in result.output
