# Dataverse × SkillOpt — dv-data end-to-end Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship SkillOpt-optimizable `dv-data` skill end-to-end — new `copilot_cli_exec` backend, new Dataverse env adapter with live verification, ~15-25 hand-authored eval items, first training run, draft PR back into Dataverse-skills.

**Architecture:** Two SkillOpt additions modeled 1:1 on existing patterns — a new target backend (`copilot_cli_exec` ≈ existing `claude_code_exec`) and a new env (`dataverse` ≈ existing `searchqa`). Reward is hybrid: deterministic substring checks + LLM judge on the response, plus live verification against a real Dataverse environment for ~5-10 items. The Dataverse-skills plugin is the agent's tool surface; SkillOpt trains a working copy of one `SKILL.md` and PRs the winner back.

**Tech Stack:** Python 3.10+, pytest, ruff, PyYAML, Azure OpenAI SDK (existing), GitHub Copilot CLI (new external dep), PowerPlatform-Dataverse-Client Python SDK (for live verification).

**Spec:** `docs/superpowers/specs/2026-05-28-dataverse-skillopt-design.md` — refer to it for design rationale; this plan does not re-explain the "why."

**Scope of this plan:** dv-data only. Rolling out to the other 7 skills is follow-up content authoring that reuses every component built here — no new plan required.

---

## File Structure

### SkillOpt repo — new files

```
skillopt/model/
  copilot_backend.py                ← chat_target / chat_target_messages wrapper
  copilot_harness.py                ← workspace prep + plugin install + exec invocation

skillopt/envs/dataverse/
  __init__.py
  adapter.py                        ← DataverseSkillAdapter
  dataloader.py                     ← DataverseSkillDataLoader (subclass of SplitDataLoader)
  rollout.py                        ← process_one + run_batch
  judges.py                         ← deterministic + semantic judges + reward composition
  live.py                           ← trace-hook controller + verify + teardown + orphan sweep
  schema.py                         ← parse + validate JSONL items
  prompts/
    analyst_error.md
    analyst_success.md
  scripts/
    setup_live_env.py
    migrate_biceval.py
    split.py
    promote.py
    sweep_orphans.py

configs/dataverse/
  _base.yaml
  dv_data.yaml

tests/                              ← REPO HAS NO TESTS TODAY. Phase 1 creates this tree.
  conftest.py
  model/test_copilot_backend_config.py
  envs/dataverse/test_schema.py
  envs/dataverse/test_judges.py
  envs/dataverse/test_dataloader.py
  envs/dataverse/scripts/test_migrate_biceval.py
  envs/dataverse/scripts/test_split.py
  envs/dataverse/test_live_trace.py
```

### SkillOpt repo — modified files

```
skillopt/model/backend_config.py    ← add "copilot_cli_exec" + config helpers
skillopt/model/router.py            ← route to copilot_backend
skillopt/model/__init__.py          ← export new symbols (only if existing pattern requires)
skillopt/config.py                  ← add judge_* + copilot_cli_exec_* keys to _FLATTEN_MAP
skillopt/envs/__init__.py           ← register "dataverse" env
configs/_base_/default.yaml         ← add judge model role + copilot_cli_exec defaults
pyproject.toml                      ← add httpx (judge HTTP calls if needed) + jsonschema
```

### Dataverse-skills repo — new + modified files

```
evals/skillopt/dv_data.jsonl        ← migrated + hand-authored items
scripts/auth.py                     ← MODIFIED: optional trace hook controlled by DATAVERSE_TRACE_FILE env var
```

### Generated artifacts (not committed)

```
data/dataverse/dv_data/{train,val,test}/items.json   ← built by split.py
outputs/<run>/                                        ← standard SkillOpt run output
```

---

## Phase 1 — Test scaffold

### Task 1: Create the test tree and confirm pytest runs

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/model/__init__.py`
- Create: `tests/envs/__init__.py`
- Create: `tests/envs/dataverse/__init__.py`
- Create: `tests/envs/dataverse/scripts/__init__.py`
- Create: `tests/sanity/test_imports.py`

- [ ] **Step 1: Create empty `__init__.py` files**

```bash
mkdir -p tests/model tests/envs/dataverse/scripts tests/sanity
touch tests/__init__.py tests/model/__init__.py tests/envs/__init__.py tests/envs/dataverse/__init__.py tests/envs/dataverse/scripts/__init__.py
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
"""Pytest fixtures shared across the SkillOpt test suite."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure src is importable.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def pytest_configure(config):
    """Default to a hermetic config — no live Azure/Dataverse calls in unit tests."""
    os.environ.setdefault("REFLACT_MODEL_BACKEND", "azure_openai")
    os.environ.setdefault("TARGET_BACKEND", "openai_chat")
    os.environ.setdefault("OPTIMIZER_BACKEND", "openai_chat")
```

- [ ] **Step 3: Write `tests/sanity/test_imports.py`**

```python
"""Smoke test: every top-level module imports without side effects."""
import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "skillopt.config",
        "skillopt.model.router",
        "skillopt.model.backend_config",
        "skillopt.envs",
    ],
)
def test_module_imports(module):
    importlib.import_module(module)
```

- [ ] **Step 4: Run the sanity test**

```bash
pip install -e ".[dev]"
pytest tests/sanity/ -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/ pyproject.toml
git commit -m "test: scaffold pytest tree with sanity import test"
```

---

## Phase 2 — `copilot_cli_exec` backend

The Copilot CLI is a subprocess invocation pattern, just like `claude_code_exec` and `codex_exec`. Mirror those modules' shape and conventions.

### Task 2: Discover the Copilot CLI binary surface

**Files:** (no code edits — this is exploration)

- [ ] **Step 1: Install Copilot CLI locally**

Follow [github/copilot-cli](https://github.com/github/copilot-cli) install instructions. Verify:

```bash
copilot --version
copilot --help
```

- [ ] **Step 2: Document the binary's invocation surface in a scratch note**

Identify the equivalents for what `claude_code_exec` uses:
- How to pass a prompt non-interactively (stdin? `--prompt`? a flag?)
- How to specify a profile / model
- How to point at a plugin manifest directory
- How to set effort / reasoning budget
- How to capture the final response (stdout? a file?)
- How auth works (`gh auth login`? a config file?)

Save the discoveries as comments to use in Task 3. **Do not skip — Tasks 3 and 4 depend on this concrete knowledge.** If the binary's surface differs materially from `claude_code_exec`, surface that to the user before continuing.

- [ ] **Step 3: Install the Dataverse plugin into Copilot CLI**

```bash
copilot
# inside the CLI:
/plugin install dataverse@awesome-copilot
```

Verify the install succeeds and that a single-shot prompt like "list Dataverse skills" loads the plugin.

---

### Task 3: Add `copilot_cli_exec` to `backend_config.py`

**Files:**
- Modify: `skillopt/model/backend_config.py:67` (allowed target backends set) and end-of-file (add config helpers)
- Create: `tests/model/test_copilot_backend_config.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for copilot_cli_exec config wiring in backend_config."""
import os

import pytest

from skillopt.model import backend_config as bc


def test_copilot_cli_exec_is_valid_target_backend():
    bc.set_target_backend("copilot_cli_exec")
    assert bc.get_target_backend() == "copilot_cli_exec"
    assert bc.is_target_exec_backend() is True


def test_configure_copilot_cli_exec_sets_env(monkeypatch):
    monkeypatch.delenv("COPILOT_CLI_EXEC_PATH", raising=False)
    bc.configure_copilot_cli_exec(
        path="/usr/local/bin/copilot",
        profile="dv-eval",
        model="gpt-5.5",
        use_sdk="auto",
        effort="medium",
    )
    cfg = bc.get_copilot_cli_exec_config()
    assert cfg["path"] == "/usr/local/bin/copilot"
    assert cfg["profile"] == "dv-eval"
    assert cfg["model"] == "gpt-5.5"
    assert os.environ["COPILOT_CLI_EXEC_PATH"] == "/usr/local/bin/copilot"


def test_set_target_backend_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported target backend"):
        bc.set_target_backend("notathing")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/model/test_copilot_backend_config.py -v
```

Expected: 2 failures (`copilot_cli_exec` not in allowed set; `configure_copilot_cli_exec` doesn't exist).

- [ ] **Step 3: Update the allowed set in `backend_config.py`**

In `set_target_backend`, change line 67's set literal:

```python
def set_target_backend(backend: str) -> None:
    global TARGET_BACKEND
    TARGET_BACKEND = normalize_backend_name(backend or "openai_chat")
    if TARGET_BACKEND not in {
        "openai_chat", "claude_chat", "qwen_chat",
        "codex_exec", "claude_code_exec", "copilot_cli_exec",
    }:
        raise ValueError(
            f"Unsupported target backend: {TARGET_BACKEND!r}. "
            "Supported values are 'openai_chat', 'claude_chat', 'qwen_chat', "
            "'codex_exec', 'claude_code_exec', and 'copilot_cli_exec'."
        )
    os.environ["TARGET_BACKEND"] = TARGET_BACKEND
```

Also update `is_target_exec_backend`:

```python
def is_target_exec_backend() -> bool:
    return TARGET_BACKEND in {"codex_exec", "claude_code_exec", "copilot_cli_exec"}
```

- [ ] **Step 4: Add module-level config constants for the new backend**

After the existing `CLAUDE_CODE_EXEC_*` constants, add:

```python
COPILOT_CLI_EXEC_PATH = os.environ.get("COPILOT_CLI_EXEC_PATH", "copilot")
COPILOT_CLI_EXEC_PROFILE = os.environ.get("COPILOT_CLI_EXEC_PROFILE", "")
COPILOT_CLI_EXEC_MODEL = os.environ.get("COPILOT_CLI_EXEC_MODEL", "gpt-5.5")
COPILOT_CLI_EXEC_USE_SDK = os.environ.get("COPILOT_CLI_EXEC_USE_SDK", "auto")
COPILOT_CLI_EXEC_EFFORT = os.environ.get("COPILOT_CLI_EXEC_EFFORT", "medium")
```

- [ ] **Step 5: Add `configure_copilot_cli_exec` and `get_copilot_cli_exec_config`**

Modeled on `configure_claude_code_exec` and `get_claude_code_exec_config`:

```python
def configure_copilot_cli_exec(
    *,
    path: str | None = None,
    profile: str | None = None,
    model: str | None = None,
    use_sdk: str | None = None,
    effort: str | None = None,
) -> None:
    global COPILOT_CLI_EXEC_PATH, COPILOT_CLI_EXEC_PROFILE, COPILOT_CLI_EXEC_MODEL, COPILOT_CLI_EXEC_USE_SDK, COPILOT_CLI_EXEC_EFFORT
    if path is not None:
        COPILOT_CLI_EXEC_PATH = str(path).strip() or "copilot"
        os.environ["COPILOT_CLI_EXEC_PATH"] = COPILOT_CLI_EXEC_PATH
    if profile is not None:
        COPILOT_CLI_EXEC_PROFILE = str(profile).strip()
        os.environ["COPILOT_CLI_EXEC_PROFILE"] = COPILOT_CLI_EXEC_PROFILE
    if model is not None:
        COPILOT_CLI_EXEC_MODEL = str(model).strip() or "gpt-5.5"
        os.environ["COPILOT_CLI_EXEC_MODEL"] = COPILOT_CLI_EXEC_MODEL
    if use_sdk is not None:
        COPILOT_CLI_EXEC_USE_SDK = str(use_sdk).strip().lower() or "auto"
        os.environ["COPILOT_CLI_EXEC_USE_SDK"] = COPILOT_CLI_EXEC_USE_SDK
    if effort is not None:
        COPILOT_CLI_EXEC_EFFORT = str(effort).strip().lower() or "medium"
        os.environ["COPILOT_CLI_EXEC_EFFORT"] = COPILOT_CLI_EXEC_EFFORT


