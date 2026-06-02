"""Eval item schema for Dataverse SkillOpt env.

One JSONL line per item; this module parses and validates a single line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALLOWED_CATEGORIES = {"happy_path", "skill_contract", "antipattern_trap", "edge_case", "cross_tool"}


class SchemaError(ValueError):
    """Raised when an eval item violates the schema."""


@dataclass
class SemanticClaim:
    claim: str
    priority: int


@dataclass
class LiveBlock:
    enabled: bool
    setup: dict[str, Any]
    verify: list[dict[str, Any]]
    teardown: str


@dataclass
class EvalItem:
    id: str
    skill: str
    category: str
    prompt: str
    expected_summary: str
    deterministic: dict[str, Any]
    semantic: list[SemanticClaim]
    live: LiveBlock | None = None
    tags: dict[str, str] = field(default_factory=dict)


_REQUIRED = ("id", "skill", "category", "prompt", "expected_summary", "deterministic", "semantic")


def parse_item(raw: dict[str, Any]) -> EvalItem:
    for f in _REQUIRED:
        if f not in raw:
            raise SchemaError(f"missing required field {f!r} in item {raw.get('id', '<no id>')!r}")
    if raw["category"] not in ALLOWED_CATEGORIES:
        raise SchemaError(
            f"invalid category {raw['category']!r} in item {raw['id']!r}; "
            f"expected one of {sorted(ALLOWED_CATEGORIES)}"
        )
    sem = []
    for s in raw["semantic"]:
        prio = s.get("priority", 2)
        if prio not in (1, 2, 3):
            raise SchemaError(f"item {raw['id']!r}: semantic priority must be 1/2/3, got {prio!r}")
        sem.append(SemanticClaim(claim=str(s["claim"]), priority=int(prio)))
    live = None
    if "live" in raw and raw["live"]:
        lb = raw["live"]
        live = LiveBlock(
            enabled=bool(lb.get("enabled", False)),
            setup=dict(lb.get("setup") or {}),
            verify=list(lb.get("verify") or []),
            teardown=str(lb.get("teardown") or "delete_session_records"),
        )
    return EvalItem(
        id=str(raw["id"]),
        skill=str(raw["skill"]),
        category=str(raw["category"]),
        prompt=str(raw["prompt"]),
        expected_summary=str(raw["expected_summary"]),
        deterministic=dict(raw["deterministic"]),
        semantic=sem,
        live=live,
        tags=dict(raw.get("tags") or {}),
    )


def load_jsonl(path: str) -> list[EvalItem]:
    import json
    items = []
    with open(path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise SchemaError(f"{path}:{line_num} invalid JSON: {e}") from e
            items.append(parse_item(raw))
    return items
