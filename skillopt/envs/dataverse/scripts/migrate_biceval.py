"""Convert .biceval.json test files to SkillOpt JSONL format.

Usage:
    python -m skillopt.envs.dataverse.scripts.migrate_biceval \
        --in evals/tests/dv_data.biceval.json \
        --out evals/skillopt/dv_data.jsonl \
        --skill dv-data
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_CONTAINS_RE = re.compile(r"^PRIORITY_\d+:\s*CONTAINS:\s*(.*)$", re.IGNORECASE)
_NOT_CONTAINS_RE = re.compile(r"^PRIORITY_\d+:\s*NOT_CONTAINS:\s*(.*)$", re.IGNORECASE)
_SKILL_LOADED_RE = re.compile(r"^PRIORITY_\d+:\s*SKILL_LOADED:\s*(.*)$", re.IGNORECASE)
_SEMANTIC_RE = re.compile(r"^PRIORITY_(\d+):\s*(.*)$")


def _classify_category(test: dict[str, Any]) -> str:
    custom = test.get("custom_metadata") or {}
    tags = test.get("tags") or {}
    trap = (custom.get("trap") or tags.get("Type"))
    if isinstance(trap, str):
        if trap.lower() in {"antipattern", "trap"} or "antipattern" in trap.lower():
            return "antipattern_trap"
    test_kind = custom.get("test_kind", "")
    if test_kind == "skill-contract":
        return "skill_contract"
    if tags.get("Type", "").lower() == "bulk":
        return "antipattern_trap"
    return "happy_path"


def _convert_test(test: dict[str, Any], skill_name: str) -> dict[str, Any]:
    must_contain: list[str] = []
    must_not_contain: list[str] = []
    must_load: str | None = None
    semantic: list[dict[str, Any]] = []

    for raw_assertion in test.get("assertions", []):
        a = str(raw_assertion).strip()
        if (m := _CONTAINS_RE.match(a)):
            must_contain.append(m.group(1).strip())
            continue
        if (m := _NOT_CONTAINS_RE.match(a)):
            must_not_contain.append(m.group(1).strip())
            continue
        if (m := _SKILL_LOADED_RE.match(a)):
            must_load = m.group(1).strip()
            continue
        if (m := _SEMANTIC_RE.match(a)):
            semantic.append({"claim": m.group(2).strip(), "priority": int(m.group(1))})
            continue
        # Unprefixed plain text: treat as priority 2.
        semantic.append({"claim": a, "priority": 2})

    deterministic: dict[str, Any] = {}
    if must_contain:
        deterministic["must_contain"] = must_contain
    if must_not_contain:
        deterministic["must_not_contain"] = must_not_contain
    if must_load:
        deterministic["must_load_skill"] = must_load

    return {
        "id": str(test["test_id"]),
        "skill": skill_name,
        "category": _classify_category(test),
        "prompt": str(test["prompt"]),
        "expected_summary": str(test.get("expected_response", "")),
        "deterministic": deterministic,
        "semantic": semantic,
        "tags": {k: str(v) for k, v in (test.get("tags") or {}).items()},
    }


def convert_biceval_file(in_path: str, out_path: str, skill_name: str) -> int:
    src = json.loads(Path(in_path).read_text(encoding="utf-8"))
    items = [_convert_test(t, skill_name) for t in src.get("tests", [])]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    ap.add_argument("--skill", dest="skill_name", required=True)
    args = ap.parse_args()
    n = convert_biceval_file(args.in_path, args.out_path, args.skill_name)
    print(f"converted {n} items -> {args.out_path}")


if __name__ == "__main__":
    main()
