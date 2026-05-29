"""Tests for the dv-data rollout — mocks the Copilot CLI invocation + judge."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from skillopt.envs.dataverse.rollout import process_one, run_batch


@pytest.fixture
def fake_plugin(tmp_path: Path) -> Path:
    p = tmp_path / "plugin_src"
    (p / "skills" / "dv-data").mkdir(parents=True)
    (p / "skills" / "dv-data" / "SKILL.md").write_text("# Original dv-data\n", encoding="utf-8")
    return p


def _item(id_: str, must_contain=None, semantic=None) -> dict:
    return {
        "id": id_,
        "skill": "dv-data",
        "category": "happy_path",
        "prompt": "create a record",
        "expected_summary": "agent uses CreateMultiple",
        "deterministic": {"must_contain": must_contain or []},
        "semantic": [{"claim": c, "priority": 1} for c in (semantic or [])],
    }


def test_process_one_happy_path(tmp_path, fake_plugin):
    fake_judge = MagicMock()
    fake_judge.chat.return_value = "1.0 — perfect"
    with patch(
        "skillopt.envs.dataverse.rollout.run_target_exec",
        return_value=("I called client.records.create with CreateMultiple", "raw"),
    ):
        result = process_one(
            _item("a", must_contain=["CreateMultiple"], semantic=["agent used bulk form"]),
            out_root=str(tmp_path / "out"),
            skill_content="# patched skill\n",
            plugin_src_dir=str(fake_plugin),
            target_skill_name="dv-data",
            judge_client=fake_judge,
            judge_deployment="gpt-5.4-mini",
        )
    assert result["hard"] == 1
    assert result["soft"] > 0.8
    assert result["agent_ok"] is True
    assert "CreateMultiple" in result["response"]


def test_process_one_must_contain_fails_hard_zero(tmp_path, fake_plugin):
    fake_judge = MagicMock()
    fake_judge.chat.return_value = "1.0 — perfect"
    with patch(
        "skillopt.envs.dataverse.rollout.run_target_exec",
        return_value=("nope, I looped per record", "raw"),
    ):
        result = process_one(
            _item("b", must_contain=["CreateMultiple"], semantic=["agent used bulk"]),
            out_root=str(tmp_path / "out"),
            skill_content="# patched\n",
            plugin_src_dir=str(fake_plugin),
            target_skill_name="dv-data",
            judge_client=fake_judge,
            judge_deployment="gpt-5.4-mini",
        )
    assert result["hard"] == 0
    assert "CreateMultiple" in result["fail_reason"]


def test_process_one_judge_error_doesnt_crash(tmp_path, fake_plugin):
    fake_judge = MagicMock()
    fake_judge.chat.side_effect = RuntimeError("transient API")
    with patch(
        "skillopt.envs.dataverse.rollout.run_target_exec",
        return_value=("any response", "raw"),
    ):
        result = process_one(
            _item("c", semantic=["agent did the thing"]),
            out_root=str(tmp_path / "out"),
            skill_content="# patched\n",
            plugin_src_dir=str(fake_plugin),
            target_skill_name="dv-data",
            judge_client=fake_judge,
            judge_deployment="gpt-5.4-mini",
        )
    # Judge failed → semantic score 0.0 → hard=0 but no crash
    assert result["hard"] == 0
    assert result["agent_ok"] is True


def test_process_one_schema_error_returns_clean_result(tmp_path, fake_plugin):
    fake_judge = MagicMock()
    bad = {"id": "x", "skill": "dv-data"}   # missing required fields
    result = process_one(
        bad,
        out_root=str(tmp_path / "out"),
        skill_content="# x\n",
        plugin_src_dir=str(fake_plugin),
        target_skill_name="dv-data",
        judge_client=fake_judge,
        judge_deployment="gpt-5.4-mini",
    )
    assert result["hard"] == 0
    assert "schema error" in result["fail_reason"]


def test_run_batch_resumes_from_existing_results(tmp_path, fake_plugin):
    out = tmp_path / "out"
    out.mkdir()
    # Pre-populate results.jsonl with one done item.
    with (out / "results.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "a", "hard": 1, "soft": 1.0}) + "\n")

    fake_judge = MagicMock()
    fake_judge.chat.return_value = "1.0 — ok"
    items = [_item("a"), _item("b")]
    with patch(
        "skillopt.envs.dataverse.rollout.run_target_exec",
        return_value=("ok", "raw"),
    ):
        results = run_batch(
            items=items,
            out_root=str(out),
            skill_content="# x\n",
            plugin_src_dir=str(fake_plugin),
            target_skill_name="dv-data",
            judge_client=fake_judge,
            judge_deployment="gpt-5.4-mini",
            workers=2,
            exec_timeout=10,
        )
    ids = [r["id"] for r in results]
    assert "a" in ids and "b" in ids
    # `a` came from the existing results.jsonl, not re-run
    a_result = next(r for r in results if r["id"] == "a")
    assert a_result["hard"] == 1
