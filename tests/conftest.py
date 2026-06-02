"""Pytest fixtures shared across the SkillOpt test suite."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure src is importable.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def pytest_configure(config):
    """Default to a hermetic config — no live Azure/Dataverse calls in unit tests."""
    os.environ.setdefault("REFLACT_MODEL_BACKEND", "azure_openai")
    os.environ.setdefault("TARGET_BACKEND", "openai_chat")
    os.environ.setdefault("OPTIMIZER_BACKEND", "openai_chat")
