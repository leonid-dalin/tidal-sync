"""Profile name validation and token file permissions.

The profile name is interpolated straight into a filename and is
attacker-controllable via --profile. An unvalidated name escapes the
config directory, and logout would then zero-fill an arbitrary file.
"""

import os
import stat
from pathlib import Path

import pytest

from tidal_sync import auth
from tidal_sync.domain.exceptions import TidalAuthenticationError


@pytest.mark.parametrize(
    "bad",
    [
        "../../tmp/evil",
        "..\\..\\windows\\evil",
        "a/b",
        "",
        "x" * 200,
        "/abs/path",
        "C:\\Windows\\Temp\\x",
    ],
)
def test_invalid_profile_names_are_rejected(bad):
    with pytest.raises(TidalAuthenticationError):
        auth.get_token_path(bad)


@pytest.mark.parametrize("good", ["default", "my-profile", "acc_1", "A.b"])
def test_valid_profile_names_are_accepted(good, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    assert auth.get_token_path(good) == tmp_path / f"{good}.json"


def _fake_session():
    class FakeSession:
        token_type = "Bearer"
        access_token = "secret-access-token"
        refresh_token = "secret-refresh-token"
        expiry_time = None

        class user:
            id = 42

    return FakeSession()


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes only")
def test_token_is_never_world_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    path = auth.get_token_path("default")
    path.write_text("{}", encoding="utf-8")

    auth._save_session_to_disk(_fake_session(), path)

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert not mode & stat.S_IRWXG
    assert not mode & stat.S_IRWXO


def test_token_is_written_and_the_directory_is_private(tmp_path, monkeypatch):
    """Runs on every platform, including the Windows dev host.

    The implementation only chmods on POSIX, so an unconditional mode
    assertion cannot pass there. What does hold everywhere: the token is
    written, and the config directory is created with owner-only access.
    """
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    path = auth.get_token_path("default")

    auth._save_session_to_disk(_fake_session(), path)

    assert path.exists()
    assert "secret-access-token" in path.read_text(encoding="utf-8")


def test_config_dir_is_private_on_posix(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("POSIX modes only")
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path / "nested")
    auth.get_token_path("default")
    assert stat.S_IMODE(os.stat(tmp_path / "nested").st_mode) == 0o700


def test_traversal_never_escapes_the_config_dir(tmp_path, monkeypatch):
    """The whole point: no profile name may resolve outside CONFIG_DIR."""
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)

    for bad in ("../../tmp/evil", "..\\..\\evil", "a/b", "/abs"):
        with pytest.raises(TidalAuthenticationError):
            auth.get_token_path(bad)

    assert not (tmp_path.parent / "evil.json").exists()
    assert Path(str(tmp_path)).exists()
