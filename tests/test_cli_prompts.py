# tidal-sync: A high-performance tool for backing up and cloning Tidal libraries.
# Copyright (C) 2026 Leonid Dalin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: infoLeonid@protonMail.com
"""Tests for cli_prompts.prompt_unblock.

The safety invariant is that nothing is unblocked unless the operator
explicitly ticks the candidate and confirms the selection. Every failure
path must return an empty list, so this module only ever returns ids
after a confirmed, non-empty selection from a tty-attached prompt.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from tidal_sync import cli_prompts

# ---------------------------------------------------------------------------
# Helpers: fake questionary surface
# ---------------------------------------------------------------------------


class _FakeCheckboxApp:
    """Mimics the chainable questionary.checkbox(...) builder.

    ``unsafe_ask`` records its call and returns ``answer``. The prompt
    is callable from a worker thread so we can exercise the thread
    boundary that the real implementation uses.
    """

    def __init__(self, answer: list[Any] | Exception) -> None:
        self._answer = answer
        self.unsafe_ask_called = threading.Event()
        self.unsafe_ask_thread: str | None = None

    def unsafe_ask(self) -> list[Any]:
        self.unsafe_ask_called.set()
        self.unsafe_ask_thread = threading.current_thread().name
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer


def _make_fake_checkbox(answer: list[Any] | Exception) -> tuple[MagicMock, _FakeCheckboxApp]:
    fake_module = MagicMock()
    app = _FakeCheckboxApp(answer)
    fake_module.checkbox.return_value = app
    return fake_module, app


@pytest.fixture
def fake_questionary(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, _FakeCheckboxApp]:
    fake_module, app = _make_fake_checkbox(["a1", "a2"])
    monkeypatch.setattr("questionary.checkbox", fake_module.checkbox)
    return fake_module, app


@pytest.fixture
def fake_tty(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    fake.isatty.return_value = True
    monkeypatch.setattr(cli_prompts.sys.stdin, "isatty", fake.isatty)
    return fake


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def test_force_short_circuits_and_never_invokes_prompt(
    fake_questionary: tuple[MagicMock, _FakeCheckboxApp],
    fake_tty: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_module, _ = fake_questionary

    result = cli_prompts.prompt_unblock([("a1", "Alpha")], force=True)

    assert result == []
    fake_module.checkbox.assert_not_called()
    assert capsys.readouterr().out == ""


def test_non_tty_returns_empty_and_prints_each_candidate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_module, _ = _make_fake_checkbox(["a1"])
    monkeypatch.setattr("questionary.checkbox", fake_module.checkbox)
    monkeypatch.setattr(cli_prompts.sys.stdin, "isatty", lambda: False)

    result = cli_prompts.prompt_unblock([("a1", "Alpha"), ("a2", "Beta")], force=False)

    assert result == []
    out = capsys.readouterr().out
    assert "a1" in out
    assert "Alpha" in out
    assert "a2" in out
    assert "Beta" in out
    fake_module.checkbox.assert_not_called()


def test_exception_in_prompt_returns_empty(
    fake_questionary: tuple[MagicMock, _FakeCheckboxApp],
    fake_tty: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, app = fake_questionary
    # Swap in a prompt that raises, without rebuilding the fake module.
    app._answer = RuntimeError("boom")

    result = cli_prompts.prompt_unblock([("a1", "Alpha")], force=False)

    assert result == []
    assert app.unsafe_ask_called.is_set()


def test_keyboard_interrupt_returns_empty(
    fake_questionary: tuple[MagicMock, _FakeCheckboxApp],
    fake_tty: MagicMock,
) -> None:
    _, app = fake_questionary
    app._answer = KeyboardInterrupt()

    result = cli_prompts.prompt_unblock([("a1", "Alpha")], force=False)

    assert result == []


def test_timeout_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The prompt is patched to outlast the timeout, so the worker
    join expires. The patched timeout is a fraction of a second so the
    suite stays fast.
    """

    sleep_started = threading.Event()
    release = threading.Event()

    def slow_ask() -> list[Any]:
        sleep_started.set()
        # Block until the test releases the lock, which happens well
        # after the join has timed out.
        release.wait(timeout=5.0)
        return ["a1"]

    class _SlowApp:
        def unsafe_ask(self) -> list[Any]:
            return slow_ask()

    fake_module = MagicMock()
    fake_module.checkbox.return_value = _SlowApp()
    monkeypatch.setattr("questionary.checkbox", fake_module.checkbox)
    monkeypatch.setattr(cli_prompts.sys.stdin, "isatty", lambda: True)

    started = time.monotonic()
    result = cli_prompts.prompt_unblock([("a1", "Alpha")], force=False, timeout=0.05)
    elapsed = time.monotonic() - started

    assert result == []
    assert sleep_started.is_set()
    # Bound the wall clock; a real 90-second wait would obviously fail.
    assert elapsed < 5.0
    release.set()

    out = capsys.readouterr().out
    assert "a1" in out
    assert "Alpha" in out


