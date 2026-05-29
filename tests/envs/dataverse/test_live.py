"""Tests for live verification logic — all Dataverse calls are mocked."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from skillopt.envs.dataverse.live import (
    LiveResult,
    read_jsonl,
    run_verify,
    teardown,
    trace_paths_for,
    verify_request_count,
    verify_row_count,
)


def test_trace_paths_for_creates_directory(tmp_path):
    trace, guids = trace_paths_for(str(tmp_path), "item_001")
    assert (Path(trace).parent).exists()
    assert trace.endswith("dataverse_trace.jsonl")
    assert guids.endswith("created_guids.jsonl")


def test_read_jsonl_missing_file_returns_empty(tmp_path):
    assert read_jsonl(str(tmp_path / "nope.jsonl")) == []


def test_read_jsonl_skips_blank_and_bad_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n\n{bad json\n{"b": 2}\n', encoding="utf-8")
    items = read_jsonl(str(p))
    assert items == [{"a": 1}, {"b": 2}]


def test_verify_request_count_pass():
    trace = [{"url": "https://x/CreateMultiple", "method": "POST", "status": 200}]
    ok, msg = verify_request_count(
        {"endpoint_regex": "/CreateMultiple", "expected_max": 1}, trace
    )
    assert ok is True
    assert "= 1" in msg


def test_verify_request_count_fail_too_many():
    trace = [
        {"url": f"https://x/api/data/v9.2/new_ticket", "method": "POST", "status": 204}
        for _ in range(500)
    ]
    ok, msg = verify_request_count(
        {"endpoint_regex": "/new_ticket", "expected_max": 1}, trace
    )
    assert ok is False
    assert "500" in msg


def test_verify_request_count_below_min():
    ok, msg = verify_request_count(
        {"endpoint_regex": "/CreateMultiple", "expected_min": 1}, []
    )
    assert ok is False


def test_verify_request_count_uses_endpoint_field_fallback():
    trace = [{"endpoint": "/api/data/v9.2/sko_eval_ticket(guid)", "method": "DELETE"}]
    ok, _ = verify_request_count(
        {"endpoint_regex": "/sko_eval_ticket", "expected_min": 1, "expected_max": 1}, trace
    )
    assert ok is True


def test_verify_row_count_pass():
    client = MagicMock()
    client.records.get.return_value = iter([{}, {}, {}])  # 3 rows
    ok, msg = verify_row_count(
        {"table": "sko_eval_ticket", "expected_min": 3, "expected_max": 3}, client
    )
    assert ok is True


def test_verify_row_count_fail_too_few():
    client = MagicMock()
    client.records.get.return_value = iter([{}])  # 1 row
    ok, msg = verify_row_count(
        {"table": "x", "expected_min": 5}, client
    )
    assert ok is False
    assert "1 < min 5" in msg


def test_run_verify_aggregates_and_handles_unknown_kind():
    trace = [{"url": "/CreateMultiple"}]
    client = MagicMock()
    client.records.get.return_value = iter([{}, {}])
    res = run_verify(
        [
            {"kind": "request_count", "endpoint_regex": "/CreateMultiple", "expected_max": 1},
            {"kind": "row_count", "table": "x", "expected_min": 2, "expected_max": 2},
            {"kind": "made_up", "what": "ever"},
        ],
        trace,
        client,
    )
    assert isinstance(res, LiveResult)
    # 2 of 3 passed
    assert abs(res.pass_rate - (2/3)) < 0.01
    assert any("unknown verify kind" in f for f in res.failures)


def test_run_verify_no_specs_passes():
    res = run_verify([], [], MagicMock())
    assert res.pass_rate == 1.0
    assert res.failures == []


def test_teardown_deletes_and_records_orphans(tmp_path):
    orphans = tmp_path / "orphans.jsonl"
    client = MagicMock()
    client.records.delete.side_effect = [None, RuntimeError("boom"), None]
    counts = teardown(
        [
            {"table": "sko_eval", "guid": "a"},
            {"table": "sko_eval", "guid": "b"},
            {"table": "sko_eval", "guid": "c"},
        ],
        client,
        str(orphans),
    )
    assert counts == {"deleted": 2, "failed": 1}
    lines = [json.loads(line) for line in orphans.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["guid"] == "b"
    assert "boom" in lines[0]["teardown_error"]


def test_teardown_empty_list_is_noop(tmp_path):
    counts = teardown([], MagicMock(), str(tmp_path / "orphans.jsonl"))
    assert counts == {"deleted": 0, "failed": 0}
    assert not (tmp_path / "orphans.jsonl").exists()
