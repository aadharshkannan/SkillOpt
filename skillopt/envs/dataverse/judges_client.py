"""Thin wrapper for the judge role Azure OpenAI deployment."""
from __future__ import annotations

import os

from skillopt.model import azure_openai


class JudgeClient:
    """Client for judge role LLM calls."""

    def __init__(self, deployment: str | None = None):
        """Initialize JudgeClient with a deployment name.

        Args:
            deployment: The Azure OpenAI deployment to use. Defaults to env var
                JUDGE_AZURE_OPENAI_DEPLOYMENT or "gpt-4o".
        """
        self.deployment = deployment or os.environ.get("JUDGE_AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    def chat(self, deployment: str, system: str, user: str) -> str:
        """Make a single LLM call to the judge model.

        Args:
            deployment: Azure OpenAI deployment name (overrides instance default).
            system: System prompt.
            user: User message.

        Returns:
            The text response from the LLM.
        """
        text, _ = azure_openai.chat_with_deployment(
            deployment=deployment,
            system=system,
            user=user,
            stage="judge",
        )
        return text
