"""Live verification: trace file management, verify checks, teardown.

For each live eval item:
  1. Caller generates a unique trace file path via `trace_paths_for()`.
  2. Caller sets `DATAVERSE_TRACE_FILE` env var so auth.py's trace hook
     (see docs/superpowers/patches/dataverse-skills-auth-trace.md) writes
     each HTTP request to the trace file, and each created record GUID to
     a sibling `_guids.jsonl` file.
  3. After the agent finishes, the rollout calls `run_verify()` against the
     item's `live.verify` spec, then `teardown()` to delete created records.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass


@dataclass
class LiveResult:
    pass_rate: float          # 0..1
    failures: list[str]
    created_guids: list[dict]
    trace_records: list[dict]


def trace_paths_for(out_dir: str, item_id: str) -> tuple[str, str]:
    """Return (trace_jsonl_path, guids_jsonl_path) under predictions/<item_id>/."""
    base = os.path.join(out_dir, "predictions", item_id)
    os.makedirs(base, exist_ok=True)
    return (
        os.path.join(base, "dataverse_trace.jsonl"),
        os.path.join(base, "created_guids.jsonl"),
    )


def read_jsonl(path: str) -> list[dict]:
    """Read a JSONL file. Returns [] if the file is missing."""
    if not os.path.exists(path):
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def verify_row_count(check: dict, dataverse_client) -> tuple[bool, str]:
    """Query Dataverse for a row count and compare against expected_min/_max."""
    table = check["table"]
    filt = check.get("filter", "")
    expected_min = int(check.get("expected_min", 0))
    expected_max = check.get("expected_max", None)

    # client.records.get may return a generator of pages or rows; iterate
    # carefully — we just need a count.
    result = dataverse_client.records.get(table, filter=filt, select=[])
    count = 0
    try:
        for _ in result:
            count += 1
    except TypeError:
        # Result wasn't iterable — try treating as a single row
        count = 1 if result else 0

    if count < expected_min:
        return False, f"row_count[{table}] {count} < min {expected_min}"
    if expected_max is not None and count > int(expected_max):
        return False, f"row_count[{table}] {count} > max {expected_max}"
    return True, f"row_count[{table}] = {count} (ok)"


def verify_request_count(check: dict, trace_records: list[dict]) -> tuple[bool, str]:
    """Count matching requests in the trace and compare against expected_min/_max."""
    rx = re.compile(check["endpoint_regex"])
    matched = [r for r in trace_records if rx.search(str(r.get("url") or r.get("endpoint", "")))]
    n = len(matched)
    expected_min = int(check.get("expected_min", 0))
    expected_max = check.get("expected_max", None)

    if n < expected_min:
        return False, f"request_count[{check['endpoint_regex']}] {n} < min {expected_min}"
    if expected_max is not None and n > int(expected_max):
        return False, f"request_count[{check['endpoint_regex']}] {n} > max {expected_max}"
    return True, f"request_count[{check['endpoint_regex']}] = {n} (ok)"


_VERIFY_KINDS = {
    "row_count": verify_row_count,
    "request_count": verify_request_count,
}


def run_verify(
    verify_specs: list[dict],
    trace_records: list[dict],
    dataverse_client,
) -> LiveResult:
    """Run all verify checks. Returns aggregate pass rate + per-check failure messages."""
    failures: list[str] = []
    passed = 0
    for check in verify_specs:
        kind = check.get("kind")
        if kind == "row_count":
            try:
                ok, msg = verify_row_count(check, dataverse_client)
            except Exception as e:
                ok, msg = False, f"row_count verify error: {type(e).__name__}: {e}"
        elif kind == "request_count":
            try:
                ok, msg = verify_request_count(check, trace_records)
            except Exception as e:
                ok, msg = False, f"request_count verify error: {type(e).__name__}: {e}"
        else:
            ok, msg = False, f"unknown verify kind: {kind!r}"
        if ok:
            passed += 1
        else:
            failures.append(msg)
    pass_rate = passed / len(verify_specs) if verify_specs else 1.0
    return LiveResult(
        pass_rate=pass_rate,
        failures=failures,
        created_guids=[],
        trace_records=trace_records,
    )


def teardown(
    created_guids: list[dict],
    dataverse_client,
    orphans_path: str,
) -> dict[str, int]:
    """Delete each created GUID. Failures append to orphans_path.

    Returns counts dict: {"deleted": N, "failed": M}.
    """
    counts = {"deleted": 0, "failed": 0}
    if not created_guids:
        return counts
    for g in created_guids:
        try:
            dataverse_client.records.delete(g["table"], g["guid"])
            counts["deleted"] += 1
        except Exception as e:
            counts["failed"] += 1
            os.makedirs(os.path.dirname(orphans_path) or ".", exist_ok=True)
            with open(orphans_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({**g, "teardown_error": str(e)}) + "\n")
    return counts
