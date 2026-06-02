"""Dataverse rollout — single-item execution + parallel batch with resume support.

Mirrors searchqa/rollout.py's structure. Response-only path (no live verification);
Task 19 will extend process_one for live items.

For each item:
  1. prepare_workspace() materializes the plugin tree with the in-progress
     SKILL.md swapped in for the target skill.
  2. run_target_exec() invokes Copilot CLI with --plugin-dir pointing at that tree.
  3. The agent's response goes through:
     - run_deterministic_checks() for must_contain / must_not_contain / must_load_skill
     - judge_semantic_claim() once per item.semantic[] claim
  4. compose_reward() produces hard/soft + structured fail_reason.
  5. The result dict uses ONLY existing SkillOpt result fields (id, hard, soft,
     response, predicted_answer, gold_answers, fail_reason, agent_ok, n_turns).
"""
from __future__ import annotations

import json
import os
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from skillopt.envs.dataverse.judges import (
    compose_reward,
    judge_semantic_claim,
    run_deterministic_checks,
)
from skillopt.envs.dataverse.judges_client import JudgeClient
from skillopt.envs.dataverse.schema import parse_item
from skillopt.model.copilot_harness import prepare_workspace, run_target_exec


def _detect_skill_loaded(response: str, target_skill: str) -> str | None:
    """Best-effort skill-load detection from the agent's response text.

    Copilot CLI doesn't emit a structured "skill loaded" event in -s mode.
    We approximate: if the response mentions the skill name in skill-name-like
    contexts, treat as loaded. This is a soft signal — the deterministic
    must_load_skill check uses it but it's not a hard guarantee.
    """
    if not target_skill:
        return None
    lo = response.lower()
    needles = [
        f"`{target_skill}`",
        f"**{target_skill}**",
        f"the {target_skill} skill",
        f"`{target_skill}` skill",
        f"loaded the {target_skill}",
        f"loaded skill: {target_skill}",
        f"using {target_skill}",
        f"used {target_skill}",
        f"with the {target_skill}",
        f"per the {target_skill}",
        f"from the {target_skill}",
        f"{target_skill}/SKILL.md",
        f"{target_skill}/skill.md",
    ]
    for n in needles:
        if n.lower() in lo:
            return target_skill
    return None


def _build_prompt(item, target_skill_name: str) -> str:
    """Build the prompt sent to Copilot via -p.

    Tells the agent it's running with the dataverse plugin loaded and gives it
    the item's prompt. Keeps the framing minimal — the skill itself contains
    all the domain guidance.
    """
    return (
        f"You are running with the dataverse plugin installed. "
        f"Use the {target_skill_name} skill to answer the following request. "
        f"Provide a concrete code/script response, citing which skill you used.\n\n"
        f"---\n\n{item.prompt}"
    )


def _resolve_plugin_tree(plugin_src_dir: str, target_skill_name: str) -> str:
    """Resolve the plugin manifest tree (containing ``skills/``) from a path that
    may be either a Dataverse-skills repo root or the plugin tree itself.

    The config/env convention (DATAVERSE_PLUGIN_SRC) points at the repo root, whose
    plugin tree lives at ``.github/plugins/dataverse``. ``prepare_workspace`` however
    expects the tree directly. Prefer the path that contains the target skill.
    """
    direct = os.path.join(plugin_src_dir, "skills", target_skill_name, "SKILL.md")
    if os.path.isfile(direct):
        return plugin_src_dir
    nested = os.path.join(plugin_src_dir, ".github", "plugins", "dataverse")
    if os.path.isfile(os.path.join(nested, "skills", target_skill_name, "SKILL.md")):
        return nested
    return plugin_src_dir


