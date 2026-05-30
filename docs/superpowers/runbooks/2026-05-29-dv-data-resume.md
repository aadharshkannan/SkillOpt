# dv-data — Resume Runbook

Tasks 29-31 require credentials Claude couldn't supply during the build session. This runbook walks through the remaining ~30 minutes of operational work end-to-end.

**State going in:** worktree `worktree-dataverse-skillopt-dv-data` at HEAD `11fc216`, 65 tests green, 17 dv_data eval items, end-to-end rollout proven to produce `hard=1, soft=1.000` on a real Copilot CLI invocation with a mocked judge.

## Step 1 — Configure environment (one-time)

Create `Dataverse-skills/.env` with the four required values for Aurora:

```
DATAVERSE_URL=https://aurorabapenvb31ab.crm10.dynamics.com/
TENANT_ID=<your-aurora-tenant-id>
# Optional service-principal auth (otherwise device-code flow is used):
# CLIENT_ID=<sp-app-id>
# CLIENT_SECRET=<sp-secret>
```

Set these in your shell (PowerShell shown — Bash uses `export`):

```powershell
$env:DATAVERSE_PLUGIN_SRC = "C:\Users\aadkannan\source\repos\Dataverse-skills"
$env:SKILLOPT_EVALS_SOLUTION = "SkillOptEvals"
$env:SKILLOPT_EVALS_PREFIX = "sko"

# Judge model — required for ANY rollout (semantic claims need scoring):
$env:JUDGE_AZURE_OPENAI_ENDPOINT = "https://<your-resource>.openai.azure.com/"
$env:JUDGE_AZURE_OPENAI_DEPLOYMENT = "gpt-5.4-mini"
$env:JUDGE_AZURE_OPENAI_AUTH_MODE = "azure_cli"

# Optimizer model — for the reflection / patch-proposing step:
$env:AZURE_OPENAI_ENDPOINT = "https://<your-resource>.openai.azure.com/"
$env:AZURE_OPENAI_AUTH_MODE = "azure_cli"
```

If using `azure_cli` auth mode, also run `az login` once.

Also confirm in the Dataverse-skills clone that you're on the right branch (which has the auth.py trace hook):

```powershell
cd C:\Users\aadkannan\source\repos\Dataverse-skills
git switch skillopt/eval-integration
```

## Step 2 — Apply the auth patch to your fork & PR upstream (optional, async)

The `skillopt/eval-integration` branch in `Dataverse-skills` has two commits ready:

- `eb68a98` — auth.py trace hook + 3 migrated items
- `1d8e3f9` — 14 more dv_data items

When ready to PR upstream into `microsoft/Dataverse-skills`:

```powershell
cd C:\Users\aadkannan\source\repos\Dataverse-skills
git push -u origin skillopt/eval-integration
gh pr create --base main --head skillopt/eval-integration --draft `
  --title "feat: SkillOpt eval integration (auth trace hook + 17 dv_data items)" `
  --body "Adds optional DATAVERSE_TRACE_FILE hook to scripts/auth.py and a starter dv_data eval set under evals/skillopt/. Used by Microsoft SkillOpt for skill-document optimization."
```

The auth hook is a strict superset (NO-OP when `DATAVERSE_TRACE_FILE` is unset), so it's safe to merge regardless of whether SkillOpt is in active use.

## Step 3 — Task 29: setup_live_env (one-time)

```powershell
cd C:\Users\aadkannan\source\repos\SkillOpt\.claude\worktrees\dataverse-skillopt-dv-data
python -m skillopt.envs.dataverse.scripts.setup_live_env
```

**Expected:** First run pops a device-code browser, you sign in once. Then it prints `created publisher sko = <guid>` and `created solution SkillOptEvals = <guid>` (or "already exists" on re-runs). Idempotent.

After completion, verify in Power Apps maker portal (or `pac org who`) that the `SkillOptEvals` solution is visible.

**Note about live eval tables:** Items `data_010`, `data_011`, `data_012` reference tables `sko_eval_ticket` and `sko_eval_account` that don't exist yet. The simplest path:

```powershell
# Manual create via MCP (if you have it set up):
# "Create a table called sko_eval_ticket with columns: title (string), priority (integer)"
# "Create a table called sko_eval_account with columns: name (string), sko_accountnumber (string, with alternate key)"
#
# OR skip live items in v1 — they're only 3 of 17 items.
```

For the first training run you can defer creating these tables; live items will fail their verify checks (logged in `out_of_scope_patches.jsonl` and `fail_reason`) but the other 14 items run fine.

## Step 4 — Task 30: noise-floor check

```powershell
cd C:\Users\aadkannan\source\repos\SkillOpt\.claude\worktrees\dataverse-skillopt-dv-data

# 4a. Score the current committed dv-data skill
python scripts/eval_only.py `
  --config configs/dataverse/dv_data.yaml `
  --skill ../../Dataverse-skills/.github/plugins/dataverse/skills/dv-data/SKILL.md `
  --split test
# Record mean(soft) and count(hard=1) from the output.

