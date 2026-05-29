"""Backend wrapper for GitHub Copilot CLI as a SkillOpt target.

Copilot CLI is target-only — optimizer-side calls go through openai_chat.

For generic chat_target calls (no plugin loaded), this module invokes
copilot via a small subprocess wrapper. For agent-style rollouts that
need the Dataverse plugin loaded, callers should use
copilot_harness.run_target_exec directly with --plugin-dir.
"""
from __future__ import annotations

import subprocess
import tempfile
from typing import Any

from skillopt.model.backend_config import (
    configure_copilot_cli_exec,
    get_copilot_cli_exec_config,
)


REASONING_EFFORT: str | None = None
_OPTIMIZER_DEPLOYMENT: str = ""
_TARGET_DEPLOYMENT: str = ""


def _run_chat(*, system: str, user: str, model: str, timeout: int, effort: str) -> str:
    """Run copilot non-interactively with the combined prompt, no plugin-dir."""
    cfg = get_copilot_cli_exec_config()
    binary = cfg.get("path", "copilot") or "copilot"

    combined = f"{system.strip()}\n\n{user.strip()}" if system.strip() else user

    with tempfile.TemporaryDirectory() as work_dir:
        cmd = [
            str(binary),
            "-p", combined,
            "--allow-all-tools",
            "--no-ask-user",
            "-s",
            "--model", model,
            "--effort", effort,
            "-C", work_dir,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT after {timeout}s]"
        except FileNotFoundError as e:
            return f"[copilot binary not found: {e}]"
        if proc.returncode != 0:
            return f"[exit={proc.returncode}] {proc.stdout}\nSTDERR: {proc.stderr}"
        return (proc.stdout or "").strip()


def _effective_model(deployment: str | None) -> str:
    if deployment:
        return deployment
    cfg = get_copilot_cli_exec_config()
    return str(cfg.get("model") or "gpt-5.5")


def _effective_effort() -> str:
    if REASONING_EFFORT:
        return REASONING_EFFORT
    cfg = get_copilot_cli_exec_config()
    return str(cfg.get("effort") or "medium")


def chat_target(
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "target",
    timeout: int | None = None,
) -> tuple[str, dict[str, int]]:
    text = _run_chat(
        system=system,
        user=user,
        model=_effective_model(_TARGET_DEPLOYMENT),
        timeout=int(timeout or 240),
        effort=_effective_effort(),
    )
    return text, {}


def chat_with_deployment(
    deployment: str,
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "custom",
    timeout: int | None = None,
) -> tuple[str, dict[str, int]]:
    text = _run_chat(
        system=system,
        user=user,
        model=_effective_model(deployment),
        timeout=int(timeout or 240),
        effort=_effective_effort(),
    )
    return text, {}


def _messages_to_system_user(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Collapse a chat messages list into (system, user) for Copilot's -p flag.

    Copilot CLI doesn't have a separate system/user split in non-interactive
    mode — we glue them into one prompt string.
    """
    system_parts: list[str] = []
    user_parts: list[str] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            # Flatten list-of-parts content (multimodal) — text-only here.
            content = "".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        if role == "system":
            system_parts.append(str(content))
        else:
            user_parts.append(f"[{role}] {content}")
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


def chat_target_messages(
    messages: list[dict[str, Any]],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "target",
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    timeout: int | None = None,
) -> tuple[Any, dict[str, int]]:
    system, user = _messages_to_system_user(messages)
    text = _run_chat(
        system=system,
        user=user,
        model=_effective_model(_TARGET_DEPLOYMENT),
        timeout=int(timeout or 240),
        effort=_effective_effort(),
    )
    if return_message:
        return {"role": "assistant", "content": text}, {}
    return text, {}


def chat_messages_with_deployment(
    deployment: str,
    messages: list[dict[str, Any]],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "custom",
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    timeout: int | None = None,
) -> tuple[Any, dict[str, int]]:
    system, user = _messages_to_system_user(messages)
    text = _run_chat(
        system=system,
        user=user,
        model=_effective_model(deployment),
        timeout=int(timeout or 240),
        effort=_effective_effort(),
    )
    if return_message:
        return {"role": "assistant", "content": text}, {}
    return text, {}


def chat_optimizer(*args: Any, **kwargs: Any) -> tuple[str, dict[str, int]]:
    raise NotImplementedError(
        "Copilot CLI backend is target-only. Configure optimizer_backend=openai_chat."
    )


def chat_optimizer_messages(*args: Any, **kwargs: Any) -> tuple[Any, dict[str, int]]:
    raise NotImplementedError(
        "Copilot CLI backend is target-only. Configure optimizer_backend=openai_chat."
    )


def get_token_summary() -> dict[str, dict[str, int]]:
    return {}


def reset_token_tracker() -> None:
    pass


def set_reasoning_effort(effort: str | None) -> None:
    global REASONING_EFFORT
    REASONING_EFFORT = (effort or "").strip() or None
    if REASONING_EFFORT:
        configure_copilot_cli_exec(effort=REASONING_EFFORT)


def set_target_deployment(deployment: str) -> None:
    global _TARGET_DEPLOYMENT
    _TARGET_DEPLOYMENT = (deployment or "").strip()
    if _TARGET_DEPLOYMENT:
        configure_copilot_cli_exec(model=_TARGET_DEPLOYMENT)


def set_optimizer_deployment(deployment: str) -> None:
    global _OPTIMIZER_DEPLOYMENT
    _OPTIMIZER_DEPLOYMENT = (deployment or "").strip()


def configure_azure_openai(**kwargs: Any) -> None:
    # Copilot CLI doesn't use Azure endpoints; no-op accepting any kwargs.
    return None
