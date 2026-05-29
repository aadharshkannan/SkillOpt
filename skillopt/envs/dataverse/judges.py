"""Reward function — deterministic checks, semantic judge, composition.

For now this file only implements the deterministic part. Task 12 adds the LLM judge.
Task 13 wires both into the soft/hard score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DeterministicResult:
    must_contain_pass: float = 1.0       # 1.0 = all hit (or no spec); 0.0 = at least one missed
    must_not_contain_pass: float = 1.0
    must_load_skill_pass: float | None = None  # None = no spec
    failures: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def overall_pass_rate(self) -> float:
        scores = [self.must_contain_pass, self.must_not_contain_pass]
        if self.must_load_skill_pass is not None:
            scores.append(self.must_load_skill_pass)
        if not scores:
            return 1.0
        return sum(scores) / len(scores)

    def all_priority_1_pass(self) -> bool:
        """Every P1 deterministic check must pass for hard=1.

        For deterministic, all checks are treated as P1 by convention (they're surface-level).
        """
        if self.must_contain_pass < 1.0:
            return False
        if self.must_not_contain_pass < 1.0:
            return False
        if self.must_load_skill_pass == 0.0:
            return False
        return True


def run_deterministic_checks(
    *,
    response: str,
    spec: dict,
    skill_loaded: str | None,
) -> DeterministicResult:
    r = DeterministicResult()
    must_contain = spec.get("must_contain") or []
    must_not_contain = spec.get("must_not_contain") or []
    must_load = spec.get("must_load_skill")

    missing = [needle for needle in must_contain if needle not in response]
    if missing:
        r.must_contain_pass = 0.0
        r.failures.append(f"must_contain missed: {missing}")
    for needle in must_contain:
        if needle in response:
            r.details.append(f"PASS must_contain[{needle!r}]")

    banned = [needle for needle in must_not_contain if needle in response]
    if banned:
        r.must_not_contain_pass = 0.0
        r.failures.append(f"must_not_contain hit: {banned}")
    for needle in must_not_contain:
        if needle not in response:
            r.details.append(f"PASS must_not_contain[{needle!r}]")

    if must_load:
        ok = (skill_loaded == must_load)
        r.must_load_skill_pass = 1.0 if ok else 0.0
        if not ok:
            r.failures.append(f"must_load_skill={must_load!r} but loaded={skill_loaded!r}")

    return r


@dataclass
class SemanticScore:
    claim: str
    priority: int
    value: float           # 0..1
    rationale: str


_JUDGE_SYSTEM = (
    "You are a strict grader. The user will give you an agent's response and a single claim. "
    "Score the claim 0..1: 1.0 = clearly satisfied by the response, 0.0 = clearly violated, "
    "intermediate for partial. Reply with the score, then ' — ', then one short sentence rationale. "
    "Example: '0.7 — The response uses CreateMultiple but does not chunk.'"
)


_SCORE_RE = re.compile(r"^\s*([01](?:\.\d+)?)\s*[—\-:]\s*(.*)$", re.DOTALL)


def judge_semantic_claim(
    *,
    client,
    response: str,
    claim: str,
    deployment: str,
    priority: int = 2,
) -> SemanticScore:
    """Score a single claim against an agent response using an LLM judge.

    `client` is any object exposing `.chat(deployment, system, user) -> str`.
    See `judges_client.py` (Task 16 will add the real Azure OpenAI client).
    """
    user = f"AGENT RESPONSE:\n{response}\n\nCLAIM:\n{claim}"
    raw = client.chat(deployment=deployment, system=_JUDGE_SYSTEM, user=user)
    m = _SCORE_RE.match(raw.strip())
    if not m:
        return SemanticScore(claim=claim, priority=priority, value=0.0, rationale=raw.strip())
    try:
        value = max(0.0, min(1.0, float(m.group(1))))
    except ValueError:
        return SemanticScore(claim=claim, priority=priority, value=0.0, rationale=raw.strip())
    return SemanticScore(claim=claim, priority=priority, value=value, rationale=m.group(2).strip())


DEFAULT_P1_THRESHOLD = 0.7
DEFAULT_WEIGHTS = {"semantic": 0.5, "deterministic": 0.3, "live": 0.2}


def compose_reward(
    *,
    deterministic: DeterministicResult,
    semantic: list[SemanticScore],
    live_pass_rate: float | None,
    weights: dict[str, float] | None = None,
    p1_threshold: float = DEFAULT_P1_THRESHOLD,
) -> dict:
    """Compose hard (0/1) and soft (0..1) reward from the three signals.

    Returns dict with keys: hard, soft, det_breakdown, sem_breakdown, fail_reason.
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    # Renormalize when live is absent
    if live_pass_rate is None:
        w["live"] = 0.0
        s = w["semantic"] + w["deterministic"]
        if s > 0:
            w["semantic"] /= s
            w["deterministic"] /= s

    semantic_mean = sum(s.value for s in semantic) / len(semantic) if semantic else 1.0
    det_rate = deterministic.overall_pass_rate()
    live_rate = live_pass_rate if live_pass_rate is not None else 1.0

    soft = w["semantic"] * semantic_mean + w["deterministic"] * det_rate + w["live"] * live_rate

    p1_pass = all(s.value >= p1_threshold for s in semantic if s.priority == 1)
    det_pass = deterministic.all_priority_1_pass()
    live_ok = live_pass_rate is None or live_pass_rate == 1.0
    hard = int(p1_pass and det_pass and live_ok)

    fail_lines = []
    for f in deterministic.failures:
        fail_lines.append(f"DET FAIL: {f}")
    for s in semantic:
        if s.priority == 1 and s.value < p1_threshold:
            fail_lines.append(f"SEM FAIL P1 [{s.claim[:60]}]: score={s.value:.2f} — {s.rationale}")
    if live_pass_rate is not None and live_pass_rate < 1.0:
        fail_lines.append(f"LIVE FAIL: pass_rate={live_pass_rate:.2f}")

    return {
        "hard": hard,
        "soft": max(0.0, min(1.0, soft)),
        "det_breakdown": deterministic,
        "sem_breakdown": semantic,
        "fail_reason": "\n".join(fail_lines) if fail_lines else "",
    }