# 4b. Create a deliberately regressed copy
copy ..\..\Dataverse-skills\.github\plugins\dataverse\skills\dv-data\SKILL.md `
     C:\tmp\regressed_dv_data.md
# In a text editor, EITHER:
#   - delete the entire "Bulk Create" section, OR
#   - replace "client.records.create(table, list)" with "for record in records: client.records.create(table, record)"

python scripts/eval_only.py `
  --config configs/dataverse/dv_data.yaml `
  --skill C:\tmp\regressed_dv_data.md `
  --split test

# 4c. Create a trivially-changed copy (semantic no-op)
copy ..\..\Dataverse-skills\.github\plugins\dataverse\skills\dv-data\SKILL.md `
     C:\tmp\trivial_dv_data.md
# Edit: change a single typo, or collapse double spaces — nothing semantic.

python scripts/eval_only.py `
  --config configs/dataverse/dv_data.yaml `
  --skill C:\tmp\trivial_dv_data.md `
  --split test
```

**Pass criteria (per spec):**
- Baseline vs regressed: `mean(soft)` gap ≥ 0.15 AND ≥ 2 items flip `hard=1` → `hard=0`.
- Baseline vs trivial: `mean(soft)` gap < 0.05, no items flip `hard`.

If baseline-vs-regressed gap is too small, add more antipattern_trap items targeting the specific section you regressed. If baseline-vs-trivial gap is too large, the eval set has flaky items — inspect the per-item `fail_reason` to find which ones moved and either tighten or drop them.

## Step 5 — Task 31: first training run

```powershell
cd C:\Users\aadkannan\source\repos\SkillOpt\.claude\worktrees\dataverse-skillopt-dv-data

# Short pilot run — 1 epoch, small batch, to validate end-to-end behavior:
python scripts/train.py `
  --config configs/dataverse/dv_data.yaml `
  --out_root outputs/dv_data_run_01 `
  --num_epochs 1 `
  --batch_size 4
```

Expected runtime: ~20-40 minutes for 1 epoch on ~13 train items with response-only rollout. Live items (3 of 17) add ~1-2 min each.

Watch for:
- `outputs/dv_data_run_01/skills/skill_v0001.md` — differs from `skill_v0000.md`
- `outputs/dv_data_run_01/best_skill.md` — the run's winning skill
- `outputs/dv_data_run_01/history.json` — shows score trajectory; should be non-decreasing
- `outputs/dv_data_run_01/orphans.jsonl` — empty (live teardown worked) or some entries (run `sweep_orphans.py`)
- `outputs/dv_data_run_01/steps/step_0001/out_of_scope_patches.jsonl` — patches the optimizer wanted to apply to other skills (logged but blocked)

## Step 6 — Promote if improved

Compare best_skill against baseline:

```powershell
python scripts/eval_only.py `
  --config configs/dataverse/dv_data.yaml `
  --skill outputs/dv_data_run_01/best_skill.md `
  --split test
```

If the best skill improves on baseline (Step 4a):

```powershell
python -m skillopt.envs.dataverse.scripts.promote `
  --run outputs/dv_data_run_01 `
  --plugin C:\Users\aadkannan\source\repos\Dataverse-skills\.github\plugins\dataverse\skills\dv-data
```

This creates a new branch in Dataverse-skills, copies `best_skill.md` into the plugin, commits, pushes, and opens a draft PR against `microsoft/Dataverse-skills`. Review the diff manually — the optimizer's edits are markdown patches and easy to read.

## Cleanup

After the run completes, sweep any leaked live-test GUIDs:

```powershell
python -m skillopt.envs.dataverse.scripts.sweep_orphans
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `copilot binary not found` | Check `where copilot` returns a path. If missing, reinstall Copilot CLI. |
| Copilot CLI says "not authenticated" | Run `gh auth login` (Copilot CLI uses gh's auth). |
| Judge calls fail with auth errors | Re-run `az login` and confirm `JUDGE_AZURE_OPENAI_AUTH_MODE=azure_cli`. |
| `setup_live_env.py` hangs | Device-code login waiting — check your default browser for the prompt. |
| Live items all fail `row_count` checks | The `sko_eval_*` tables don't exist yet — see Step 3 note. |
| `out_of_scope_patches.jsonl` populated heavily | Optimizer wants to fix things in other skills — review and decide whether to relax the guardrail or merge those edits into the other skills' SKILL.md files manually. |
| Training run dies mid-step | Re-run the same command — the loop is resume-aware via `results.jsonl`. |

## What's done & ready

- ✅ SkillOpt-side engineering (Tasks 1-27) — all SkillOpt code, configs, scripts, prompts
- ✅ Dataverse-skills auth.py trace hook (Task 18b applied to `skillopt/eval-integration` branch)
- ✅ 17 dv_data eval items (Task 28) covering happy_path, antipattern_trap, edge_case, cross_tool, skill_contract — 3 with live verification
- ✅ End-to-end rollout proof point (1 real Copilot invocation, hard=1 soft=1.000 with mocked judge)
- ⏸ Tasks 29-31 — environment-dependent operational steps, this runbook covers them
