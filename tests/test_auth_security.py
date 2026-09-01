"""Account collision rejection and truthful secure deletion.

Two profiles bound to one account let `clear --profile backup` destroy
the account that was exported from. And secure_delete_token claimed
success after a failed verification.
"""

import json

import pytest

from tidal_sync import auth
from tidal_sync.domain.exceptions import TidalAuthenticationError


def _fake_session(user_id=99):
    class FakeSession:
        token_type = "Bearer"
        access_token = "a"
        refresh_token = "r"
        expiry_time = None

        class user:
            id = user_id

    return FakeSession()


def test_collision_is_rejected_not_just_warned(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    (tmp_path / "other.json").write_text(json.dumps({"user_id": 99}), encoding="utf-8")

    with pytest.raises(TidalAuthenticationError):
        auth._check_account_collision(_fake_session(99), "default")


def test_no_collision_when_the_same_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    (tmp_path / "default.json").write_text(json.dumps({"user_id": 99}), encoding="utf-8")

    auth._check_account_collision(_fake_session(99), "default")


def test_no_collision_for_a_different_account(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    (tmp_path / "other.json").write_text(json.dumps({"user_id": 1}), encoding="utf-8")

    auth._check_account_collision(_fake_session(99), "default")


def test_secure_delete_overwrites_then_unlinks(tmp_path, monkeypatch):
    token = tmp_path / "default.json"
    secret = b'{"access_token": "super-secret-value"}'
    token.write_bytes(secret)
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)

    assert auth.secure_delete_token("default") is True
    assert not token.exists()


def test_secure_delete_reports_false_when_overwrite_fails(tmp_path, monkeypatch):
    token = tmp_path / "default.json"
    token.write_text(json.dumps({"access_token": "secret"}), encoding="utf-8")
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)

    def exploding_open(path, *args, **kwargs):
        raise OSError("file locked")

    # auth.py opens the token with the builtin open(), not os.open(), so
    # patching os.open would leave this test inert.
    monkeypatch.setattr("builtins.open", exploding_open)

    assert auth.secure_delete_token("default") is False
    assert token.exists(), "must NOT delete a file it failed to overwrite"


def test_secure_delete_reports_false_for_a_missing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    assert auth.secure_delete_token("nope") is False


def test_failed_verification_does_not_unlink(tmp_path, monkeypatch):
    token = tmp_path / "default.json"
    token.write_bytes(b'{"access_token": "secret"}')
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)

    real_open = open

    class LyingFile:
        """Accepts the zero-fill but reads back the original content."""

        def __init__(self, path, mode):
            self._f = real_open(path, mode)
            self._original = real_open(path, "rb").read()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()
            return False

        def __getattr__(self, name):
            return getattr(self._f, name)

        def read(self, *a):
            return self._original

    def fake_open(path, mode="r", *args, **kwargs):
        if "b" in mode and "+" in mode:
            return LyingFile(path, mode)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    assert auth.secure_delete_token("default") is False
    assert token.exists(), "a failed verification must not unlink the token"
