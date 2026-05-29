"""Smoke test for JudgeClient — verifies the public interface, mocks the actual LLM call."""
from unittest.mock import patch, MagicMock

from skillopt.envs.dataverse.judges_client import JudgeClient


def test_judge_client_chat_returns_text():
    """Test that JudgeClient.chat() returns text from the LLM."""
    client = JudgeClient(deployment="gpt-5.4-mini")
    mock_response = "0.9 — looks good"
    with patch(
        "skillopt.envs.dataverse.judges_client.azure_openai.chat_with_deployment",
        return_value=(mock_response, {"prompt_tokens": 10, "completion_tokens": 5}),
    ) as mock_call:
        result = client.chat("gpt-5.4-mini", "You are a judge.", "Is this good?")
        assert result == mock_response
        mock_call.assert_called_once_with(
            deployment="gpt-5.4-mini",
            system="You are a judge.",
            user="Is this good?",
            stage="judge",
        )


def test_judge_client_default_deployment_from_env(monkeypatch):
    """Test that JudgeClient uses JUDGE_AZURE_OPENAI_DEPLOYMENT env var."""
    monkeypatch.setenv("JUDGE_AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-nano")
    client = JudgeClient()
    assert client.deployment == "gpt-5.4-nano"


def test_judge_client_deployment_override(monkeypatch):
    """Test that constructor deployment param overrides env var."""
    monkeypatch.setenv("JUDGE_AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-nano")
    client = JudgeClient(deployment="gpt-5.4-custom")
    assert client.deployment == "gpt-5.4-custom"