def process_one(
    item_raw: dict,
    out_root: str,
    skill_content: str,
    *,
    plugin_src_dir: str,
    target_skill_name: str,
    judge_client: JudgeClient,
    judge_deployment: str,
    exec_timeout: int = 240,
    max_completion_tokens: int = 16384,
    live_enabled: bool = True,
    live_client_factory=None,
) -> dict:
    """Process a single eval item. Returns a SkillOpt result dict."""
    try:
        item = parse_item(item_raw)
    except Exception as e:
        return {
            "id": str(item_raw.get("id", "<unknown>")),
            "hard": 0,
            "soft": 0.0,
            "response": "",
            "predicted_answer": "",
            "gold_answers": [],
            "fail_reason": f"schema error: {type(e).__name__}: {e}",
            "agent_ok": False,
            "n_turns": 0,
        }

    item_id = item.id
    pred_dir = os.path.join(out_root, "predictions", item_id)
    os.makedirs(pred_dir, exist_ok=True)

    result = {
        "id": item_id,
        "question": item.prompt,
        "hard": 0,
        "soft": 0.0,
        "predicted_answer": "",
        "gold_answers": [s.claim for s in item.semantic],
        "response": "",
        "fail_reason": "",
        "agent_ok": False,
        "n_turns": 0,
    }

    try:
        # Determine whether live tracing is active for this item (single-run path).
        is_live = bool(item.live and item.live.enabled and live_enabled)

        trace_path = ""
        guids_path = ""
        extra_env: dict[str, str] = {}

        if is_live:
            from skillopt.envs.dataverse.live import trace_paths_for
            trace_path, guids_path = trace_paths_for(out_root, item_id)
            # Pre-create the files so auth.py's hook can append without racing.
            open(trace_path, "w", encoding="utf-8").close()
            open(guids_path, "w", encoding="utf-8").close()
            extra_env["DATAVERSE_TRACE_FILE"] = trace_path

        work_dir = os.path.join(pred_dir, "workspace")
        prepare_workspace(
            work_dir=work_dir,
            skill_md=skill_content,
            task_text=item.prompt,
            plugin_src_dir=_resolve_plugin_tree(plugin_src_dir, target_skill_name),
            target_skill_name=target_skill_name,
        )

        prompt = _build_prompt(item, target_skill_name)
        final, raw = run_target_exec(
            work_dir=work_dir,
            prompt=prompt,
            model="",   # uses COPILOT_CLI_EXEC_MODEL default
            timeout=exec_timeout,
            extra_env=extra_env if extra_env else None,
        )

        with open(os.path.join(pred_dir, "response.txt"), "w", encoding="utf-8") as f:
            f.write(final or raw)

        # Also emit conversation.json so the reflect/analyst stage can read the
        # agent's trajectory. The analyst's fmt_minibatch_trajectories looks for
        # conversation.json per prediction and skips any item that lacks it; the
        # dataverse agent is single-turn, so we record its prompt + response.
        with open(os.path.join(pred_dir, "conversation.json"), "w", encoding="utf-8") as f:
            json.dump(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": final or raw},
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )

        result["response"] = final or raw
        result["agent_ok"] = bool(final and not final.startswith("[TIMEOUT"))
        result["n_turns"] = 1

        skill_loaded = _detect_skill_loaded(result["response"], target_skill_name)

        det = run_deterministic_checks(
            response=result["response"],
            spec=item.deterministic,
            skill_loaded=skill_loaded,
        )

        sem_scores = []
        for claim in item.semantic:
            try:
                score = judge_semantic_claim(
                    client=judge_client,
                    response=result["response"],
                    claim=claim.claim,
                    deployment=judge_deployment,
                    priority=claim.priority,
                )
            except Exception as e:
                # Judge call failed (transient API error etc.) — record a 0.0
                # but don't crash the whole rollout.
                from skillopt.envs.dataverse.judges import SemanticScore
                score = SemanticScore(
                    claim=claim.claim,
                    priority=claim.priority,
                    value=0.0,
                    rationale=f"judge error: {type(e).__name__}: {e}",
                )
            sem_scores.append(score)

        live_pass_rate = None
        created_guids: list[dict] = []

        if is_live:
            from skillopt.envs.dataverse.live import (
                read_jsonl,
                run_verify,
                teardown as live_teardown,
            )

            trace_records = read_jsonl(trace_path)
            created_guids = read_jsonl(guids_path)

            if live_client_factory is None:
                # No client wired — only request_count checks (trace-only) can run.
                result["fail_reason"] = (
                    "[WARN] live_client_factory not provided; "
                    "skipping row_count checks but running request_count checks."
                )
                dataverse_client = None
            else:
                try:
                    dataverse_client = live_client_factory()
                except Exception as e:
                    result["fail_reason"] = f"live client error: {type(e).__name__}: {e}"
                    dataverse_client = None

            if dataverse_client is not None or all(
                c.get("kind") != "row_count" for c in item.live.verify
            ):
                live_result = run_verify(item.live.verify, trace_records, dataverse_client)
                live_pass_rate = live_result.pass_rate
            else:
                live_pass_rate = 0.0

            # Teardown — delete every GUID the agent created, log orphans.
            if dataverse_client is not None and item.live.teardown == "delete_session_records":
                orphans_path = os.path.join(out_root, "orphans.jsonl")
                live_teardown(created_guids, dataverse_client, orphans_path)

        reward = compose_reward(
            deterministic=det,
            semantic=sem_scores,
            live_pass_rate=live_pass_rate,
        )

        result["hard"] = reward["hard"]
        result["soft"] = reward["soft"]
        if reward["fail_reason"]:
            existing = result["fail_reason"]
            result["fail_reason"] = (existing + "\n" + reward["fail_reason"]).strip()

        # predicted_answer = last non-empty line of the response, capped at 200 chars
        lines = [ln.strip() for ln in result["response"].splitlines() if ln.strip()]
        result["predicted_answer"] = (lines[-1] if lines else "")[:200]

        # Per-item artifacts
        with open(os.path.join(pred_dir, "judge_rationales.jsonl"), "w", encoding="utf-8") as f:
            for s in sem_scores:
                f.write(
                    json.dumps({
                        "claim": s.claim,
                        "priority": s.priority,
                        "score": s.value,
                        "rationale": s.rationale,
                    })
                    + "\n"
                )

        with open(os.path.join(pred_dir, "verify_results.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "deterministic": {
                        "must_contain_pass": det.must_contain_pass,
                        "must_not_contain_pass": det.must_not_contain_pass,
                        "must_load_skill_pass": det.must_load_skill_pass,
                        "details": det.details,
                        "failures": det.failures,
                    },
                    "live_pass_rate": live_pass_rate,
                },
                f,
                indent=2,
            )

    except Exception as e:
        result["fail_reason"] = f"error: {type(e).__name__}: {e}\n{traceback.format_exc()}"

    return result


