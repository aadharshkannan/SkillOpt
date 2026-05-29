"""Tests for the Dataverse eval item schema."""
import pytest

from skillopt.envs.dataverse.schema import (
    parse_item,
    SchemaError,
)


def test_minimal_item_parses():
    raw = {
        "id": "x_001",
        "skill": "dv-data",
        "category": "happy_path",
        "prompt": "do the thing",
        "expected_summary": "agent does the thing",
        "deterministic": {},
        "semantic": [],
    }
    item = parse_item(raw)
    assert item.id == "x_001"
    assert item.skill == "dv-data"
    assert item.live is None


def test_live_block_parses():
    raw = {
        "id": "x_002",
        "skill": "dv-data",
        "category": "antipattern_trap",
        "prompt": "p",
        "expected_summary": "e",
        "deterministic": {"must_contain": ["foo"], "must_not_contain": ["bar"], "must_load_skill": "dv-data"},
        "semantic": [{"claim": "agent did X", "priority": 1}],
        "live": {
            "enabled": True,
            "setup": {"ensure_table": "sko_eval_ticket"},
            "verify": [{"kind": "row_count", "table": "sko_eval_ticket", "expected_min": 1, "expected_max": 1}],
            "teardown": "delete_session_records",
        },
    }
    item = parse_item(raw)
    assert item.live is not None
    assert item.live.enabled is True
    assert item.live.verify[0]["kind"] == "row_count"


def test_missing_required_field_errors():
    with pytest.raises(SchemaError, match="missing required field"):
        parse_item({"id": "x", "skill": "dv-data"})


def test_invalid_category_errors():
    with pytest.raises(SchemaError, match="invalid category"):
        parse_item({
            "id": "x", "skill": "dv-data", "category": "made_up",
            "prompt": "p", "expected_summary": "e", "deterministic": {}, "semantic": [],
        })


def test_semantic_priority_out_of_range():
    with pytest.raises(SchemaError, match="priority"):
        parse_item({
            "id": "x", "skill": "dv-data", "category": "happy_path",
            "prompt": "p", "expected_summary": "e", "deterministic": {},
            "semantic": [{"claim": "c", "priority": 99}],
        })