def get_copilot_cli_exec_config() -> dict[str, str | int]:
    return {
        "path": COPILOT_CLI_EXEC_PATH,
        "profile": COPILOT_CLI_EXEC_PROFILE,
        "model": COPILOT_CLI_EXEC_MODEL,
        "use_sdk": COPILOT_CLI_EXEC_USE_SDK,
        "effort": COPILOT_CLI_EXEC_EFFORT,
        "empty_response_retries": EXEC_EMPTY_RESPONSE_RETRIES,
    }
```

- [ ] **Step 6: Run tests and verify they pass**

```bash
pytest tests/model/test_copilot_backend_config.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add skillopt/model/backend_config.py tests/model/test_copilot_backend_config.py
git commit -m "feat(model): wire copilot_cli_exec into backend_config"
```

---

### Task 4: Implement `copilot_harness.py`

**Files:**
- Create: `skillopt/model/copilot_harness.py`

This mirrors `codex_harness.py`'s `prepare_workspace` + `render_skill_md` + `run_target_exec` shape. Copy the existing `codex_harness.py` as the starting template and adapt the subprocess call for Copilot CLI.

- [ ] **Step 1: Copy `codex_harness.py` as the starting template**

```bash
cp skillopt/model/codex_harness.py skillopt/model/copilot_harness.py
```

- [ ] **Step 2: Adapt the file header and imports**

Replace the docstring with a Copilot-specific one. Replace the import line `from skillopt.model.backend_config import (get_claude_code_exec_config, get_codex_exec_config, ...)` so it pulls in `get_copilot_cli_exec_config` instead.

- [ ] **Step 3: Adapt the subprocess invocation**

Find every `subprocess.Popen` / `subprocess.run` call in the copied file that constructs the codex command line. Replace with the Copilot CLI invocation discovered in Task 2. Concretely:

- If Copilot CLI accepts a prompt via stdin: pipe via `subprocess.run(input=prompt.encode(), ...)`.
- If it requires a positional or `--prompt` flag: build the argv accordingly.
- If it writes the final response to a file: post-process by reading that file.
- If it writes to stdout: capture `stdout` directly.

Use `get_copilot_cli_exec_config()` to read path, profile, model, use_sdk, effort. Match `claude_code_exec`'s convention for empty-response retries.

- [ ] **Step 4: Adapt `prepare_workspace` for the Copilot plugin manifest**

`prepare_workspace` in `codex_harness.py` writes the skill content into `.agents/skills/skillopt-target/SKILL.md`. For Copilot CLI, write the Dataverse plugin tree into the workspace and overwrite ONLY the target skill's `SKILL.md` with the current `skill_md` parameter. The function signature stays the same; the internal layout changes:

```python
def prepare_workspace(
    *,
    work_dir: str,
    skill_md: str,                       # the in-progress SKILL.md for the target skill
    task_text: str = "",
    task_filename: str = "task.md",
    plugin_src_dir: str = "",            # NEW: path to Dataverse-skills clone
    target_skill_name: str = "",         # NEW: e.g., "dv-data"
    images: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
    copy_files: list[tuple[str, str]] | None = None,
    link_dirs: list[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """Prepare a Copilot CLI workspace with the Dataverse plugin tree.

    Copies the entire plugin tree from `plugin_src_dir` into `work_dir`.
    Overwrites `<plugin>/skills/<target_skill_name>/SKILL.md` with `skill_md`.
    Other skills stay frozen at their committed version.
    """
    ...
```

The internal implementation: `shutil.rmtree(work_dir)`, `shutil.copytree(plugin_src_dir, <work_dir>/plugin)`, `(<work_dir>/plugin/skills/<target_skill_name>/SKILL.md).write_text(skill_md)`. Then write `task.md` for the prompt.

- [ ] **Step 5: Smoke test**

Skip a full unit test here — `copilot_harness.py` is an integration module whose value comes from running against a real Copilot CLI. Add a smoke test instead:

Create `tests/model/test_copilot_harness_smoke.py`:

```python
"""Smoke test: prepare_workspace produces a valid plugin tree on disk.

Does NOT invoke the Copilot CLI — that's covered in Task 5's end-to-end smoke.
"""
import shutil
from pathlib import Path

import pytest

from skillopt.model.copilot_harness import prepare_workspace


@pytest.fixture
def fake_plugin(tmp_path: Path) -> Path:
    """Build a minimal plugin tree mimicking Dataverse-skills layout."""
    p = tmp_path / "plugin_src"
    (p / "skills" / "dv-data").mkdir(parents=True)
    (p / "skills" / "dv-data" / "SKILL.md").write_text("# Original dv-data\n")
    (p / "skills" / "dv-query").mkdir(parents=True)
    (p / "skills" / "dv-query" / "SKILL.md").write_text("# Frozen dv-query\n")
    return p


def test_prepare_workspace_swaps_only_target_skill(tmp_path, fake_plugin):
    work_dir = tmp_path / "work"
    prepare_workspace(
        work_dir=str(work_dir),
        skill_md="# Patched dv-data v42\n",
        task_text="solve this",
        plugin_src_dir=str(fake_plugin),
        target_skill_name="dv-data",
    )
    target = work_dir / "plugin" / "skills" / "dv-data" / "SKILL.md"
    assert "Patched dv-data v42" in target.read_text()
    frozen = work_dir / "plugin" / "skills" / "dv-query" / "SKILL.md"
    assert "Frozen dv-query" in frozen.read_text()
    assert (work_dir / "task.md").read_text().strip() == "solve this"
```

- [ ] **Step 6: Run smoke test**

```bash
pytest tests/model/test_copilot_harness_smoke.py -v
```

Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add skillopt/model/copilot_harness.py tests/model/test_copilot_harness_smoke.py
git commit -m "feat(model): add copilot_harness with plugin-tree workspace prep"
```

---

### Task 5: Implement `copilot_backend.py` and wire into router

**Files:**
- Create: `skillopt/model/copilot_backend.py`
- Modify: `skillopt/model/router.py` (add copilot_backend import + dispatch)

- [ ] **Step 1: Copy `claude_backend.py` as the starting template**

```bash
cp skillopt/model/claude_backend.py skillopt/model/copilot_backend.py
```

- [ ] **Step 2: Replace `claude_chat` / `claude_code_exec` references with Copilot equivalents**

Find every Anthropic API endpoint / model / config reference in the copied file. Replace with Copilot CLI equivalents:
- `chat_target` and `chat_target_messages` should call the harness (via `copilot_harness.run_target_exec`) when `TARGET_BACKEND == "copilot_cli_exec"`, never the Anthropic API.
- The `chat_optimizer` stub can raise `NotImplementedError` for now — optimizer stays on `openai_chat` per the spec.
- `get_token_summary` should return `{}` or hook into the harness's token-tracking if Copilot CLI emits usage info.

- [ ] **Step 3: Update `router.py`**

In `skillopt/model/router.py`:

```python
# Add to imports
from . import azure_openai, claude_backend, codex_backend, copilot_backend

# Add to _backend_module
def _backend_module(name: str):
    if name == "azure_openai":
        return azure_openai
    if name == "codex":
        return codex_backend
    if name == "claude":
        return claude_backend
    if name == "copilot":
        return copilot_backend
    raise ValueError(f"Unknown backend: {name!r}")


def _all_backend_modules() -> list[Any]:
    return [azure_openai, codex_backend, claude_backend, copilot_backend]
```

Update `set_backend` to accept `"copilot"`.

- [ ] **Step 4: End-to-end smoke test (manual, but check it in as a script)**

Create `scripts/smoke_copilot.py`:

```python
"""End-to-end smoke: invoke Copilot CLI via the harness with a trivial prompt.

Requires: Copilot CLI installed and authenticated (`gh auth login` + Copilot subscription).
Run: python scripts/smoke_copilot.py
"""
import os
import tempfile

from skillopt.model.backend_config import set_target_backend, configure_copilot_cli_exec
from skillopt.model.copilot_harness import prepare_workspace, run_target_exec

set_target_backend("copilot_cli_exec")
configure_copilot_cli_exec(model="gpt-5.5", effort="medium")

with tempfile.TemporaryDirectory() as work_dir:
    # Fake plugin with one skill that says "always answer in ALL CAPS"
    plugin_src = os.path.join(work_dir, "plugin_src")
    skill_dir = os.path.join(plugin_src, "skills", "tester")
    os.makedirs(skill_dir)
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write("---\nname: tester\ndescription: Use when asked a question. Always answer in ALL CAPS.\n---\n# Tester\nAlways answer in ALL CAPS.\n")

    target_workspace = os.path.join(work_dir, "work")
    prepare_workspace(
        work_dir=target_workspace,
        skill_md=open(os.path.join(skill_dir, "SKILL.md")).read(),
        task_text="What is the capital of France?",
        plugin_src_dir=plugin_src,
        target_skill_name="tester",
    )
    final, raw = run_target_exec(
        work_dir=target_workspace,
        prompt="Answer the question in task.md.",
        model="gpt-5.5",
        timeout=60,
    )
    print(f"FINAL: {final!r}")
    print(f"RAW: {raw[:500]!r}")
    assert "PARIS" in final.upper(), "Expected the agent to answer Paris (in caps if skill loaded)"
    print("OK")
```

- [ ] **Step 5: Run smoke**

```bash
python scripts/smoke_copilot.py
```

Expected: prints `OK`. If it fails on "Copilot CLI not found," fix `COPILOT_CLI_EXEC_PATH`. If it fails on auth, run `gh auth login` and confirm Copilot subscription.

- [ ] **Step 6: Commit**

```bash
git add skillopt/model/copilot_backend.py skillopt/model/router.py scripts/smoke_copilot.py
git commit -m "feat(model): implement copilot_backend and route copilot_cli_exec"
```

---

### Task 6: Update `skillopt/config.py` to flatten new model keys

**Files:**
- Modify: `skillopt/config.py` (extend `_FLATTEN_MAP`)
- Modify: `configs/_base_/default.yaml` (add judge role + copilot defaults)

- [ ] **Step 1: Add the new keys to `_FLATTEN_MAP`**

Open `skillopt/config.py` and find `_FLATTEN_MAP`. Add these entries:

```python
    # copilot_cli_exec backend
    "model.copilot_cli_exec_path": "copilot_cli_exec_path",
    "model.copilot_cli_exec_profile": "copilot_cli_exec_profile",
    "model.copilot_cli_exec_model": "copilot_cli_exec_model",
    "model.copilot_cli_exec_use_sdk": "copilot_cli_exec_use_sdk",
    "model.copilot_cli_exec_effort": "copilot_cli_exec_effort",
    # judge role
    "model.judge": "judge_model",
    "model.judge_backend": "judge_backend",
    "model.judge_azure_openai_endpoint": "judge_azure_openai_endpoint",
    "model.judge_azure_openai_api_version": "judge_azure_openai_api_version",
    "model.judge_azure_openai_api_key": "judge_azure_openai_api_key",
    "model.judge_azure_openai_auth_mode": "judge_azure_openai_auth_mode",
    "model.judge_azure_openai_ad_scope": "judge_azure_openai_ad_scope",
    "model.judge_azure_openai_managed_identity_client_id": "judge_azure_openai_managed_identity_client_id",
```

- [ ] **Step 2: Add defaults to `configs/_base_/default.yaml`**

Append to the `model:` section:

```yaml
  # copilot_cli_exec backend
  copilot_cli_exec_path: copilot
  copilot_cli_exec_profile: ""
  copilot_cli_exec_model: gpt-5.5
  copilot_cli_exec_use_sdk: auto
  copilot_cli_exec_effort: medium
  # judge role
  judge: gpt-5.4-mini
  judge_backend: openai_chat
  judge_azure_openai_endpoint: ""
  judge_azure_openai_api_version: "2024-12-01-preview"
  judge_azure_openai_api_key: ""
  judge_azure_openai_auth_mode: azure_cli
  judge_azure_openai_ad_scope: "https://cognitiveservices.azure.com/.default"
  judge_azure_openai_managed_identity_client_id: ""
```

- [ ] **Step 3: Quick sanity test that config still loads**

```bash
python -c "from skillopt.config import load_config, flatten_config; c = load_config('configs/_base_/default.yaml'); f = flatten_config(c); print(sorted(k for k in f if k.startswith('judge_') or k.startswith('copilot_')))"
```

Expected: prints the new keys.

- [ ] **Step 4: Commit**

```bash
git add skillopt/config.py configs/_base_/default.yaml
git commit -m "feat(config): add judge role and copilot_cli_exec keys to flatten map"
```

---

## Phase 3 — Eval item schema, migration, split

### Task 7: Implement `schema.py` for JSONL item validation

**Files:**
- Create: `skillopt/envs/dataverse/schema.py`
- Create: `tests/envs/dataverse/test_schema.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/envs/dataverse/test_schema.py -v
```

Expected: import error (module doesn't exist yet).

- [ ] **Step 3: Implement the schema**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/envs/dataverse/test_schema.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add skillopt/envs/dataverse/__init__.py skillopt/envs/dataverse/schema.py tests/envs/dataverse/test_schema.py
git commit -m "feat(dataverse): add JSONL eval-item schema with validation"
```

(Make sure `skillopt/envs/dataverse/__init__.py` exists — create an empty file if not.)

---

### Task 8: Implement `migrate_biceval.py`

**Files:**
- Create: `skillopt/envs/dataverse/scripts/__init__.py` (empty)
- Create: `skillopt/envs/dataverse/scripts/migrate_biceval.py`
- Create: `tests/envs/dataverse/scripts/test_migrate_biceval.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for biceval → SkillOpt JSONL conversion."""
import json
from pathlib import Path

from skillopt.envs.dataverse.scripts.migrate_biceval import convert_biceval_file


BICEVAL_SAMPLE = {
    "group": "DataverseSkills",
    "scenarioName": "Data",
    "tests": [
        {
            "test_id": "data_001",
            "prompt": "Write a Python script that creates a single ticket record.",
            "expected_response": "Agent uses Python SDK pattern.",
            "category": "data",
            "description": "Single-record create via SDK.",
            "priority": 1,
            "tags": {"Suite": "Regression", "Domain": "Data", "Skill": "dv-data"},
            "custom_metadata": {"skill": "dv-data"},
            "assertions": [
                "PRIORITY_1: Agent uses the official Python SDK.",
                "PRIORITY_2: CONTAINS: new_ticket",
                "PRIORITY_2: SKILL_LOADED: dv-data",
                "PRIORITY_1: NOT_CONTAINS: One HTTP call per record",
            ],
        }
    ],
}


def test_convert_biceval_produces_valid_jsonl(tmp_path: Path):
    src = tmp_path / "dv_data.biceval.json"
    dst = tmp_path / "dv_data.jsonl"
    src.write_text(json.dumps(BICEVAL_SAMPLE))
    convert_biceval_file(str(src), str(dst), skill_name="dv-data")
    lines = dst.read_text().strip().splitlines()
    assert len(lines) == 1
    item = json.loads(lines[0])
    assert item["id"] == "data_001"
    assert item["skill"] == "dv-data"
    assert item["category"] == "happy_path"  # default for non-trap items
    assert "Agent uses the official Python SDK." in [c["claim"] for c in item["semantic"]]
    assert "new_ticket" in item["deterministic"]["must_contain"]
    assert "One HTTP call per record" in item["deterministic"]["must_not_contain"]
    assert item["deterministic"]["must_load_skill"] == "dv-data"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/envs/dataverse/scripts/test_migrate_biceval.py -v
```

- [ ] **Step 3: Implement the conversion**

```python
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
    print(f"converted {n} items → {args.out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/envs/dataverse/scripts/test_migrate_biceval.py -v
```

- [ ] **Step 5: Run the migration against the existing biceval file**

```bash
python -m skillopt.envs.dataverse.scripts.migrate_biceval \
  --in ../Dataverse-skills/evals/tests/dv_data.biceval.json \
  --out ../Dataverse-skills/evals/skillopt/dv_data.jsonl \
  --skill dv-data
```

Expected: prints `converted 3 items → ...`. Inspect the JSONL file — every test from the original biceval should appear as a valid item.

- [ ] **Step 6: Commit**

```bash
git add skillopt/envs/dataverse/scripts/__init__.py skillopt/envs/dataverse/scripts/migrate_biceval.py tests/envs/dataverse/scripts/test_migrate_biceval.py
git commit -m "feat(dataverse): migrate biceval.json → SkillOpt JSONL"
```

Also commit the generated JSONL in the **Dataverse-skills** repo (separate commit, separate repo):

```bash
cd ../Dataverse-skills
git add evals/skillopt/dv_data.jsonl
git commit -m "feat(evals): add SkillOpt-format dv_data items (migrated from biceval)"
```

---

### Task 9: Implement `split.py`

**Files:**
- Create: `skillopt/envs/dataverse/scripts/split.py`
- Create: `tests/envs/dataverse/scripts/test_split.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for deterministic 80/10/10 splitter."""
import json
from pathlib import Path

from skillopt.envs.dataverse.scripts.split import split_items_file


def _make_jsonl(tmp_path: Path, n: int) -> Path:
    p = tmp_path / "items.jsonl"
    with p.open("w") as f:
        for i in range(n):
            f.write(json.dumps({
                "id": f"item_{i:03d}", "skill": "dv-data", "category": "happy_path",
                "prompt": "p", "expected_summary": "e", "deterministic": {}, "semantic": [],
            }) + "\n")
    return p


def test_split_80_10_10(tmp_path):
    src = _make_jsonl(tmp_path, 100)
    out = tmp_path / "out"
    split_items_file(str(src), str(out), ratio="80:10:10", seed=42)
    train = (out / "train" / "items.json").read_text()
    val = (out / "val" / "items.json").read_text()
    test = (out / "test" / "items.json").read_text()
    train_items = json.loads(train)
    val_items = json.loads(val)
    test_items = json.loads(test)
    assert len(train_items) == 80
    assert len(val_items) == 10
    assert len(test_items) == 10
    # disjoint
    ids = lambda items: {i["id"] for i in items}
    assert ids(train_items).isdisjoint(ids(val_items))
    assert ids(train_items).isdisjoint(ids(test_items))
    assert ids(val_items).isdisjoint(ids(test_items))


def test_split_is_deterministic(tmp_path):
    src = _make_jsonl(tmp_path, 100)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    split_items_file(str(src), str(out1), ratio="80:10:10", seed=42)
    split_items_file(str(src), str(out2), ratio="80:10:10", seed=42)
    assert (out1 / "train" / "items.json").read_text() == (out2 / "train" / "items.json").read_text()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/envs/dataverse/scripts/test_split.py -v
```

- [ ] **Step 3: Implement the splitter**

```python
"""Deterministic 80/10/10 splitter for SkillOpt JSONL items.

Usage:
    python -m skillopt.envs.dataverse.scripts.split \
        --in evals/skillopt/dv_data.jsonl \
        --out data/dataverse/dv_data \
        --ratio 80:10:10 --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def split_items_file(in_path: str, out_dir: str, ratio: str = "80:10:10", seed: int = 42) -> dict[str, int]:
    parts = [int(x) for x in ratio.split(":")]
    if len(parts) != 3 or sum(parts) != 100:
        raise ValueError(f"ratio must be 'A:B:C' summing to 100, got {ratio!r}")
    train_pct, val_pct, _ = parts

    with open(in_path, encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]

    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = (n * train_pct) // 100
    n_val = (n * val_pct) // 100
    splits = {
        "train": shuffled[:n_train],
        "val": shuffled[n_train:n_train + n_val],
        "test": shuffled[n_train + n_val:],
    }
    out = Path(out_dir)
    for name, lst in splits.items():
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        with (d / "items.json").open("w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False, indent=2)
    return {name: len(lst) for name, lst in splits.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_dir", required=True)
    ap.add_argument("--ratio", default="80:10:10")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    counts = split_items_file(args.in_path, args.out_dir, args.ratio, args.seed)
    print(f"split sizes: {counts}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/envs/dataverse/scripts/test_split.py -v
```

- [ ] **Step 5: Commit**

```bash
git add skillopt/envs/dataverse/scripts/split.py tests/envs/dataverse/scripts/test_split.py
git commit -m "feat(dataverse): deterministic 80/10/10 splitter"
```

---

### Task 10: Run split on the migrated dv_data items

**Files:** (no code edits — operational step)

- [ ] **Step 1: Run the splitter**

```bash
python -m skillopt.envs.dataverse.scripts.split \
  --in ../Dataverse-skills/evals/skillopt/dv_data.jsonl \
  --out data/dataverse/dv_data \
  --ratio 80:10:10 --seed 42
```

Expected: produces 3 directories under `data/dataverse/dv_data/{train,val,test}/items.json`. With only 3 source items so far, all 3 land in train (80% of 3 = 2 train, but `(3*80)//100 = 2` so 2 train, 0 val, 1 test). That's fine for now — Task 36 will add ~12-22 more items and we'll re-split.

- [ ] **Step 2: Commit the split files**

```bash
git add data/dataverse/
git commit -m "data: initial dv_data split (3 seed items, full set in Task 36)"
```

---

## Phase 4 — Judges (deterministic + semantic + reward composition)

### Task 11: Implement deterministic checks

**Files:**
- Create: `skillopt/envs/dataverse/judges.py` (deterministic part)
- Create: `tests/envs/dataverse/test_judges.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/envs/dataverse/test_judges.py -v
```

- [ ] **Step 3: Implement deterministic checks**

```python
"""Reward function — deterministic checks, semantic judge, composition.

For now this file only implements the deterministic part. Task 12 adds the LLM judge.
Task 13 wires both into the soft/hard score.
"""
from __future__ import annotations

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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/envs/dataverse/test_judges.py -v
```

- [ ] **Step 5: Commit**

```bash
git add skillopt/envs/dataverse/judges.py tests/envs/dataverse/test_judges.py
git commit -m "feat(dataverse): deterministic checks for response judging"
```

---

### Task 12: Implement the semantic LLM judge

**Files:**
- Modify: `skillopt/envs/dataverse/judges.py` (add `judge_semantic_claim` and helpers)
- Modify: `tests/envs/dataverse/test_judges.py` (add judge tests with a fake client)

- [ ] **Step 1: Extend the failing test**

Append to `tests/envs/dataverse/test_judges.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/envs/dataverse/test_judges.py -v
```

- [ ] **Step 3: Implement the judge**

Append to `skillopt/envs/dataverse/judges.py`:

```python
import re


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
    See `judges_client.py` (Task 14 will add the real Azure OpenAI client).
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/envs/dataverse/test_judges.py -v
```

- [ ] **Step 5: Commit**

```bash
git add skillopt/envs/dataverse/judges.py tests/envs/dataverse/test_judges.py
git commit -m "feat(dataverse): LLM-judge for semantic claim scoring"
```

---

### Task 13: Compose `hard` + `soft` reward

**Files:**
- Modify: `skillopt/envs/dataverse/judges.py` (add `compose_reward`)
- Modify: `tests/envs/dataverse/test_judges.py` (add composition tests)

- [ ] **Step 1: Extend the failing test**

Append to `tests/envs/dataverse/test_judges.py`:

```python
from skillopt.envs.dataverse.judges import compose_reward


def test_compose_reward_all_pass():
    det = DeterministicResult(must_contain_pass=1.0, must_not_contain_pass=1.0)
    sem = [SemanticScore(claim="c", priority=1, value=1.0, rationale="ok")]
    reward = compose_reward(deterministic=det, semantic=sem, live_pass_rate=None, weights={"semantic": 0.5, "deterministic": 0.5, "live": 0.0})
    assert reward["hard"] == 1
    assert reward["soft"] == 1.0


def test_compose_reward_p1_semantic_fail_forces_hard_zero():
    det = DeterministicResult(must_contain_pass=1.0, must_not_contain_pass=1.0)
    sem = [SemanticScore(claim="c", priority=1, value=0.3, rationale="weak")]
    reward = compose_reward(deterministic=det, semantic=sem, live_pass_rate=None, weights={"semantic": 0.5, "deterministic": 0.5, "live": 0.0})
    assert reward["hard"] == 0
    assert reward["soft"] < 1.0


def test_compose_reward_live_block_weights():
    det = DeterministicResult(must_contain_pass=1.0, must_not_contain_pass=1.0)
    sem = [SemanticScore(claim="c", priority=1, value=1.0, rationale="ok")]
    reward = compose_reward(deterministic=det, semantic=sem, live_pass_rate=0.5, weights={"semantic": 0.5, "deterministic": 0.3, "live": 0.2})
    # 0.5*1.0 + 0.3*1.0 + 0.2*0.5 = 0.9
    assert abs(reward["soft"] - 0.9) < 0.01
    # live not all-pass → hard=0
    assert reward["hard"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/envs/dataverse/test_judges.py -v
```

- [ ] **Step 3: Implement composition**

Append to `skillopt/envs/dataverse/judges.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/envs/dataverse/test_judges.py -v
```

- [ ] **Step 5: Commit**

```bash
git add skillopt/envs/dataverse/judges.py tests/envs/dataverse/test_judges.py
git commit -m "feat(dataverse): compose hard+soft reward from det/sem/live signals"
```

---

## Phase 5 — Dataloader + adapter shell

### Task 14: Implement `DataverseSkillDataLoader`

**Files:**
- Create: `skillopt/envs/dataverse/dataloader.py`
- Create: `tests/envs/dataverse/test_dataloader.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the Dataverse dataloader."""
import json
from pathlib import Path

from skillopt.envs.dataverse.dataloader import DataverseSkillDataLoader


def _make_split_dir(tmp_path: Path, n_each: int) -> Path:
    root = tmp_path / "data"
    for split in ("train", "val", "test"):
        d = root / split
        d.mkdir(parents=True)
        items = [
            {
                "id": f"{split}_{i}", "skill": "dv-data", "category": "happy_path",
                "prompt": "p", "expected_summary": "e", "deterministic": {}, "semantic": [],
            }
            for i in range(n_each)
        ]
        (d / "items.json").write_text(json.dumps(items))
    return root


def test_dataloader_loads_splits(tmp_path):
    root = _make_split_dir(tmp_path, n_each=3)
    dl = DataverseSkillDataLoader(split_dir=str(root))
    dl.setup({"split_mode": "split_dir", "split_dir": str(root)})
    items = dl.get_split_items("train")
    assert len(items) == 3
    assert items[0]["id"] == "train_0"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/envs/dataverse/test_dataloader.py -v
```

- [ ] **Step 3: Implement the dataloader**

```python
"""Dataloader for Dataverse SkillOpt env. Mirrors SearchQA's pattern."""
from __future__ import annotations

import json

from skillopt.datasets.base import SplitDataLoader


def _load_items(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
    data = json.loads(content)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or list(data.values())
    return []


class DataverseSkillDataLoader(SplitDataLoader):
    """Loads pre-split items.json files for a single skill."""

    def load_raw_items(self, data_path: str) -> list[dict]:
        return _load_items(data_path)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/envs/dataverse/test_dataloader.py -v
```

(Note: `SplitDataLoader` may need a specific config shape. If the test fails because `setup()` doesn't load from `split_dir`, refer to `skillopt/envs/searchqa/dataloader.py` for the exact contract — copy its setup pattern.)

- [ ] **Step 5: Commit**

```bash
git add skillopt/envs/dataverse/dataloader.py tests/envs/dataverse/test_dataloader.py
git commit -m "feat(dataverse): dataloader for pre-split JSONL items"
```

---

### Task 15: Implement `DataverseSkillAdapter` shell

**Files:**
- Create: `skillopt/envs/dataverse/adapter.py`

This task creates the adapter class skeleton — `__init__`, `setup`, `get_dataloader`, `build_env_from_batch`, `build_train_env`, `build_eval_env`, and a `rollout` that raises NotImplementedError (filled in by Task 17). Mirror `skillopt/envs/searchqa/adapter.py`.

- [ ] **Step 1: Copy SearchQA adapter as starting template**

```bash
cp skillopt/envs/searchqa/adapter.py skillopt/envs/dataverse/adapter.py
```

- [ ] **Step 2: Rename the class and constructor params**

```python
class DataverseSkillAdapter(EnvAdapter):
    """SkillOpt env for a single Dataverse skill."""

    def __init__(
        self,
        skill_name: str,
        plugin_src_dir: str,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "split_dir",
        split_seed: int = 42,
        split_output_dir: str = "",
        max_turns: int = 1,
        exec_timeout: int = 180,
        workers: int = 24,
        live_workers: int = 4,
        analyst_workers: int = 16,
        failure_only: bool = False,
        minibatch_size: int = 8,
        edit_budget: int = 4,
        seed: int = 42,
        limit: int = 0,
        max_completion_tokens: int = 16384,
        live_enabled: bool = True,
        live_env_url: str = "",
        live_solution: str = "SkillOptEvals",
        live_prefix: str = "sko",
        record_bodies: bool = False,
    ) -> None:
        self.skill_name = skill_name
        self.plugin_src_dir = plugin_src_dir
        self.max_turns = max_turns
        self.exec_timeout = exec_timeout
        self.workers = workers
        self.live_workers = live_workers
        self.max_completion_tokens = int(max_completion_tokens)
        self.analyst_workers = analyst_workers
        self.failure_only = failure_only
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget
        self.live_enabled = live_enabled
        self.live_env_url = live_env_url
        self.live_solution = live_solution
        self.live_prefix = live_prefix
        self.record_bodies = record_bodies
        self.dataloader = DataverseSkillDataLoader(
            split_dir=split_dir,
            data_path=data_path,
            split_mode=split_mode,
            split_seed=split_seed,
            split_output_dir=split_output_dir,
            seed=seed,
            limit=limit,
        )
```

- [ ] **Step 3: Replace `rollout` body with a NotImplementedError stub**

```python
    def rollout(
        self,
        env_manager,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        raise NotImplementedError("Task 17 wires this to dataverse.rollout.run_batch")
```

- [ ] **Step 4: Replace `reflect` body with a NotImplementedError stub**

(Task 26 wires this; for now we just want the adapter to be importable.)

- [ ] **Step 5: Verify it imports cleanly**

```bash
python -c "from skillopt.envs.dataverse.adapter import DataverseSkillAdapter; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add skillopt/envs/dataverse/adapter.py
git commit -m "feat(dataverse): adapter shell with constructor and stubbed rollout/reflect"
```

---

## Phase 6 — Response-only rollout

### Task 16: Implement `judges_client.py` — judge LLM wrapper

**Files:**
- Create: `skillopt/envs/dataverse/judges_client.py`

The judge runs against the Azure OpenAI deployment specified by `judge_*` config. Reuse SkillOpt's existing `azure_openai` backend module rather than write a new HTTP client.

- [ ] **Step 1: Implement**

```python
"""Thin wrapper around Azure OpenAI for the judge role.

Uses SkillOpt's existing azure_openai backend module so we don't reinvent auth.
"""
from __future__ import annotations

import os

from skillopt.model import azure_openai


class JudgeClient:
    def __init__(self, deployment: str | None = None):
        self.deployment = deployment or os.environ.get("JUDGE_AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-mini")

    def chat(self, deployment: str, system: str, user: str) -> str:
        """Single LLM call. Returns just the text."""
        # Route through chat_with_deployment so the judge endpoint is honored.
        text, _ = azure_openai.chat_with_deployment(
            deployment=deployment or self.deployment,
            system=system,
            user=user,
            max_completion_tokens=512,
            retries=3,
            stage="judge",
            timeout=60,
        )
        return text
```

(Note: If `azure_openai.chat_with_deployment` doesn't accept a separate endpoint per call, you'll need to configure the judge endpoint via `configure_azure_openai` at adapter `setup()` time. Check the existing `azure_openai.py` for the right hook.)

- [ ] **Step 2: Commit**

```bash
git add skillopt/envs/dataverse/judges_client.py
git commit -m "feat(dataverse): JudgeClient wrapping Azure OpenAI for the judge role"
```

---

### Task 17: Implement `rollout.py` — `process_one` and `run_batch`

**Files:**
- Create: `skillopt/envs/dataverse/rollout.py`
- Modify: `skillopt/envs/dataverse/adapter.py` (wire rollout into `rollout()` method)

- [ ] **Step 1: Implement `process_one`**

```python
"""Dataverse rollout — single-item execution + parallel batch with resume.

Mirrors searchqa/rollout.py's structure. Live-item handling (proxy spawn,
verify, teardown) is added in Task 24; this module starts with response-only.
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
    """Heuristic skill-load detection.

    Inspect the Copilot CLI transcript for a plugin-load event referencing
    the target skill. The exact log line shape depends on Copilot CLI's logging
    — Task 2's discovery should produce the marker string. Until then, fall
    back to a string match on the skill name in the response.
    """
    marker = f"loaded skill: {target_skill}"   # PLACEHOLDER — REPLACE WITH ACTUAL MARKER FROM TASK 2
    if marker in response.lower():
        return target_skill
    if f"`{target_skill}`" in response or f"the {target_skill} skill" in response.lower():
        return target_skill
    return None


def process_one(
    item: dict,
    out_root: str,
    skill_content: str,
    *,
    plugin_src_dir: str,
    target_skill_name: str,
    judge_client: JudgeClient,
    judge_deployment: str,
    exec_timeout: int = 180,
    max_completion_tokens: int = 16384,
) -> dict:
    """Process a single eval item: run agent, score, return result dict."""
    parsed = parse_item(item)
    item_id = parsed.id
    pred_dir = os.path.join(out_root, "predictions", item_id)
    os.makedirs(pred_dir, exist_ok=True)

    result = {
        "id": item_id,
        "question": parsed.prompt,
        "hard": 0,
        "soft": 0.0,
        "predicted_answer": "",
        "gold_answers": [s.claim for s in parsed.semantic],
        "response": "",
        "fail_reason": "",
        "agent_ok": False,
        "n_turns": 0,
    }

    try:
        work_dir = os.path.join(pred_dir, "workspace")
        prepare_workspace(
            work_dir=work_dir,
            skill_md=skill_content,
            task_text=parsed.prompt,
            plugin_src_dir=plugin_src_dir,
            target_skill_name=target_skill_name,
        )
        final, raw = run_target_exec(
            work_dir=work_dir,
            prompt=(
                f"You are answering as a user of the Dataverse skills plugin. "
                f"Read task.md and respond. Cite the skill you used."
            ),
            model="",  # uses COPILOT_CLI_EXEC_MODEL by default
            timeout=exec_timeout,
        )

        with open(os.path.join(pred_dir, "response.txt"), "w", encoding="utf-8") as f:
            f.write(final or raw)

        result["response"] = final or raw
        result["agent_ok"] = True
        result["n_turns"] = 1

        # Deterministic checks
        skill_loaded = _detect_skill_loaded(result["response"], target_skill_name)
        det = run_deterministic_checks(
            response=result["response"],
            spec=parsed.deterministic,
            skill_loaded=skill_loaded,
        )

        # Semantic judge — one call per claim
        sem_scores = []
        for claim in parsed.semantic:
            score = judge_semantic_claim(
                client=judge_client,
                response=result["response"],
                claim=claim.claim,
                deployment=judge_deployment,
                priority=claim.priority,
            )
            sem_scores.append(score)

        # Live verification placeholder (filled in by Task 24).
        live_pass_rate = None
        if parsed.live and parsed.live.enabled:
            # Until Task 24 wires this, treat live items as response-only with a warning.
            result["fail_reason"] = "[WARN] live verification not yet implemented; treating as response-only"

        reward = compose_reward(
            deterministic=det,
            semantic=sem_scores,
            live_pass_rate=live_pass_rate,
        )

        result["hard"] = reward["hard"]
        result["soft"] = reward["soft"]
        if reward["fail_reason"]:
            result["fail_reason"] = (result["fail_reason"] + "\n" + reward["fail_reason"]).strip()
        result["predicted_answer"] = result["response"].strip().split("\n")[-1][:200]

        # Write per-item artifacts
        with open(os.path.join(pred_dir, "judge_rationales.jsonl"), "w", encoding="utf-8") as f:
            for s in sem_scores:
                f.write(json.dumps({"claim": s.claim, "priority": s.priority, "score": s.value, "rationale": s.rationale}) + "\n")
        with open(os.path.join(pred_dir, "verify_results.json"), "w", encoding="utf-8") as f:
            json.dump({
                "deterministic": {
                    "must_contain_pass": det.must_contain_pass,
                    "must_not_contain_pass": det.must_not_contain_pass,
                    "must_load_skill_pass": det.must_load_skill_pass,
                    "details": det.details,
                    "failures": det.failures,
                },
                "live_pass_rate": live_pass_rate,
            }, f, indent=2)

    except Exception as e:
        result["fail_reason"] = f"error: {type(e).__name__}: {e}\n{traceback.format_exc()}"

    return result
```

- [ ] **Step 2: Implement `run_batch`**

Append to `rollout.py`:

```python
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
    exec_timeout: int = 180,
    max_completion_tokens: int = 16384,
    task_timeout: int = 600,
) -> list[dict]:
    """Run items in parallel with resume support. Mirrors searchqa.run_batch."""
    task_timeout = max(int(task_timeout), int(exec_timeout) + 60)
    results_path = os.path.join(out_root, "results.jsonl")
    os.makedirs(out_root, exist_ok=True)

    done_ids: set[str] = set()
    existing: list[dict] = []
    if os.path.exists(results_path):
        with open(results_path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done_ids.add(str(r["id"]))
                    existing.append(r)
                except Exception:
                    pass

    pending = [it for it in items if str(it["id"]) not in done_ids]
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
        )

    with open(results_path, "a", encoding="utf-8") as outf, ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_run_one, it): it for it in pending}
        for fut in list(futs):
            it = futs[fut]
            try:
                res = fut.result(timeout=task_timeout)
            except Exception as exc:
                res = {"id": str(it["id"]), "hard": 0, "soft": 0.0, "fail_reason": f"timeout/error: {exc}", "agent_ok": False, "n_turns": 0}
            results.append(res)
            completed += 1
            if res.get("hard", 0):
                correct += 1
            acc = correct / completed if completed else 0
            print(f"    [rollout] {completed}/{total} (acc={acc:.3f}) id={res['id']} hard={res.get('hard', '?')}", flush=True)
            outf.write(json.dumps(res, ensure_ascii=False) + "\n")
            outf.flush()
    return results
```

- [ ] **Step 3: Wire into `DataverseSkillAdapter.rollout`**

In `skillopt/envs/dataverse/adapter.py`, replace the `NotImplementedError` body:

```python
    def rollout(
        self,
        env_manager,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        items = list(env_manager)
        from skillopt.envs.dataverse.judges_client import JudgeClient
        from skillopt.envs.dataverse.rollout import run_batch
        judge_client = JudgeClient()
        return run_batch(
            items=items,
            out_root=out_dir,
            skill_content=skill_content,
            plugin_src_dir=self.plugin_src_dir,
            target_skill_name=self.skill_name,
            judge_client=judge_client,
            judge_deployment=os.environ.get("JUDGE_AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-mini"),
            workers=self.workers,
            exec_timeout=self.exec_timeout,
            max_completion_tokens=self.max_completion_tokens,
        )
```

Add `import os` at the top if not already present.

- [ ] **Step 4: Smoke test (manual)**

Create `scripts/smoke_dataverse_rollout.py`:

```python
"""Smoke test: run the response-only rollout on the 3 migrated dv_data items.

Requires: Copilot CLI + plugin installed (smoke_copilot.py works), JUDGE env vars set.
"""
import os
import tempfile

from skillopt.envs.dataverse.adapter import DataverseSkillAdapter
from skillopt.envs.dataverse.dataloader import DataverseSkillDataLoader
from skillopt.model.backend_config import set_target_backend, configure_copilot_cli_exec

set_target_backend("copilot_cli_exec")
configure_copilot_cli_exec()  # uses env vars

adapter = DataverseSkillAdapter(
    skill_name="dv-data",
    plugin_src_dir=os.environ["DATAVERSE_PLUGIN_SRC"],
    split_dir="data/dataverse/dv_data",
)
adapter.setup({"split_mode": "split_dir"})

items = adapter.get_dataloader().get_split_items("train")
skill_content = open(f"{os.environ['DATAVERSE_PLUGIN_SRC']}/.github/plugins/dataverse/skills/dv-data/SKILL.md").read()

with tempfile.TemporaryDirectory() as out_dir:
    results = adapter.rollout(items, skill_content, out_dir)
    for r in results:
        print(f"{r['id']}: hard={r['hard']} soft={r['soft']:.2f}")
```

```bash
python scripts/smoke_dataverse_rollout.py
```

Expected: prints 2-3 lines with hard/soft scores. The current committed `dv-data` skill should score reasonably well — `hard=1` on the happy_path item and `hard=1` on the bulk-create item. The skill-contract item is the most demanding.

- [ ] **Step 5: Commit**

```bash
git add skillopt/envs/dataverse/rollout.py skillopt/envs/dataverse/adapter.py scripts/smoke_dataverse_rollout.py
git commit -m "feat(dataverse): response-only rollout with parallel batch + resume"
```

---

## Phase 7 — Live verification

### Task 18: Decide trace mechanism, then implement `live.py` controller

**Files:**
- Modify (in Dataverse-skills repo): `scripts/auth.py` — add optional trace hook
- Create (in SkillOpt repo): `skillopt/envs/dataverse/live.py`
- Create: `tests/envs/dataverse/test_live_trace.py`

The spec describes an HTTPS proxy, but a simpler implementation extends `Dataverse-skills/scripts/auth.py` with a trace hook controlled by a `DATAVERSE_TRACE_FILE` env var. This avoids TLS termination. Use the trace-hook approach unless cross-repo modification is blocked, in which case fall back to the proxy plan (described in the spec).

- [ ] **Step 1: Modify `Dataverse-skills/scripts/auth.py` to write a trace file**

In `auth.py`'s HTTP client setup (where the `requests.Session` is created or the `DataverseClient` is built), add:

```python
import json
import os
import time

_TRACE_PATH = os.environ.get("DATAVERSE_TRACE_FILE")


def _trace_request(method: str, url: str, status: int, response_summary: dict) -> None:
    if not _TRACE_PATH:
        return
    rec = {
        "ts": time.time(),
        "method": method,
        "url": url,
        "status": status,
        "summary": response_summary,
    }
    with open(_TRACE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
```

Hook `_trace_request` into the session's response handler. For GUID extraction, after parsing a 2xx response from a create/upsert endpoint, append to a sibling file `_TRACE_PATH.replace('.jsonl', '_guids.jsonl')`.

(Coordinate the exact integration with the auth.py author / current shape — this is a small, focused edit. Commit in the Dataverse-skills repo with a PR.)

- [ ] **Step 2: Implement `live.py` controller in SkillOpt**

```python
"""Live verification: orchestrate trace file, verify checks, teardown.

For each live item:
  1. Generate a unique trace file path.
  2. Set DATAVERSE_TRACE_FILE in the agent's env (via run_target_exec kwargs).
  3. After the agent finishes, read the trace + GUIDs.
  4. Run verify checks (row_count / request_count).
  5. Teardown — delete each GUID via the SDK.
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
    trace = os.path.join(out_dir, "predictions", item_id, "dataverse_trace.jsonl")
    guids = os.path.join(out_dir, "predictions", item_id, "created_guids.jsonl")
    return trace, guids


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def verify_row_count(check: dict, dataverse_client) -> tuple[bool, str]:
    table = check["table"]
    filt = check.get("filter", "")
    expected_min = check.get("expected_min", 0)
    expected_max = check.get("expected_max", None)
    # Query Dataverse for row count
    result = dataverse_client.records.get(table, filter=filt, select=[])
    count = sum(1 for _ in result)
    if count < expected_min:
        return False, f"row_count[{table}] {count} < min {expected_min}"
    if expected_max is not None and count > expected_max:
        return False, f"row_count[{table}] {count} > max {expected_max}"
    return True, f"row_count[{table}] = {count} (ok)"


def verify_request_count(check: dict, trace_records: list[dict]) -> tuple[bool, str]:
    rx = re.compile(check["endpoint_regex"])
    matched = [r for r in trace_records if rx.search(r.get("url", ""))]
    n = len(matched)
    expected_max = check.get("expected_max", None)
    expected_min = check.get("expected_min", 0)
    if n < expected_min:
        return False, f"request_count[{check['endpoint_regex']}] {n} < min {expected_min}"
    if expected_max is not None and n > expected_max:
        return False, f"request_count[{check['endpoint_regex']}] {n} > max {expected_max}"
    return True, f"request_count[{check['endpoint_regex']}] = {n} (ok)"


def run_verify(verify_specs: list[dict], trace_records: list[dict], dataverse_client) -> LiveResult:
    failures = []
    passed = 0
    for check in verify_specs:
        kind = check.get("kind")
        try:
            if kind == "row_count":
                ok, msg = verify_row_count(check, dataverse_client)
            elif kind == "request_count":
                ok, msg = verify_request_count(check, trace_records)
            else:
                ok, msg = False, f"unknown verify kind: {kind!r}"
        except Exception as e:
            ok, msg = False, f"verify error: {e}"
        if ok:
            passed += 1
        else:
            failures.append(msg)
    pass_rate = passed / len(verify_specs) if verify_specs else 1.0
    return LiveResult(pass_rate=pass_rate, failures=failures, created_guids=[], trace_records=trace_records)


def teardown(created_guids: list[dict], dataverse_client, orphans_path: str) -> None:
    """Delete each created GUID. On failure, append to orphans.jsonl."""
    for g in created_guids:
        try:
            dataverse_client.records.delete(g["table"], g["guid"])
        except Exception as e:
            with open(orphans_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({**g, "teardown_error": str(e)}) + "\n")
```

- [ ] **Step 3: Write a unit test (no real Dataverse)**

```python
"""Tests for live verification logic (mocked Dataverse)."""
from unittest.mock import MagicMock

from skillopt.envs.dataverse.live import (
    LiveResult,
    run_verify,
    verify_request_count,
    verify_row_count,
)


def test_verify_request_count_pass():
    trace = [{"url": "https://x/CreateMultiple", "method": "POST", "status": 200}]
    ok, msg = verify_request_count({"endpoint_regex": "/CreateMultiple", "expected_max": 1}, trace)
    assert ok is True


def test_verify_request_count_fail_too_many():
    trace = [{"url": f"https://x/api/data/v9.2/new_ticket", "method": "POST", "status": 204} for _ in range(500)]
    ok, msg = verify_request_count({"endpoint_regex": "/new_ticket", "expected_max": 1}, trace)
    assert ok is False
    assert "500" in msg


def test_verify_row_count_pass():
    client = MagicMock()
    client.records.get.return_value = iter([{}, {}, {}])  # 3 rows
    ok, msg = verify_row_count({"table": "sko_eval_ticket", "expected_min": 3, "expected_max": 3}, client)
    assert ok is True


def test_run_verify_aggregates():
    trace = [{"url": "/CreateMultiple"}]
    client = MagicMock()
    client.records.get.return_value = iter([{}, {}])
    res = run_verify(
        [
            {"kind": "request_count", "endpoint_regex": "/CreateMultiple", "expected_max": 1},
            {"kind": "row_count", "table": "x", "expected_min": 2, "expected_max": 2},
        ],
        trace,
        client,
    )
    assert res.pass_rate == 1.0
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/envs/dataverse/test_live_trace.py -v
```

- [ ] **Step 5: Commit**

In SkillOpt repo:

```bash
git add skillopt/envs/dataverse/live.py tests/envs/dataverse/test_live_trace.py
git commit -m "feat(dataverse): live verification controller + verify kinds"
```

In Dataverse-skills repo (commit the auth.py trace hook separately):

```bash
cd ../Dataverse-skills
git add scripts/auth.py
git commit -m "feat(auth): optional HTTP trace hook for SkillOpt eval verification"
```

---

### Task 19: Wire live items into `process_one`

**Files:**
- Modify: `skillopt/envs/dataverse/rollout.py` (extend `process_one` for live items)

- [ ] **Step 1: Add a `live_client_factory` parameter to `process_one`**

```python
def process_one(
    item: dict,
    out_root: str,
    skill_content: str,
    *,
    plugin_src_dir: str,
    target_skill_name: str,
    judge_client: JudgeClient,
    judge_deployment: str,
    exec_timeout: int = 180,
    max_completion_tokens: int = 16384,
    live_enabled: bool = True,
    live_client_factory=None,   # callable returning a DataverseClient
    live_env_url: str = "",
) -> dict:
    ...
```

- [ ] **Step 2: In `process_one`, set up the trace file before `run_target_exec`**

After `prepare_workspace`, BEFORE `run_target_exec`, if the item has a live block AND `live_enabled`:

```python
        is_live = bool(parsed.live and parsed.live.enabled and live_enabled)
        env_extra = {}
        trace_path = None
        guids_path = None
        if is_live:
            from skillopt.envs.dataverse.live import trace_paths_for
            trace_path, guids_path = trace_paths_for(out_root, item_id)
            # Ensure files exist (so the auth.py hook can append).
            open(trace_path, "w").close()
            open(guids_path, "w").close()
            env_extra["DATAVERSE_TRACE_FILE"] = trace_path
```

Pass `env_extra` into `run_target_exec` (you'll need to extend `copilot_harness.run_target_exec` to accept extra env vars — small change).

- [ ] **Step 3: After `run_target_exec`, run verification + teardown for live items**

```python
        live_pass_rate = None
        if is_live and live_client_factory:
            from skillopt.envs.dataverse.live import read_jsonl, run_verify, teardown
            trace_records = read_jsonl(trace_path)
            created_guids = read_jsonl(guids_path)
            dataverse_client = live_client_factory()
            live_result = run_verify(parsed.live.verify, trace_records, dataverse_client)
            live_pass_rate = live_result.pass_rate
            if parsed.live.teardown == "delete_session_records":
                orphans_path = os.path.join(out_root, "orphans.jsonl")
                teardown(created_guids, dataverse_client, orphans_path)
```

- [ ] **Step 4: Plumb `live_client_factory` from the adapter**

In `DataverseSkillAdapter.rollout`, create a closure that lazily builds a `DataverseClient` per worker:

```python
def _live_client_factory():
    import sys
    sys.path.insert(0, os.path.join(self.plugin_src_dir, ".github", "plugins", "dataverse", "scripts"))
    from auth import get_client
    return get_client("skillopt-eval")
```

Pass it into `run_batch` and through to `process_one`.

- [ ] **Step 5: Smoke test (manual)**

Add one live item to the dv_data JSONL temporarily (e.g., one of the existing items with a `live` block added). Run the smoke script. Verify that:
- A `dataverse_trace.jsonl` file appears under `predictions/<id>/`.
- `created_guids.jsonl` lists what the agent wrote.
- Teardown deletes those records (manual check via Dataverse UI or `pac` CLI).

- [ ] **Step 6: Commit**

```bash
git add skillopt/envs/dataverse/rollout.py skillopt/envs/dataverse/adapter.py skillopt/model/copilot_harness.py
git commit -m "feat(dataverse): live verification path in process_one"
```

---

### Task 20: Implement `sweep_orphans.py`

**Files:**
- Create: `skillopt/envs/dataverse/scripts/sweep_orphans.py`

- [ ] **Step 1: Implement**

```python
"""Clean up leaked GUIDs from crashed runs.

Reads orphans.jsonl files under outputs/ and deletes each GUID.
Also scans the Dataverse env for any sko_* records older than a cutoff.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def sweep_orphans_file(orphans_path: str, dataverse_client) -> dict[str, int]:
    counts = {"deleted": 0, "failed": 0}
    if not os.path.exists(orphans_path):
        return counts
    keep = []
    with open(orphans_path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    for r in records:
        try:
            dataverse_client.records.delete(r["table"], r["guid"])
            counts["deleted"] += 1
        except Exception as e:
            print(f"  WARN failed to delete {r['table']}/{r['guid']}: {e}", flush=True)
            counts["failed"] += 1
            keep.append(r)
    with open(orphans_path, "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r) + "\n")
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default="outputs", help="root directory to scan")
    ap.add_argument("--plugin-src", default=os.environ.get("DATAVERSE_PLUGIN_SRC", ""))
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(args.plugin_src, ".github", "plugins", "dataverse", "scripts"))
    from auth import get_client
    client = get_client("skillopt-sweep")

    total = {"deleted": 0, "failed": 0}
    for orphans in Path(args.outputs).rglob("orphans.jsonl"):
        print(f"sweeping {orphans} ...", flush=True)
        c = sweep_orphans_file(str(orphans), client)
        total["deleted"] += c["deleted"]
        total["failed"] += c["failed"]
    print(f"total: {total}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add skillopt/envs/dataverse/scripts/sweep_orphans.py
git commit -m "feat(dataverse): orphan-record sweep script"
```

---

## Phase 8 — Reflect integration

### Task 21: Author analyst prompt templates

**Files:**
- Create: `skillopt/envs/dataverse/prompts/analyst_error.md`
- Create: `skillopt/envs/dataverse/prompts/analyst_success.md`

- [ ] **Step 1: Author `analyst_error.md`**

Open `skillopt/envs/searchqa/prompts/analyst_error.md` as the template. Replace SearchQA-specific guidance with Dataverse-specific priors per the spec's Section 5:

```markdown
You are optimizing a Markdown skill document that teaches an AI agent how to use Microsoft Dataverse.

## Current skill document

{skill_content}

## Failed minibatch

{minibatch_results}

## Optimizer step buffer

{step_buffer_context}

## Cross-epoch memory

{meta_skill_context}

## Your job

Propose a small set of edit patches to the skill document above to make the failing items pass.

### Priors (guidance, not rules)

- Prefer additive edits to existing sections over wholesale rewrites.
- Antipattern failures (the agent fell into a documented trap):
  - Add a "Don't do this" example block with a `WRONG:` label, matching the skill's existing voice.
  - Reference the right pattern explicitly so the agent sees the contrast.
- Field-casing failures: tighten the casing table or add a row.
- Skill-contract failures (the agent paraphrased a wrong rule): trace which line in the skill led to the wrong paraphrase and patch that specific line.
- Do NOT edit other skills' content. If a fix logically belongs in dv-overview or another skill, note it in the patch metadata.

### Output format

Return a JSON list of edit patches. Each patch has: {{"target_file": "SKILL.md", "anchor": "exact text to find", "operation": "replace_with|insert_after|delete", "new_text": "..."}}.
```

(Use the exact format expected by `run_minibatch_reflect`. Check what SearchQA's analyst returns — match it.)

- [ ] **Step 2: Author `analyst_success.md`**

```markdown
You are reviewing skill-document changes that did NOT regress passing items.

## Current skill document

{skill_content}

## Items that passed

{minibatch_results}

## Your job

Briefly note (1-3 sentences) what about the current skill seems to be working for these items, so future optimization steps don't regress it. Be specific — point at the section/example/rule that's load-bearing.
```

- [ ] **Step 3: Commit**

```bash
git add skillopt/envs/dataverse/prompts/
git commit -m "feat(dataverse): analyst error + success prompts"
```

---

### Task 22: Implement `DataverseSkillAdapter.reflect` with patch-scope guardrail

**Files:**
- Modify: `skillopt/envs/dataverse/adapter.py`

- [ ] **Step 1: Replace the `NotImplementedError` reflect body**

Mirror `searchqa/adapter.py`'s reflect call, plus a guardrail filter:

```python
    def reflect(
        self,
        results: list[dict],
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict | None]:
        prediction_dir = kwargs.get("prediction_dir", os.path.join(out_dir, "predictions"))
        patches_dir = kwargs.get("patches_dir", os.path.join(out_dir, "patches"))
        random_seed = kwargs.get("random_seed")
        step_buffer_context = kwargs.get("step_buffer_context", "")
        meta_skill_context = kwargs.get("meta_skill_context", "")

        from skillopt.gradient.reflect import run_minibatch_reflect
        raw_patches = run_minibatch_reflect(
            results=results,
            skill_content=skill_content,
            prediction_dir=prediction_dir,
            patches_dir=patches_dir,
            workers=self.analyst_workers,
            failure_only=self.failure_only,
            minibatch_size=self.minibatch_size,
            edit_budget=self.edit_budget,
            random_seed=random_seed,
            error_system=self.get_error_minibatch_prompt(),
            success_system=self.get_success_minibatch_prompt(),
            step_buffer_context=step_buffer_context,
            meta_skill_context=meta_skill_context,
            update_mode=getattr(self, "_cfg", {}).get("skill_update_mode", "patch"),
        )
        # Patch-scope guardrail: only allow edits to the target skill's SKILL.md.
        return self._filter_in_scope(raw_patches, out_dir)

    def _filter_in_scope(self, patches: list, out_dir: str) -> list:
        in_scope = []
        out_of_scope = []
        for p in patches:
            if p is None:
                in_scope.append(p)
                continue
            target = (p.get("target_file") or "SKILL.md").lower() if isinstance(p, dict) else "skill.md"
            if target.endswith("skill.md") and "/" not in target.replace("\\", "/").strip("./"):
                in_scope.append(p)
            else:
                out_of_scope.append(p)
        if out_of_scope:
            path = os.path.join(out_dir, "out_of_scope_patches.jsonl")
            os.makedirs(out_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for p in out_of_scope:
                    f.write(json.dumps(p) + "\n")
        return in_scope

    def get_error_minibatch_prompt(self) -> str:
        from skillopt.prompts import load_prompt
        return load_prompt("analyst_error", env="dataverse")

    def get_success_minibatch_prompt(self) -> str:
        from skillopt.prompts import load_prompt
        return load_prompt("analyst_success", env="dataverse")

    def get_task_types(self) -> list[str]:
        return ["dataverse"]
```

Add `import json` at top of file.

- [ ] **Step 2: Verify the adapter still imports**

```bash
python -c "from skillopt.envs.dataverse.adapter import DataverseSkillAdapter; print('ok')"
```

- [ ] **Step 3: Commit**

```bash
git add skillopt/envs/dataverse/adapter.py
git commit -m "feat(dataverse): adapter.reflect with patch-scope guardrail"
```

---

## Phase 9 — Configs, env registration, train.py wiring

### Task 23: Author `configs/dataverse/_base.yaml`

**Files:**
- Create: `configs/dataverse/_base.yaml`

- [ ] **Step 1: Write the base**

```yaml
# Dataverse SkillOpt env — shared base for all per-skill configs.
_base_: ['../_base_/default.yaml']

model:
  target_backend: copilot_cli_exec
  optimizer_backend: openai_chat
  judge_backend: openai_chat

train:
  batch_size: 8
  accumulation: 1
  seed: 42

gradient:
  minibatch_size: 8
  merge_batch_size: 8
  analyst_workers: 8

optimizer:
  learning_rate: 3
  lr_scheduler: cosine
  skill_update_mode: patch
  use_slow_update: true
  use_meta_skill: true

evaluation:
  use_gate: true
  eval_test: true

env:
  name: dataverse
  workers: 24
  live_workers: 4
  exec_timeout: 240
  max_turns: 1
  max_completion_tokens: 16384
  live_enabled: true
  live_solution: SkillOptEvals
  live_prefix: sko
  record_bodies: false
```

- [ ] **Step 2: Commit**

```bash
git add configs/dataverse/_base.yaml
git commit -m "feat(config): dataverse base config"
```

---

### Task 24: Author `configs/dataverse/dv_data.yaml`

**Files:**
- Create: `configs/dataverse/dv_data.yaml`

- [ ] **Step 1: Write**

```yaml
_base_: ['_base.yaml']

env:
  skill_name: dv-data
  skill_init: ../../Dataverse-skills/.github/plugins/dataverse/skills/dv-data/SKILL.md
  plugin_src_dir: ../../Dataverse-skills
  split_mode: split_dir
  split_dir: data/dataverse/dv_data
```

- [ ] **Step 2: Commit**

```bash
git add configs/dataverse/dv_data.yaml
git commit -m "feat(config): dv_data skill config"
```

---

### Task 25: Register the dataverse env in the env registry

**Files:**
- Modify: `skillopt/envs/__init__.py`
- Modify: `skillopt/engine/trainer.py` (if env construction lives there — check first)

- [ ] **Step 1: Find where envs are registered**

```bash
grep -rn "searchqa\|SearchQAAdapter\|env_name\|BENCHMARK_REGISTRY" skillopt/envs/__init__.py skillopt/engine/
```

If a registry dict exists, add an entry for `"dataverse"` pointing at `DataverseSkillAdapter`. If envs are constructed via a factory function based on `env.name`, add the dataverse branch.

- [ ] **Step 2: Register**

Pattern (adapt to actual code):

```python
from skillopt.envs.dataverse.adapter import DataverseSkillAdapter

# In registry or factory:
"dataverse": DataverseSkillAdapter,
```

- [ ] **Step 3: Verify**

```bash
python -c "from skillopt.envs import get_env_adapter; a = get_env_adapter('dataverse', skill_name='dv-data', plugin_src_dir='.'); print(type(a).__name__)"
```

Expected: prints `DataverseSkillAdapter`.

- [ ] **Step 4: Commit**

```bash
git add skillopt/envs/__init__.py
git commit -m "feat(envs): register dataverse env in registry"
```

---

## Phase 10 — Setup & promote scripts

### Task 26: Implement `setup_live_env.py`

**Files:**
- Create: `skillopt/envs/dataverse/scripts/setup_live_env.py`

- [ ] **Step 1: Implement**

```python
"""One-time Dataverse env setup for SkillOpt evals.

Creates the SkillOptEvals solution + sko publisher, pre-seeds reference data.
Idempotent: re-running confirms state and reports drift.

Usage:
    python -m skillopt.envs.dataverse.scripts.setup_live_env
"""
from __future__ import annotations

import os
import sys


def main():
    plugin_src = os.environ["DATAVERSE_PLUGIN_SRC"]
    sys.path.insert(0, os.path.join(plugin_src, ".github", "plugins", "dataverse", "scripts"))
    from auth import get_client
    client = get_client("skillopt-setup")

    solution_name = os.environ.get("SKILLOPT_EVALS_SOLUTION", "SkillOptEvals")
    prefix = os.environ.get("SKILLOPT_EVALS_PREFIX", "sko")

    # 1) Ensure publisher
    existing = list(client.records.get("publisher", filter=f"uniquename eq '{prefix}'", select=["publisherid"]))
    if not existing:
        publisher_id = client.records.create("publisher", {
            "uniquename": prefix,
            "friendlyname": "SkillOpt Evals",
            "customizationprefix": prefix,
            "description": "Publisher for SkillOpt eval artifacts. Auto-created.",
        })
        print(f"created publisher {prefix} = {publisher_id}", flush=True)
    else:
        publisher_id = next(iter(existing[0]))["publisherid"]
        print(f"publisher {prefix} already exists ({publisher_id})", flush=True)

    # 2) Ensure solution
    existing = list(client.records.get("solution", filter=f"uniquename eq '{solution_name}'", select=["solutionid"]))
    if not existing:
        solution_id = client.records.create("solution", {
            "uniquename": solution_name,
            "friendlyname": "SkillOpt Evals",
            "version": "1.0.0.0",
            "publisherid@odata.bind": f"/publishers({publisher_id})",
        })
        print(f"created solution {solution_name} = {solution_id}", flush=True)
    else:
        print(f"solution {solution_name} already exists", flush=True)

    # 3) Pre-seed reference data
    # For now, log what would be seeded — actual seed logic depends on the eval-set
    # items being authored. Seed tables: sko_ref_account (500 rows), sko_ref_contact (1000 rows).
    print("seed reference data: TODO once eval set finalized", flush=True)
    print("setup complete", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add skillopt/envs/dataverse/scripts/setup_live_env.py
git commit -m "feat(dataverse): one-time live-env setup script"
```

---

### Task 27: Implement `promote.py`

**Files:**
- Create: `skillopt/envs/dataverse/scripts/promote.py`

- [ ] **Step 1: Implement**

```python
"""Copy best_skill.md into the Dataverse-skills plugin and open a draft PR.

Usage:
    python -m skillopt.envs.dataverse.scripts.promote \
        --run outputs/dv_data_run_01 \
        --plugin ../Dataverse-skills/.github/plugins/dataverse/skills/dv-data
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--branch", default="")
    args = ap.parse_args()

    best = Path(args.run) / "best_skill.md"
    if not best.exists():
        raise SystemExit(f"best_skill.md not found in {args.run}")

    dest = Path(args.plugin) / "SKILL.md"
    plugin_repo = next(p for p in dest.parents if (p / ".git").exists())
    skill_name = dest.parent.name
    branch = args.branch or f"skillopt/{skill_name}-{Path(args.run).name}"

    subprocess.check_call(["git", "-C", str(plugin_repo), "checkout", "-b", branch])
    shutil.copy(best, dest)
    subprocess.check_call(["git", "-C", str(plugin_repo), "add", str(dest.relative_to(plugin_repo))])
    subprocess.check_call(["git", "-C", str(plugin_repo), "commit", "-m", f"skill({skill_name}): SkillOpt-optimized update from {Path(args.run).name}"])
    subprocess.check_call(["git", "-C", str(plugin_repo), "push", "-u", "origin", branch])
    # Open a draft PR
    subprocess.check_call([
        "gh", "pr", "create", "--draft",
        "--title", f"skill({skill_name}): SkillOpt-optimized update",
        "--body", f"Generated by SkillOpt run `{Path(args.run).name}`. Eval deltas in run output.",
        "--repo", "microsoft/Dataverse-skills",
    ], cwd=str(plugin_repo))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add skillopt/envs/dataverse/scripts/promote.py
git commit -m "feat(dataverse): promote.py opens draft PR with best skill"
```

---

## Phase 11 — Eval authoring, noise-floor check, first training run

### Task 28: Author additional dv_data items per the methodology

**Files:**
- Modify: `../Dataverse-skills/evals/skillopt/dv_data.jsonl` (append items)

This is content authoring, not code. Follow the spec's "Eval Authoring Methodology" section verbatim. Target: 15-25 items total. The 3 seed items from migration count toward this.

- [ ] **Step 1: Mine `Dataverse-skills/.github/plugins/dataverse/skills/dv-data/SKILL.md` for items**

For each documented invariant, write the corresponding eval item using the schema from Task 7. Cover the categories per the spec methodology:
- ~5-8 happy_path items (basic CRUD, bulk create, upsert, lookup resolution, CSV import)
- ~4-6 antipattern_trap items (per-record loop trap, raw HTTP trap, wrong @odata.bind casing, chunking-too-small trap, missing required field trap)
- ~2-3 edge_case items (case sensitivity, navigation property naming, alternate-key body exclusion)
- 1 skill_contract item (preserve the existing data_003 pattern)
- ~3-5 cross_tool items (when to use MCP vs SDK vs Web API per dv-overview)

- [ ] **Step 2: Mine `static_checks.py` invariants**

For each CAT-1 through CAT-5 rule that applies to dv-data, write a runtime eval. E.g.:
- EVAL-PY-01 → "Quote me your auth import block" → assert correct sys.path.insert ordering
- EVAL-AUTH-01 → "What import do you use for the SDK client?" → assert `from auth import get_client`
- EVAL-PY-05 → "Write a bulk-create script" → assert no `get_token()` near `DataverseClient`

- [ ] **Step 3: Mark ~5 items as `live` with verify blocks**

Topics per the spec:
- bulk-create single-API-call check (`request_count` on `/CreateMultiple` ≤ 1, `row_count` exact)
- upsert idempotency (run prompt twice, row_count stays the same)
- lookup resolution (verify the FK is set correctly in the created record)
- CSV import with chunking (`request_count` on the table endpoint = ceil(n / chunk_size))

- [ ] **Step 4: Validate every item parses**

```bash
python -c "from skillopt.envs.dataverse.schema import load_jsonl; items = load_jsonl('../Dataverse-skills/evals/skillopt/dv_data.jsonl'); print(f'{len(items)} items, all parse')"
```

- [ ] **Step 5: Re-split**

```bash
python -m skillopt.envs.dataverse.scripts.split \
  --in ../Dataverse-skills/evals/skillopt/dv_data.jsonl \
  --out data/dataverse/dv_data \
  --ratio 80:10:10 --seed 42
```

Expected: ~12-20 train, ~1-3 val, ~1-3 test for 15-25 source items.

- [ ] **Step 6: Commit (Dataverse-skills repo)**

```bash
cd ../Dataverse-skills
git add evals/skillopt/dv_data.jsonl
git commit -m "feat(evals): hand-author 15-25 dv_data SkillOpt items (full v1 set)"
```

Commit the new split files in SkillOpt repo:

```bash
cd ../SkillOpt
git add data/dataverse/dv_data/
git commit -m "data: refresh dv_data split with full 15-25 item set"
```

---

### Task 29: Run setup_live_env against Aurora

**Files:** (no code edits — operational step)

- [ ] **Step 1: Confirm env vars**

```bash
echo $DATAVERSE_ENV_URL
echo $DATAVERSE_PLUGIN_SRC
echo $SKILLOPT_EVALS_SOLUTION
echo $SKILLOPT_EVALS_PREFIX
```

If any are unset, populate `.env` and source it.

- [ ] **Step 2: Run setup**

```bash
python -m skillopt.envs.dataverse.scripts.setup_live_env
```

Expected: prints created publisher + solution (or "already exists" on re-run). Verify in Power Apps maker portal: solution `SkillOptEvals` is visible.

- [ ] **Step 3: Confirm plugin install path**

```bash
copilot
# inside:
/plugin install dataverse@awesome-copilot
```

Verify install completes and `pac auth list` shows the Aurora env as active.

---

### Task 30: Noise-floor check

**Files:** (no code edits — uses existing `eval_only.py`)

Per the spec's noise-floor methodology.

- [ ] **Step 1: Run eval_only against current committed skill**

```bash
python scripts/eval_only.py \
  --config configs/dataverse/dv_data.yaml \
  --skill ../Dataverse-skills/.github/plugins/dataverse/skills/dv-data/SKILL.md \
  --split valid_unseen
```

Record `mean(soft)` and `count(hard=1)`.

- [ ] **Step 2: Create a deliberately regressed copy**

```bash
cp ../Dataverse-skills/.github/plugins/dataverse/skills/dv-data/SKILL.md /tmp/regressed_dv_data.md
# Manually edit /tmp/regressed_dv_data.md: delete the "Bulk Create" section.
# OR: replace "client.records.create(table, list_of_dicts)" with "loop per record and call create"
```

```bash
python scripts/eval_only.py \
  --config configs/dataverse/dv_data.yaml \
  --skill /tmp/regressed_dv_data.md \
  --split valid_unseen
```

Record results. **Score gap must be ≥ 0.15 on soft AND ≥ 2 items flipping hard.** If not, add more antipattern_trap items to the eval set and retry.

- [ ] **Step 3: Create a trivially regressed copy**

```bash
cp ../Dataverse-skills/.github/plugins/dataverse/skills/dv-data/SKILL.md /tmp/trivial_dv_data.md
# Add a typo fix or whitespace change ONLY.
sed -i 's/  / /g' /tmp/trivial_dv_data.md   # collapse double spaces, semantically identical
```

```bash
python scripts/eval_only.py \
  --config configs/dataverse/dv_data.yaml \
  --skill /tmp/trivial_dv_data.md \
  --split valid_unseen
```

**Score must not move materially.** If it does, replace flaky items in the eval set (look for items where the judge is volatile).

- [ ] **Step 4: Document the noise-floor results in the run README**

Create `outputs/noise_floor_dv_data.md` summarizing baseline + regressed + trivial deltas. This is the artifact you'll point at if the loop later misbehaves.

---

### Task 31: Run the first SkillOpt training job on dv-data

**Files:** (no code edits — operational)

- [ ] **Step 1: Sweep any orphans first**

```bash
python -m skillopt.envs.dataverse.scripts.sweep_orphans
```

- [ ] **Step 2: Run training**

```bash
python scripts/train.py \
  --config configs/dataverse/dv_data.yaml \
  --out_root outputs/dv_data_run_01
```

For the first run, override the YAML defaults to a single short epoch so you can validate end-to-end behavior fast:

```bash
python scripts/train.py \
  --config configs/dataverse/dv_data.yaml \
  --out_root outputs/dv_data_run_01 \
  --num_epochs 1 \
  --batch_size 4
```

Expected runtime: ~30-60 minutes for 1 epoch on ~16 items with response-only rollout. Live items add 1-2 minutes each.

- [ ] **Step 3: Verify outputs are coherent**

Check:
- `outputs/dv_data_run_01/skills/skill_v0001.md` exists (and differs from v0000)
- `outputs/dv_data_run_01/best_skill.md` exists
- `outputs/dv_data_run_01/history.json` shows score improvement (or at least no regression)
- `outputs/dv_data_run_01/steps/step_0001/predictions/<id>/judge_rationales.jsonl` is populated
- `outputs/dv_data_run_01/orphans.jsonl` is empty (or sweeps clean)
- `outputs/dv_data_run_01/steps/step_0001/out_of_scope_patches.jsonl` — note any cross-skill patches the loop wanted to apply

- [ ] **Step 4: Run eval_only on best_skill.md vs current committed**

```bash
python scripts/eval_only.py \
  --config configs/dataverse/dv_data.yaml \
  --skill outputs/dv_data_run_01/best_skill.md \
  --split valid_unseen
```

Compare `mean(soft)` and `count(hard=1)` against the Task 30 baseline. If better, proceed to promote. If not, investigate by reading the analyst patches and rationales for the failing items.

- [ ] **Step 5: Promote (only if eval improved)**

```bash
python -m skillopt.envs.dataverse.scripts.promote \
  --run outputs/dv_data_run_01 \
  --plugin ../Dataverse-skills/.github/plugins/dataverse/skills/dv-data
```

This opens a draft PR against `microsoft/Dataverse-skills`. Review the diff manually, add notes to the PR body about which categories improved, and request review.

---

## Validation Checklist

After completing all tasks above, verify:

- [ ] `pytest tests/ -v` — all green
- [ ] `python scripts/smoke_copilot.py` — exits 0 with `OK`
- [ ] `python scripts/smoke_dataverse_rollout.py` — reports per-item hard/soft on the migrated items
- [ ] `python -m skillopt.envs.dataverse.scripts.setup_live_env` — idempotent, exits 0
- [ ] First training run produces a different `best_skill.md` than the input
- [ ] Eval-only against `best_skill.md` improves on baseline
- [ ] Promote opens a draft PR successfully

## Out of scope (NOT in this plan — content/operational follow-up)

- Authoring eval items for the other 7 skills (~10-12 person-days, repeat Task 28 per skill).
- Live `setup_live_env` reference-data seeding once the eval-set demands it (Task 26 stubs this).
- Eval-only CI hook on Dataverse-skills PRs (deferred per spec).
- Caching judge calls (deferred per spec).
- Softening the patch-scope guardrail (revisit after 3-5 runs per spec).
