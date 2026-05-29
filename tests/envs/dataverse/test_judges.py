"""Tests for deterministic checks and reward composition."""
from skillopt.envs.dataverse.judges import (
    run_deterministic_checks,
    DeterministicResult,
)


def test_must_contain_pass():
    r = run_deterministic_checks(
        response="The agent calls CreateMultiple with a list.",
        spec={"must_contain": ["CreateMultiple"]},
        skill_loaded="dv-data",
    )
    assert r.must_contain_pass == 1.0
    assert r.must_not_contain_pass == 1.0
    assert r.must_load_skill_pass is None


def test_must_contain_fail():
    r = run_deterministic_checks(
        response="The agent loops per record.",
        spec={"must_contain": ["CreateMultiple"]},
        skill_loaded="dv-data",
    )
    assert r.must_contain_pass == 0.0
    assert "CreateMultiple" in r.failures[0]


def test_must_not_contain_fail():
    r = run_deterministic_checks(
        response="There is no batch API.",
        spec={"must_not_contain": ["There is no batch API"]},
        skill_loaded="dv-data",
    )
    assert r.must_not_contain_pass == 0.0


def test_must_load_skill():
    r = run_deterministic_checks(
        response="ok",
        spec={"must_load_skill": "dv-data"},
        skill_loaded="dv-data",
    )
    assert r.must_load_skill_pass == 1.0
    r2 = run_deterministic_checks(
        response="ok",
        spec={"must_load_skill": "dv-data"},
        skill_loaded=None,
    )
    assert r2.must_load_skill_pass == 0.0


def test_overall_pass_rate():
    r = run_deterministic_checks(
        response="CreateMultiple is used. one HTTP call per record",
        spec={
            "must_contain": ["CreateMultiple"],
            "must_not_contain": ["one HTTP call per record"],
        },
        skill_loaded="dv-data",
    )
    # 1 pass + 1 fail = 0.5 overall
    assert r.overall_pass_rate() == 0.5
    assert r.all_priority_1_pass() is False


from unittest.mock import MagicMock

from skillopt.envs.dataverse.judges import (
    judge_semantic_claim,
    SemanticScore,
)


def test_judge_semantic_claim_parses_score():
    fake_client = MagicMock()
    fake_client.chat.return_value = "0.85 — The agent correctly used CreateMultiple."
    score = judge_semantic_claim(
        client=fake_client,
        response="agent used CreateMultiple",
        claim="agent uses the bulk form",
        deployment="gpt-5.4-mini",
    )
    assert isinstance(score, SemanticScore)
    assert 0.84 <= score.value <= 0.86
    assert "CreateMultiple" in score.rationale


def test_judge_semantic_claim_handles_malformed_response():
    fake_client = MagicMock()
    fake_client.chat.return_value = "I'm not sure how to rate this."
    score = judge_semantic_claim(
        client=fake_client,
        response="x",
        claim="y",
        deployment="gpt-5.4-mini",
    )
    # Malformed → score 0.0, full text saved as rationale.
    assert score.value == 0.0
    assert "not sure" in score.rationale.lower()
