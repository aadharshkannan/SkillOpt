# Dataverse Skills × SkillOpt — Design

**Date:** 2026-05-28
**Status:** Approved (pending implementation plan)
**Owner:** aadkannan@microsoft.com

## Context

The [Dataverse-skills plugin](https://github.com/microsoft/Dataverse-skills) ships eight skill documents (`dv-overview`, `dv-connect`, `dv-data`, `dv-query`, `dv-metadata`, `dv-solution`, `dv-admin`, `dv-security`) that teach AI coding agents how to drive Microsoft Dataverse. The repo currently has:

- One `.biceval.json` file (`evals/tests/dv_data.biceval.json`) with **3 eval items** covering only `dv-data`.
- A `static_checks.py` suite that validates skill *structure* (frontmatter, token budget, cross-references) but not skill *content quality*.
- No live-execution evals; no reward function suitable for skill-document optimization.

[SkillOpt](https://github.com/microsoft/SkillOpt) is a skill-document optimization training loop — it treats a Markdown skill as "weights" and uses rollouts + reflection to iteratively edit the skill, with a validation gate accepting or rejecting each update. SkillOpt needs a per-env adapter that produces `hard` and `soft` scores for each item.

This spec describes how to bring the two together: author meaningful evals across all eight Dataverse skills, build a SkillOpt environment whose reward function is those evals, and run the optimization loop against the user's Aurora Dataverse environment (`https://aurorabapenvb31ab.crm10.dynamics.com/`).

## Goals

- One SkillOpt environment per Dataverse skill (eight envs, all driven by the same `DataverseSkillAdapter` module parametrized by `skill_name`).
- ~15-25 hand-authored eval items per skill (~150 total), grounded in documented skill content — no invented criteria.
- A hybrid reward function: most items are response-judged (LLM judge + deterministic substring checks), ~5-10 per skill are live-verified against a real Dataverse environment.
- The Dataverse-skills repo remains the source of truth for `SKILL.md` content. SkillOpt trains on a working copy; the best skill from a run is PR'd back manually.
- Reuse SkillOpt's existing fields, base classes, and reflect pipeline. The only schema addition to SkillOpt is a new `judge` model role that follows the exact same pattern as the existing `optimizer` and `target` roles (own backend, endpoint, auth mode, AD scope, API version, managed-identity client id).

## Non-goals

- Optimizing multiple skills in one training run. Each training run targets exactly one skill.
- Editing `references/*.md` files. Only the top-level `SKILL.md` for the configured skill is edited; everything else is frozen.
- Replacing `static_checks.py` or `LocalEvalRunner`. Both keep running in Dataverse-skills CI. The SkillOpt eval set is additive.
- Automatic PR-back to Dataverse-skills. The `promote` script opens a draft PR; humans review and merge.
- Optimizing `dv-connect` or `dv-overview` in v1. Both are orchestration/setup skills; their content is largely tool-routing tables rather than executable patterns. They get the env scaffolding and a small eval set (~5 items each, all response-only — no live block) but are deprioritized in the rollout order.

## Architecture

Three repos are involved.

```
Dataverse-skills/                  ← source of truth for SKILL.md
  .github/plugins/dataverse/skills/<skill>/SKILL.md
  evals/skillopt/                  ← NEW — eval items authored next to skills
    dv_data.jsonl
    dv_query.jsonl
    ...

SkillOpt/                          ← env adapter + training loop
  skillopt/envs/dataverse/         ← NEW — one adapter module, parametrized by skill
    adapter.py                     ← DataverseSkillAdapter(skill_name=...)
    rollout.py                     ← claude_code_exec wrapper + plugin install
    judges.py                      ← LLM judge + deterministic checks
    live.py                        ← live verification helpers (proxy, teardown)
    dataloader.py
    prompts/
      analyst_error.md
      analyst_success.md
    scripts/
      setup_live_env.py            ← one-time Dataverse env prep
      migrate_biceval.py           ← biceval.json → jsonl converter
      split.py                     ← train/val/test split builder
      promote.py                   ← open draft PR back to Dataverse-skills
      sweep_orphans.py             ← clean up leaked live-test GUIDs
  configs/dataverse/               ← NEW — 8 skill configs + 1 shared base
    _base.yaml
    dv_data.yaml ... dv_security.yaml
  data/dataverse/                  ← NEW — pre-split data
    dv_data/{train,val,test}/items.json
    ...

Aurora Dataverse env               ← live verification target
  Solution: SkillOptEvals (publisher prefix: sko)
  Pre-seeded reference data under sko_* tables
```

### Flow per training step

1. Loader pulls a batch of items for the configured skill (e.g., `dv_data`).
2. For each item, rollout:
   - Materializes a *working copy* of the plugin under `outputs/<run>/plugin/`, with the current `SKILL.md` swapped in for the target skill; other skills frozen at their committed version (the agent loads the whole plugin).
   - For live items: spawns a per-item HTTP proxy on a free localhost port.
   - Invokes `claude_code_exec` with the working-copy plugin manifest, the item's `prompt`, and `DATAVERSE_HTTP_PROXY` env var.
   - Captures the final response, any tool calls, and (for live items) the proxy's recorded trace + created-GUID list.
3. Judge scores the response: deterministic checks + per-claim LLM judge calls.
4. For live items: adapter runs verification queries against the Dataverse env, then teardown via the SDK.
5. Reflect runs as normal on failures → optimizer proposes patches → patches are scope-filtered to the target skill only → applied to the working-copy `SKILL.md`.
6. Gate accepts/rejects on the val split. Resume-aware via SkillOpt's existing `results.jsonl` append pattern.
7. At end of run: `outputs/<run>/best_skill.md` is the trained artifact. `promote.py` opens a draft PR into Dataverse-skills.

## Eval Item Format

One JSONL file per skill at `Dataverse-skills/evals/skillopt/<skill>.jsonl`. Each line:

```jsonc
{
  "id": "dv_data_002_bulk_create",
  "skill": "dv-data",
  "category": "antipattern_trap",     // happy_path | skill_contract | antipattern_trap | edge_case | cross_tool
  "prompt": "Write a Python script that efficiently creates 500 ticket records...",
  "expected_summary": "Agent uses CreateMultiple — one HTTP call per chunk, NOT a per-record for-loop.",

  // Deterministic substring checks. P1 fail forces hard=0.
  "deterministic": {
    "must_contain": ["new_ticket"],
    "must_not_contain": [
      "There is no batch API",
      "one HTTP call per record",
      "no chunking helper"
    ],
    "must_load_skill": "dv-data"      // verified via plugin-load sidecar log
  },

  // LLM-judged plain-English claims. P1 below threshold forces hard=0.
  "semantic": [
    {
      "claim": "Agent uses the bulk/list form of client.records.create — passes a Python list as the records arg. Per-record for-loops must FAIL.",
      "priority": 1
    },
    {
      "claim": "Agent mentions CreateMultiple, batch semantics, or bulk_create helpers from the dv-data skill.",
      "priority": 2
    }
  ],

  // Optional. Only set for live-verified items.
  "live": {
    "enabled": true,
    "setup": {
      "ensure_table": "sko_eval_ticket",
      "seed": []
    },
    "verify": [
      {"kind": "row_count", "table": "sko_eval_ticket", "filter": "createdon ge @session_start", "expected_min": 500, "expected_max": 500},
      {"kind": "request_count", "endpoint_regex": "/CreateMultiple", "expected_max": 1}
    ],
    "teardown": "delete_session_records"
  },

  "tags": {"suite": "regression", "domain": "data"}
}
```

### Reward function

```
deterministic_pass_rate = passed_checks / total_checks
semantic_scores         = [judge_score(response, claim) for claim in item.semantic]
live_pass_rate          = live_verify(item.live, session_log) if item.live.enabled else None

soft = weighted_mean(
  semantic_mean    * w_semantic,        # default 0.5
  det_pass_rate    * w_deterministic,   # default 0.3
  live_pass_rate   * w_live,            # default 0.2 if live, else weights renormalize
)

hard = int(
  all(P1 semantic scores >= P1_threshold) and
  all(must_contain hit) and
  not any(must_not_contain hit) and
  (live_pass_rate == 1.0 or not item.live.enabled)
)
```

Weights live in `configs/dataverse/_base.yaml`, not hardcoded.

### Scoring fields used on the SkillOpt result dict

Only existing SkillOpt result fields — no new schema additions.

| Field            | Use                                                        |
|------------------|------------------------------------------------------------|
| `id`             | item key                                                   |
| `hard`           | 0/1 — gate accept/reject                                   |
| `soft`           | 0..1 — analyst grading + ranking                           |
| `response`       | full agent transcript                                      |
| `predicted_answer` | extracted final answer (same extraction as SearchQA)    |
| `fail_reason`    | structured failure block fed to analyst (format below)     |
| `agent_ok`       | did `claude_code_exec` finish without crashing             |
| `n_turns`        | turn count                                                 |
| `gold_answers`   | repurposed: list of the item's `semantic[].claim` strings  |

Dataverse-specific artifacts (deterministic check breakdown, judge rationales, live verify results, request trace) are written to `predictions/<id>/*` files, not into the SkillOpt result dict.

### `fail_reason` format (fed to analyst)

```
[ITEM dv_data_002_bulk_create] hard=0 soft=0.21

DETERMINISTIC:
  PASS  must_contain[new_ticket]
  FAIL  must_not_contain[one HTTP call per record]
        → match at response char 482: "...so we do one HTTP call per record..."
  FAIL  must_not_contain[There is no batch API]

SEMANTIC:
  FAIL P1  "Agent uses the bulk/list form of client.records.create..."
           judge=0.15 — Agent wrote a for-loop calling client.records.create(table, single_dict)
                       on each iteration. No list-form call observed.

LIVE:
  FAIL request_count[/CreateMultiple] expected_max=1, observed=0
  FAIL request_count[/v9.2/new_ticket] expected_max=1, observed=500
```

## Adapter & Reward Function

### `DataverseSkillAdapter`

Subclasses `EnvAdapter` (same base as SearchQA). Constructor knobs:

```python
class DataverseSkillAdapter(EnvAdapter):
    def __init__(
        self,
        skill_name: str,                  # which of the 8 skills this env optimizes
        plugin_src_dir: str,              # path to local Dataverse-skills clone
        items_dir: str,                   # data/dataverse/<skill>/{train,val,test}
        workers: int = 24,                # response-only worker pool
        live_workers: int = 4,            # live-item worker pool
        analyst_workers: int = 16,
        failure_only: bool = False,
        minibatch_size: int = 8,
        edit_budget: int = 4,
        exec_timeout: int = 180,
        max_completion_tokens: int = 16384,
        # ... plus split_mode, split_dir, seed, limit per SearchQA
        live_enabled: bool = True,
        live_env_url: str = "",
        live_solution: str = "SkillOptEvals",
        live_prefix: str = "sko",
        proxy_port_range: tuple[int, int] = (45000, 46000),
        record_bodies: bool = False,      # full HTTP bodies in trace, off by default
    ):
        ...
```

Uses existing `SplitDataLoader` base; `load_raw_items(path)` parses the JSONL.

`rollout`, `reflect`, `build_train_env`, `build_eval_env` signatures match SearchQA exactly. `reflect` delegates to `run_minibatch_reflect` with `error_system` and `success_system` loaded from the adapter's `prompts/` directory.

### Judge

One LLM call per `semantic[].claim`, parallel within an item. Judge prompt is a stripped-down version of the existing biceval `correctness.prompty` pattern: "Here is the agent's response. Here is one claim. Score 0..1 and give a one-sentence rationale." Rationales go to `predictions/<id>/judge_rationales.jsonl`.

Judge is its own model role with its own backend, endpoint, auth — same config pattern as `optimizer` and `target`. No caching in v1 (judge bandwidth is sufficient). The judge role is **required** for this env; there is no fallback to the optimizer model if `judge_*` config is missing — the adapter fails fast at `setup()` with a clear error pointing at the missing `.env` keys.

## Live Verification Subsystem

### One-time setup (`setup_live_env.py`)

Runs once per Dataverse env before training. Idempotent. Creates the `SkillOptEvals` solution + `sko` publisher, pre-seeds reference data (~500 accounts, ~1000 contacts under `sko_*` prefix). Documented in adapter README; training assumes setup has completed.

### Per-rollout auth

Reuses the Dataverse-skills `scripts/auth.py` pattern. Adapter loads credentials once at `setup()`, caches a `DataverseClient` per worker thread.

### Agent-action observability

Per-item HTTP proxy on a free localhost port. Injected as `DATAVERSE_HTTP_PROXY` into the `claude_code_exec` subprocess environment. The proxy:

- Forwards every request to the real Dataverse env (full pass-through, no MITM).
- Records `{method, endpoint, status, request_body_hash, response_summary}` per call to `predictions/<id>/dataverse_trace.jsonl`.
- Extracts created-record GUIDs from response bodies → `predictions/<id>/created_guids.jsonl`.

Body recording is metadata + body hash by default; full bodies behind `record_bodies: true` for diagnostic runs (significant disk cost — a single bulk import can write 50MB).

`live.verify` checks:
- `row_count` → adapter queries Dataverse directly.
- `request_count` → adapter reads `dataverse_trace.jsonl`.
- Future verify kinds (e.g., `field_value_equals`, `record_exists`, `solution_contains`) added on demand; not pre-built.

### Teardown

After grading, adapter reads `created_guids.jsonl` and issues bulk delete via the SDK. Failures → `outputs/<run>/orphans.jsonl`. `sweep_orphans.py` runs implicitly at the start of every training run (cheap query for `sko_*` records older than the current session). A crashed run leaves orphans; the sweep recovers them.

### Parallelism

Two worker pools per env: `workers` (default 24) for response-only items, `live_workers` (default 4) for live items. Response-only items run first at high concurrency; live items run after at low concurrency. Both pools resume through SkillOpt's existing `results.jsonl` append pattern.

## Reflect / Optimizer Integration

`run_minibatch_reflect` reused verbatim. Dataverse-specific work is in two places.

### Analyst prompts

`prompts/analyst_error.md` and `prompts/analyst_success.md` — same Markdown template shape as SearchQA, same variables (`{skill_content}`, `{minibatch_results}`, `{step_buffer_context}`, `{meta_skill_context}`). Differences are only in instructions:

**`analyst_error.md` priors (guidance, not rules):**
- Prefer additive edits to existing sections over wholesale rewrites.
- Antipattern failures (request_count traps) → add a "Don't do this" example block with a `WRONG:` label, matching the skill's existing voice.
- Field-casing failures → tighten the casing table or add a row.
- Skill-contract failures → trace which line led to the wrong paraphrase and patch that line specifically.
- Do not edit other skills' content. If a fix logically belongs in `dv-overview`, note it in patch metadata — the loop won't apply it, but the meta-skill memory will pick it up.

**`analyst_success.md`** — invoked on items that passed the gate. Used by SkillOpt's slow-update mechanism for longitudinal comparison.

### Patch-scope guardrail

Optimizer patches can theoretically target any file. We constrain to the single configured skill's `SKILL.md` (not `references/*.md` either, in v1). Rejected patches go to `outputs/<run>/steps/step_NNNN/out_of_scope_patches.jsonl`. This guardrail is what makes per-skill env granularity actually work — without it, a `dv-data` training run could quietly drift `dv-overview`.

**Decision:** cross-skill edits are blocked, not just warned. The trade-off (slower convergence on cross-skill issues) is acceptable in v1; revisit after the first few runs.

### Meta-skill / slow-update

No Dataverse-specific changes. Both mechanisms operate on skill-document deltas; they work once `analyst_success.md` exists.

## Configs & Run Flow

### Config layout

`configs/dataverse/_base.yaml` defines everything common: judge model role, live block defaults, proxy port range, weight constants. Each skill config inherits via `_base_:`:

```yaml
# configs/dataverse/dv_data.yaml
_base_: ['_base.yaml', '../_base_/default.yaml']

env:
  name: dataverse
  skill_name: dv-data
  skill_init: ../../Dataverse-skills/.github/plugins/dataverse/skills/dv-data/SKILL.md
  split_dir: data/dataverse/dv_data
  workers: 24
  live_workers: 4

train:
  train_size: 0
  batch_size: 8
```

The 8 configs are nearly identical — only `skill_name`, `skill_init`, and `split_dir` differ. Other knobs inherit from `_base_/default.yaml` so they stay tuned alongside the rest of SkillOpt.

### `.env` additions

```
JUDGE_AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
JUDGE_AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini
JUDGE_AZURE_OPENAI_AUTH_MODE=azure_cli

DATAVERSE_ENV_URL=https://aurorabapenvb31ab.crm10.dynamics.com/
DATAVERSE_PLUGIN_SRC=C:/Users/aadkannan/source/repos/Dataverse-skills
DATAVERSE_AUTH_MODE=interactive
SKILLOPT_EVALS_SOLUTION=SkillOptEvals
SKILLOPT_EVALS_PREFIX=sko
```

### Three-model role split

`optimizer`, `target`, and `judge` are each independent SkillOpt roles with their own backend, endpoint, auth mode, AD scope, API version, and managed-identity client id — same pattern that already exists for optimizer/target. Judge is added as the third role following the same pattern.

`claude_code_exec` as the `target_backend` uses the local Claude CLI's own auth (`claude login` or `ANTHROPIC_API_KEY`), not an Azure endpoint. `claude_code_exec_*` settings configure how to invoke the binary, not which API endpoint it calls. Multiple CC profiles via `claude_code_exec_profile` allow different models/effort for different roles.

### Run flow

```
Phase 0 (once per Dataverse env): SETUP
  python -m skillopt.envs.dataverse.scripts.setup_live_env

Phase 1 (once per skill): MIGRATE EVALS + SPLIT
  python -m skillopt.envs.dataverse.scripts.migrate_biceval \
    --in ../Dataverse-skills/evals/tests/dv_data.biceval.json \
    --out ../Dataverse-skills/evals/skillopt/dv_data.jsonl

  # author additional ~12-22 items by hand (see Eval Authoring section)

  python -m skillopt.envs.dataverse.scripts.split \
    --in ../Dataverse-skills/evals/skillopt/dv_data.jsonl \
    --out data/dataverse/dv_data \
    --ratio 80:10:10 --seed 42

Phase 2 (per training run): TRAIN
  python scripts/train.py \
    --config configs/dataverse/dv_data.yaml \
    --out_root outputs/dv_data_run_01

Phase 3 (per run, optional): PROMOTE
  python -m skillopt.envs.dataverse.scripts.promote \
    --run outputs/dv_data_run_01 \
    --plugin ../Dataverse-skills/.github/plugins/dataverse/skills/dv-data
```

`scripts/eval_only.py` works unchanged — point it at any committed `SKILL.md` for held-out test scores. Suitable as a Dataverse-skills CI hook (PR check that posts score delta).

## Eval Authoring Methodology

Target: 15-25 items per skill (~150 total), hand-authored from documented sources.

### Source 1: mine `SKILL.md` directly (~60% of items)

| In the skill doc | Becomes |
|---|---|
| A code example block | `happy_path` |
| A `WRONG:` or `❌` example | `antipattern_trap` |
| A Hard Rule | `antipattern_trap` (prompt that tempts violation) |
| A casing table | `edge_case` |
| A "Skill boundaries" cross-reference | `cross_tool` |

### Source 2: mine `static_checks.py` invariants (~25% of items)

Each CAT rule encodes a documented invariant. Promote each to a runtime eval — the static check catches the rule in the *skill text*, the runtime eval catches whether the agent *applies* the rule.

| Static check | Runtime eval pattern |
|---|---|
| `EVAL-PY-01` | "Write a script that authenticates" → assert correct `sys.path.insert` ordering |
| `EVAL-AUTH-01` | "Quote the auth import you'd use" → assert clean import |
| `EVAL-PY-05` | "Write a bulk create" → assert no `get_token()` near `DataverseClient` |
| `EVAL-COMPLETE-04` | "Which skill handles record CRUD?" → assert `dv-data`, not `dv-python-sdk` |

### Source 3: mine `references/*.md` (~15% of items)

Detail content the skill body doesn't repeat — Level-3 on-demand content. Agent must know when to consult it. Pattern: prompt for the detail-file topic, assert agent's answer matches the detail file specifically.

### Skill-contract slot (one per skill)

Borrowed from `data_003_skill_contract`. Asks the agent to *report what the loaded skill teaches* about a topic, before writing code. Catches reward-hacking regressions where SkillOpt edits the skill in a way that semantically inverts a rule — the static checks still pass but the agent learns wrong content.

### Live-verified subset (~5-10 per skill, topic-driven)

Pick items where the side effect is the thing being tested:

- `dv-data`: bulk-create single-API-call check, upsert idempotency, lookup resolution, FK-ordered import.
- `dv-query`: row count after a known-result query, `$apply` aggregation against seeded data.
- `dv-metadata`: table created with right alternate key, column casing matches.
- `dv-solution`: publisher gets right prefix, export produces expected components.
- `dv-admin`: allowlist setting actually applied (read it back), denylist setting refused.
- `dv-security`: role assignment present, scope correct.
- `dv-connect` / `dv-overview`: mostly response-only; live items sparse.

### Noise-floor check (one-time per skill, ~30 min)

Before declaring a skill's eval set ready:

1. Run `eval_only.py` against the **current committed skill** → record baseline.
2. Run against an **intentionally regressed copy** (delete one Hard Rule, or rewrite the bulk-create section to teach per-record loops).
3. Score gap must be ≥ 0.15 on `soft` AND ≥ 2 items flipping `hard`. If not, add traps until it does.
4. Run against a **trivially regressed copy** (typo fix, whitespace). Score must not move. If it does, replace flaky items.

Catches insensitive sets and flaky sets in both directions. Single highest-ROI step before training.

### Effort estimate

Per skill: ~1 day to draft 15-25 items from sources 1-3, ~half day for live-item setup and noise-floor check. Across 8 skills: ~10-12 person-days. The 3 existing `dv_data` biceval items migrate automatically; everything else hand-authored.

## Decisions Log

Explicit choices pinned via brainstorming Q&A, in the order they were made:

| # | Decision | Why |
|---|---|---|
| 1 | Hybrid reward (response-judged + ~5-10 live items per skill) | Tight reward signal for most batches, ground-truth for the critical traps. Pure response-only misses runtime bugs; pure live is too slow and side-effect-heavy. |
| 2 | One env per skill, eight training jobs | Clean attribution. A dv-query regression never confuses a dv-data loop. Per-skill batch sizes and learning rates. |
| 3 | `claude_code_exec` target backend with plugin loaded | Matches production setup. Exercises plugin loader, frontmatter routing, references/ on-demand loading. |
| 4 | Dedicated `SkillOptEvals` solution + `sko_` prefix + per-item teardown | Predictable isolation, easy to nuke, never touches production tables. One-time setup overhead. |
| 5 | ~15-25 items per skill, ~150 total, 80/10/10 split | Enough signal for batch_size=8 minibatches; small enough to hand-author from documented sources. |
| 6 | Train on a working copy in `outputs/`, PR final into Dataverse-skills | Dataverse-skills repo stays source of truth. Crashed runs don't leave the file mid-edit. Parallel runs possible. |
| 7 | Judge is a third independent model role | `gpt-5.4-mini` or nano via dedicated endpoint. Cheap enough not to need caching in v1. |
| 8 | Patch-scope guardrail: only the configured skill's top-level `SKILL.md` | Per-skill granularity actually works. Cross-skill issues logged but not applied. |
| 9 | HTTP-proxy observability for live items, metadata + body hash by default | Agent never knows it's being observed. Full bodies behind a flag for diagnostic runs. |
| 10 | Response-only and live items use separate worker pools | Response items high concurrency, live items low concurrency to respect Dataverse throttling. |

## Open Questions / Deferred

- **Judge call caching.** Skipped in v1 (high judge bandwidth). Revisit if a run's judge cost exceeds expectations.
- **Cross-skill edit application.** Currently blocked. After 3-5 training runs, review `out_of_scope_patches.jsonl` and decide whether a softer rule (apply if confidence ≥ threshold) is warranted.
- **`dv-overview` / `dv-connect` optimization depth.** Both deprioritized in v1 — small eval sets, mostly response-only. Decide post-v1 whether they warrant full-scale evals.
- **Additional `live.verify` kinds.** Only `row_count` and `request_count` pre-built. Add `field_value_equals`, `record_exists`, `solution_contains` etc. as specific items demand them.
- **Eval-only CI hook on Dataverse-skills PRs.** Mechanism is supported but operationalizing it (auth, runner host, score-delta comment formatting) is out of scope for v1.

## Migration Plan

1. **SkillOpt repo:** add `skillopt/envs/dataverse/`, `configs/dataverse/`, `data/dataverse/`. No changes to existing envs or shared modules.
2. **Dataverse-skills repo:** add `evals/skillopt/` alongside the existing `evals/tests/`. Existing `.biceval.json` files and `static_checks.py` keep running. Migrate `dv_data.biceval.json`'s 3 items into `dv_data.jsonl` via the converter; treat the result as a seed, then hand-author the rest.
3. **Aurora env:** run `setup_live_env.py` once to create `SkillOptEvals` solution and seed `sko_*` reference data.
4. **Per-skill rollout:** start with `dv-data` (richest existing content, most antipattern surface, has seed items). Land env scaffolding + 15-25 items + noise-floor check. Run first training job. Promote winning skill via draft PR. Validate end-to-end before scaling to the next skill.
5. **Order of skill rollout:** dv-data → dv-query → dv-metadata → dv-solution → dv-admin → dv-security → dv-overview → dv-connect. Richest content first; orchestration skills last.