def run_batch(
    items: list[dict],
    out_root: str,
    skill_content: str,
    *,
    plugin_src_dir: str,
    target_skill_name: str,
    judge_client: JudgeClient,
    judge_deployment: str,
    workers: int = 24,
    exec_timeout: int = 240,
    max_completion_tokens: int = 16384,
    task_timeout: int = 600,
    live_enabled: bool = True,
    live_client_factory=None,
) -> list[dict]:
    """Run items in parallel with resume support, mirroring searchqa.run_batch."""
    task_timeout = max(int(task_timeout), int(exec_timeout) + 60)
    results_path = os.path.join(out_root, "results.jsonl")
    os.makedirs(out_root, exist_ok=True)

    done_ids: set[str] = set()
    existing: list[dict] = []
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done_ids.add(str(r["id"]))
                    existing.append(r)
                except Exception:
                    pass

    pending = [it for it in items if str(it.get("id")) not in done_ids]
    if not pending:
        return existing

    total = len(existing) + len(pending)
    completed = len(existing)
    correct = sum(1 for r in existing if r.get("hard", 0))
    if existing:
        print(f"    [rollout] resuming: {completed}/{total} already done", flush=True)

    results = list(existing)

    def _run_one(it):
        return process_one(
            it,
            out_root,
            skill_content,
            plugin_src_dir=plugin_src_dir,
            target_skill_name=target_skill_name,
            judge_client=judge_client,
            judge_deployment=judge_deployment,
            exec_timeout=exec_timeout,
            max_completion_tokens=max_completion_tokens,
            live_enabled=live_enabled,
            live_client_factory=live_client_factory,
        )

    with open(results_path, "a", encoding="utf-8") as outf:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_one, it): it for it in pending}
            for fut in list(futs):
                it = futs[fut]
                try:
                    res = fut.result(timeout=task_timeout)
                except Exception as exc:
                    res = {
                        "id": str(it.get("id", "<unknown>")),
                        "hard": 0,
                        "soft": 0.0,
                        "response": "",
                        "predicted_answer": "",
                        "gold_answers": [],
                        "fail_reason": f"task error/timeout: {type(exc).__name__}: {exc}",
                        "agent_ok": False,
                        "n_turns": 0,
                    }
                results.append(res)
                completed += 1
                if res.get("hard", 0):
                    correct += 1
                acc = correct / completed if completed else 0.0
                print(
                    f"    [rollout] {completed}/{total} (acc={acc:.3f}) "
                    f"id={res['id']} hard={res.get('hard', '?')}",
                    flush=True,
                )
                outf.write(json.dumps(res, ensure_ascii=False) + "\n")
                outf.flush()
    return results
