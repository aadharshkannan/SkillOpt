"""Tests for copilot_harness — pure on-disk workspace prep + subprocess mocking."""
import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from skillopt.model.copilot_harness import prepare_workspace, run_target_exec


@pytest.fixture
def fake_plugin(tmp_path: Path) -> Path:
    """Build a minimal plugin tree mimicking the Dataverse layout."""
    p = tmp_path / "plugin_src"
    (p / "skills" / "dv-data").mkdir(parents=True)
    (p / "skills" / "dv-data" / "SKILL.md").write_text("# Original dv-data\n", encoding="utf-8")
    (p / "skills" / "dv-query").mkdir(parents=True)
    (p / "skills" / "dv-query" / "SKILL.md").write_text("# Frozen dv-query\n", encoding="utf-8")
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text('{"name": "dataverse"}', encoding="utf-8")
    return p


def test_prepare_workspace_swaps_only_target_skill(tmp_path, fake_plugin):
    work_dir = tmp_path / "work"
    out_work, out_plugin = prepare_workspace(
        work_dir=str(work_dir),
        skill_md="# Patched dv-data v42\n",
        task_text="solve this",
        plugin_src_dir=str(fake_plugin),
        target_skill_name="dv-data",
    )
    target = work_dir / "plugin" / "skills" / "dv-data" / "SKILL.md"
    assert "Patched dv-data v42" in target.read_text(encoding="utf-8")
    frozen = work_dir / "plugin" / "skills" / "dv-query" / "SKILL.md"
    assert "Frozen dv-query" in frozen.read_text(encoding="utf-8")
    assert (work_dir / "task.md").read_text(encoding="utf-8").strip() == "solve this"
    # The whole plugin manifest gets copied
    assert (work_dir / "plugin" / ".claude-plugin" / "plugin.json").exists()


def test_prepare_workspace_errors_on_missing_skill(tmp_path, fake_plugin):
    with pytest.raises(FileNotFoundError, match="not found in plugin tree"):
        prepare_workspace(
            work_dir=str(tmp_path / "work"),
            skill_md="x",
            plugin_src_dir=str(fake_plugin),
            target_skill_name="dv-doesnt-exist",
        )


def test_prepare_workspace_errors_on_bad_plugin_src(tmp_path):
    with pytest.raises(FileNotFoundError, match="plugin_src_dir not found"):
        prepare_workspace(
            work_dir=str(tmp_path / "work"),
            skill_md="x",
            plugin_src_dir=str(tmp_path / "doesnt-exist"),
            target_skill_name="dv-data",
        )


def test_run_target_exec_invokes_copilot_with_expected_flags(tmp_path, fake_plugin):
    work_dir = tmp_path / "work"
    prepare_workspace(
        work_dir=str(work_dir),
        skill_md="# Patched\n",
        task_text="t",
        plugin_src_dir=str(fake_plugin),
        target_skill_name="dv-data",
    )
    fake_proc = MagicMock(stdout="HELLO", stderr="", returncode=0)
    with patch("skillopt.model.copilot_harness.subprocess.run", return_value=fake_proc) as M:
        final, raw = run_target_exec(
            work_dir=str(work_dir),
            prompt="say hi",
            model="gpt-5.5",
            timeout=60,
        )
    assert final == "HELLO"
    args = M.call_args[0][0]   # the cmd list
    assert args[0].endswith("copilot")
    assert "-p" in args and args[args.index("-p") + 1] == "say hi"
    assert "--allow-all-tools" in args
    assert "--no-ask-user" in args
    assert "-s" in args
    assert "--plugin-dir" in args
    assert "--model" in args and args[args.index("--model") + 1] == "gpt-5.5"
    assert "--effort" in args
    assert "-C" in args


def test_run_target_exec_handles_timeout(tmp_path, fake_plugin):
    import subprocess
    work_dir = tmp_path / "work"
    prepare_workspace(
        work_dir=str(work_dir),
        skill_md="# x\n",
        plugin_src_dir=str(fake_plugin),
        target_skill_name="dv-data",
    )
    with patch("skillopt.model.copilot_harness.subprocess.run", side_effect=subprocess.TimeoutExpired("copilot", 1)):
        final, raw = run_target_exec(work_dir=str(work_dir), prompt="p", timeout=1)
    assert final == ""
    assert "TIMEOUT" in raw
