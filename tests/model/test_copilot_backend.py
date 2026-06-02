"""Tests for copilot_backend — mocks subprocess to avoid real CLI calls."""
from unittest.mock import patch, MagicMock

import pytest

from skillopt.model import copilot_backend


def test_chat_target_returns_text(monkeypatch):
    fake_proc = MagicMock(stdout="hello world", stderr="", returncode=0)
    with patch("skillopt.model.copilot_backend.subprocess.run", return_value=fake_proc):
        text, tokens = copilot_backend.chat_target(
            system="be terse", user="say hi", timeout=10,
        )
    assert text == "hello world"
    assert isinstance(tokens, dict)


def test_chat_target_messages_collapses_roles():
    fake_proc = MagicMock(stdout="ok", stderr="", returncode=0)
    with patch("skillopt.model.copilot_backend.subprocess.run", return_value=fake_proc) as M:
        text, _ = copilot_backend.chat_target_messages(
            messages=[
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ack"},
                {"role": "user", "content": "second"},
            ],
            timeout=10,
        )
    assert text == "ok"
    cmd = M.call_args[0][0]
    # The combined prompt should contain both system content and both user messages.
    combined = cmd[cmd.index("-p") + 1]
    assert "be terse" in combined
    assert "first" in combined
    assert "second" in combined


def test_chat_optimizer_raises():
    with pytest.raises(NotImplementedError, match="target-only"):
        copilot_backend.chat_optimizer(system="x", user="y")


def test_chat_target_timeout_returns_error_text():
    import subprocess
    with patch(
        "skillopt.model.copilot_backend.subprocess.run",
        side_effect=subprocess.TimeoutExpired("copilot", 1),
    ):
        text, _ = copilot_backend.chat_target(system="", user="x", timeout=1)
    assert "TIMEOUT" in text


def test_set_target_deployment_calls_configure(monkeypatch):
    captured = {}
    def fake_configure(**kwargs):
        captured.update(kwargs)
    monkeypatch.setattr("skillopt.model.copilot_backend.configure_copilot_cli_exec", fake_configure)
    copilot_backend.set_target_deployment("gpt-5.4")
    assert captured.get("model") == "gpt-5.4"


def test_get_token_summary_empty():
    assert copilot_backend.get_token_summary() == {}