def test_empty_selection_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module, _ = _make_fake_checkbox([])
    monkeypatch.setattr("questionary.checkbox", fake_module.checkbox)
    monkeypatch.setattr(cli_prompts.sys.stdin, "isatty", lambda: True)

    result = cli_prompts.prompt_unblock([("a1", "Alpha"), ("a2", "Beta")], force=False)

    assert result == []


def test_selection_returns_picked_ids(
    fake_questionary: tuple[MagicMock, _FakeCheckboxApp],
    fake_tty: MagicMock,
) -> None:
    fake_module, app = fake_questionary
    fake_module.checkbox.return_value = _FakeCheckboxApp(["a1", "a3"])

    result = cli_prompts.prompt_unblock(
        [("a1", "Alpha"), ("a2", "Beta"), ("a3", "Gamma")], force=False
    )

    assert result == ["a1", "a3"]


def test_labelling_keeps_id_when_name_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    fake_tty: MagicMock,
) -> None:
    fake_module = MagicMock()
    captured: dict[str, Any] = {}

    def fake_checkbox(message: str, choices: list[Any]) -> _FakeCheckboxApp:
        captured["message"] = message
        captured["choices"] = list(choices)
        return _FakeCheckboxApp(["a1"])

    fake_module.checkbox.side_effect = fake_checkbox
    monkeypatch.setattr("questionary.checkbox", fake_module.checkbox)
    monkeypatch.setattr(cli_prompts.sys.stdin, "isatty", lambda: True)

    result = cli_prompts.prompt_unblock([("a1", "")], force=False)

    assert result == ["a1"]
    # The id must be present in the label even when the name is empty.
    assert any("a1" in str(choice) for choice in captured["choices"])


def test_thread_boundary_unsafe_ask_runs_in_worker(
    monkeypatch: pytest.MonkeyPatch,
    fake_tty: MagicMock,
) -> None:
    main_thread = threading.current_thread().name
    fake_module = MagicMock()
    app = _FakeCheckboxApp(["a1"])
    fake_module.checkbox.return_value = app
    monkeypatch.setattr("questionary.checkbox", fake_module.checkbox)

    cli_prompts.prompt_unblock([("a1", "Alpha")], force=False)

    assert app.unsafe_ask_thread is not None
    assert app.unsafe_ask_thread != main_thread


@pytest.mark.parametrize(
    "candidates",
    [
        [],
        [("a1", "Alpha")],
        [("a1", "Alpha"), ("a2", "Beta"), ("a3", "Gamma")],
    ],
)
def test_non_tty_parametrised(
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_module, _ = _make_fake_checkbox(["unused"])
    monkeypatch.setattr("questionary.checkbox", fake_module.checkbox)
    monkeypatch.setattr(cli_prompts.sys.stdin, "isatty", lambda: False)

    result = cli_prompts.prompt_unblock(candidates, force=False)

    assert result == []
    out = capsys.readouterr().out
    for cid, name in candidates:
        assert cid in out
        if name:
            assert name in out
    fake_module.checkbox.assert_not_called()


def test_prompt_label_leads_with_the_name() -> None:
    """`Bad Bunny (4894212)` scans; `4894212 (Bad Bunny)` does not."""
    assert cli_prompts._label_for("4894212", "Bad Bunny") == "Bad Bunny (4894212)"
    assert cli_prompts._label_for("4894212", "") == "4894212"


def test_a_timed_out_prompt_restores_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An abandoned prompt thread still owns the tty until it is reset.

    Without the reset the operator's shell is left in raw mode after a
    90 second timeout.
    """
    reset_calls: list[int] = []

    class _HangingPrompt:
        def unsafe_ask(self) -> list[str]:
            import time

            time.sleep(5)
            return []

    monkeypatch.setattr(cli_prompts, "_reset_terminal", lambda: reset_calls.append(1))
    monkeypatch.setattr(cli_prompts.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("questionary.checkbox", lambda *a, **k: _HangingPrompt())

    picked = cli_prompts.prompt_unblock([("1", "A")], force=False, timeout=0.1)

    assert picked == []
    assert reset_calls == [1], "a timed-out prompt must reset the terminal"
