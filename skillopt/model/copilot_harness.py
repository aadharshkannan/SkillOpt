"""Helpers for running the GitHub Copilot CLI as the SkillOpt target harness.

Mirrors the shape of `codex_harness.py` and `claude_backend.py` but invokes
the `copilot` binary with the Copilot CLI's actual non-interactive flags.

Key primitive: `prepare_workspace` materializes a working-copy plugin tree
under `<work_dir>/plugin/` with exactly one `SKILL.md` swapped in for the
target skill. Other skills in the plugin stay at their committed version.

Auth is the user's responsibility (gh auth login or COPILOT_GITHUB_TOKEN env var).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from typing import Any

from skillopt.model.backend_config import (
    get_copilot_cli_exec_config,
    get_target_backend,
)


def render_skill_md(
    skill_content: str,
    *,
    name: str = "dv-data",
    description: str = "Use when the user asks about Dataverse.",
    preamble: str = "",
) -> str:
    """Build a SKILL.md document with YAML frontmatter + body.

    Used when the caller wants to wrap raw skill content as a complete SKILL.md
    suitable for writing into the plugin's skills/<name>/SKILL.md slot.

    NOTE: For Dataverse, the existing SKILL.md already has frontmatter — this
    helper is provided for the rare case where skill_content is bare body without
    frontmatter. The default workflow is `prepare_workspace(skill_md=<full SKILL.md text>)`
    where the caller passes the complete file.
    """
    body = skill_content.strip() or "(No additional dynamic guidance.)"
    if body.startswith("---"):
        # Already has frontmatter — return as-is.
        return body
    chunks = [
        "---",
        f'name: "{name}"',
        f'description: "{description}"',
        "---",
        "",
    ]
    if preamble.strip():
        chunks.append(preamble.strip())
        chunks.append("")
    chunks.append(body)
    return "\n".join(chunks)


def prepare_workspace(
    *,
    work_dir: str,
    skill_md: str,
    task_text: str = "",
    task_filename: str = "task.md",
    plugin_src_dir: str = "",
    target_skill_name: str = "",
    images: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Materialize a Copilot CLI workspace with a swapped plugin tree.

    1. Wipe `work_dir` if it exists, recreate clean.
    2. Copy entire plugin tree from `plugin_src_dir` to `<work_dir>/plugin/`.
       - `plugin_src_dir` should be a directory whose contents are the plugin manifest
         tree (containing `skills/`, `.claude-plugin/`, etc.) — i.e., the same shape
         Copilot CLI loads via `--plugin-dir`.
       - For Dataverse-skills, this is typically the local Dataverse-skills clone path
         (e.g., `<repo>/.github/plugins/dataverse/`) OR the installed plugin tree at
         `~/.copilot/installed-plugins/awesome-copilot/dataverse/`. Caller decides.
    3. Overwrite `<work_dir>/plugin/skills/<target_skill_name>/SKILL.md` with `skill_md`.
       Other skills' files stay frozen.
    4. Write `task_text` to `<work_dir>/<task_filename>` so the agent can read it via -C.

    Returns: (work_dir, path-to-plugin-dir).

    Raises:
        FileNotFoundError if plugin_src_dir doesn't exist or doesn't contain
        skills/<target_skill_name>/SKILL.md.
    """
    if not plugin_src_dir or not os.path.isdir(plugin_src_dir):
        raise FileNotFoundError(f"plugin_src_dir not found: {plugin_src_dir!r}")
    if not target_skill_name:
        raise ValueError("target_skill_name is required")
    target_skill_md = os.path.join(plugin_src_dir, "skills", target_skill_name, "SKILL.md")
    if not os.path.isfile(target_skill_md):
        raise FileNotFoundError(
            f"target skill {target_skill_name!r} not found in plugin tree: {target_skill_md}"
        )

    # Step 1: reset work_dir
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)
    os.makedirs(work_dir, exist_ok=True)

    # Step 2: copy plugin tree
    plugin_dst = os.path.join(work_dir, "plugin")
    shutil.copytree(plugin_src_dir, plugin_dst)

    # Step 3: overwrite target skill
    target_dst = os.path.join(plugin_dst, "skills", target_skill_name, "SKILL.md")
    with open(target_dst, "w", encoding="utf-8") as f:
        f.write(skill_md)

    # Step 4: write task.md
    if task_text:
        with open(os.path.join(work_dir, task_filename), "w", encoding="utf-8") as f:
            f.write(task_text)

    # Optional extra files
    if extra_files:
        for relpath, content in extra_files.items():
            full = os.path.join(work_dir, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)

    return work_dir, plugin_dst


def run_target_exec(
    *,
    work_dir: str,
    prompt: str,
    model: str = "",
    timeout: int = 240,
    extra_env: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Invoke Copilot CLI non-interactively against the prepared workspace.

    Returns (final_message, raw_stdout). For Copilot's `-s` flag, raw_stdout is
    already just the agent's response — final_message and raw are the same string.

    Args:
        work_dir: the workspace prepared by prepare_workspace (contains plugin/, task.md).
        prompt: the prompt string sent to the agent.
        model: model id like "gpt-5.5". If empty, uses COPILOT_CLI_EXEC_MODEL config.
        timeout: subprocess timeout in seconds.
        extra_env: optional dict of additional env vars to inject (e.g. DATAVERSE_TRACE_FILE).

    Auth: caller is responsible for `gh auth login` having been run, or for
    setting COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN env vars.
    """
    cfg = get_copilot_cli_exec_config()
    binary = cfg.get("path", "copilot") or "copilot"
    effective_model = model or cfg.get("model") or "gpt-5.5"
    effort = cfg.get("effort") or "medium"

    plugin_dir = os.path.join(work_dir, "plugin")
    if not os.path.isdir(plugin_dir):
        raise FileNotFoundError(
            f"plugin dir not found at {plugin_dir}; did you call prepare_workspace?"
        )

    cmd = [
        binary,
        "-p", prompt,
        "--allow-all-tools",
        "--no-ask-user",
        "-s",
        "--plugin-dir", plugin_dir,
        "--model", effective_model,
        "--effort", effort,
        "-C", work_dir,
    ]

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return ("", f"[TIMEOUT after {timeout}s]")
    except FileNotFoundError as e:
        return ("", f"[copilot binary not found: {e}]")

    raw_out = proc.stdout or ""
    raw_err = proc.stderr or ""

    if proc.returncode != 0:
        return (
            raw_out.strip(),
            f"[exit={proc.returncode}]\nSTDOUT:\n{raw_out}\nSTDERR:\n{raw_err}",
        )

    final = raw_out.strip()
    return (final, raw_out)
