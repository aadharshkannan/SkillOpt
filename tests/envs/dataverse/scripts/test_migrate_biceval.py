"""Tests for biceval → SkillOpt JSONL conversion."""
import json
from pathlib import Path

from skillopt.envs.dataverse.scripts.migrate_biceval import convert_biceval_file


BICEVAL_SAMPLE = {
    "group": "DataverseSkills",
    "scenarioName": "Data",
    "tests": [
        {
            "test_id": "data_001",
            "prompt": "Write a Python script that creates a single ticket record.",
            "expected_response": "Agent uses Python SDK pattern.",
            "category": "data",
            "description": "Single-record create via SDK.",
            "priority": 1,
            "tags": {"Suite": "Regression", "Domain": "Data", "Skill": "dv-data"},
            "custom_metadata": {"skill": "dv-data"},
            "assertions": [
                "PRIORITY_1: Agent uses the official Python SDK.",
                "PRIORITY_2: CONTAINS: new_ticket",
                "PRIORITY_2: SKILL_LOADED: dv-data",
                "PRIORITY_1: NOT_CONTAINS: One HTTP call per record",
            ],
        }
    ],
}


def test_convert_biceval_produces_valid_jsonl(tmp_path: Path):
    src = tmp_path / "dv_data.biceval.json"
    dst = tmp_path / "dv_data.jsonl"
    src.write_text(json.dumps(BICEVAL_SAMPLE))
    convert_biceval_file(str(src), str(dst), skill_name="dv-data")
    lines = dst.read_text().strip().splitlines()
    assert len(lines) == 1
    item = json.loads(lines[0])
    assert item["id"] == "data_001"
    assert item["skill"] == "dv-data"
    assert item["category"] == "happy_path"  # default for non-trap items
    assert "Agent uses the official Python SDK." in [c["claim"] for c in item["semantic"]]
    assert "new_ticket" in item["deterministic"]["must_contain"]
    assert "One HTTP call per record" in item["deterministic"]["must_not_contain"]
    assert item["deterministic"]["must_load_skill"] == "dv-data"
