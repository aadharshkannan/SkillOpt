"""Tests for copilot_cli_exec config wiring in backend_config."""
import os

import pytest

from skillopt.model import backend_config as bc


def test_copilot_cli_exec_is_valid_target_backend():
    bc.set_target_backend("copilot_cli_exec")
    assert bc.get_target_backend() == "copilot_cli_exec"
    assert bc.is_target_exec_backend() is True


def test_configure_copilot_cli_exec_sets_env(monkeypatch):
    monkeypatch.delenv("COPILOT_CLI_EXEC_PATH", raising=False)
    bc.configure_copilot_cli_exec(
        path="/usr/local/bin/copilot",
        profile="dv-eval",
        model="gpt-5.5",
        use_sdk="auto",
        effort="medium",
    )
    cfg = bc.get_copilot_cli_exec_config()
    assert cfg["path"] == "/usr/local/bin/copilot"
    assert cfg["profile"] == "dv-eval"
    assert cfg["model"] == "gpt-5.5"
    assert os.environ["COPILOT_CLI_EXEC_PATH"] == "/usr/local/bin/copilot"


def test_set_target_backend_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported target backend"):
        bc.set_target_backend("notathing")
