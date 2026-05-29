"""End-to-end smoke test for the dv-data rollout.

Runs ONE eval item from the test split through the full rollout pipeline
with a real Copilot CLI invocation but a mocked judge (since no Azure
OpenAI endpoint is configured in this environment).

Confirms:
  - The Copilot CLI binary is on PATH and authenticated
  - The Dataverse-skills plugin tree can be copied and the dv-data SKILL.md swapped
  - copilot --plugin-dir ... loads the swapped skill and the agent responds
  - The response flows through deterministic checks and reward composition
  - A result dict with hard/soft/response/fail_reason is produced

Does NOT verify:
  - Live verification (no Aurora env configured)
  - Real judge LLM calls (would need JUDGE_AZURE_OPENAI_* env vars)

Run:  python scripts/smoke_dv_data_rollout.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


def main():
    plugin_src = os.path.expanduser(
        os.environ.get(
            "DATAVERSE_PLUGIN_SRC_DIR",
            "C:/Users/aadkannan/source/repos/Dataverse-skills/.github/plugins/dataverse",
        )
    )
    if not os.path.isdir(plugin_src):
        print(f"ERROR: plugin dir not found: {plugin_src}", file=sys.stderr)
        sys.exit(2)

    # Load one item from the test split.
    test_items_path = Path("data/dataverse/dv_data/test/items.json")
    if not test_items_path.exists():
        print(f"ERROR: {test_items_path} not found — run split first", file=sys.stderr)
        sys.exit(2)
    with test_items_path.open(encoding="utf-8") as f:
        items = json.load(f)
    if not items:
        print("ERROR: empty test split", file=sys.stderr)
        sys.exit(2)
    # Skip live items for the smoke; we have no Aurora env.
    items = [it for it in items if not (it.get("live") and it["live"].get("enabled"))]
    if not items:
        print("ERROR: no response-only items in test split", file=sys.stderr)
        sys.exit(2)
    item = items[0]
    print(f"Selected item: {item['id']} (category={item['category']})")

    # Load the current dv-data SKILL.md
    skill_md_path = Path(plugin_src) / "skills" / "dv-data" / "SKILL.md"
    skill_content = skill_md_path.read_text(encoding="utf-8")
    print(f"Loaded dv-data SKILL.md ({len(skill_content)} chars)")

    # Mock the judge — every claim scored 1.0.
    fake_judge = MagicMock()
    fake_judge.chat.return_value = "1.0 - mocked judge always passes"

    # Configure Copilot to use gpt-5.5 (OpenAI-only).
    from skillopt.model.backend_config import set_target_backend, configure_copilot_cli_exec

    set_target_backend("copilot_cli_exec")
    configure_copilot_cli_exec(model="gpt-5.5", effort="medium")

    from skillopt.envs.dataverse.rollout import process_one

    with tempfile.TemporaryDirectory() as out_dir:
        print(f"out_dir: {out_dir}")
        print("Invoking Copilot via process_one ...")
        result = process_one(
            item,
            out_root=out_dir,
            skill_content=skill_content,
            plugin_src_dir=plugin_src,
            target_skill_name="dv-data",
            judge_client=fake_judge,
            judge_deployment="mocked",
            exec_timeout=300,
            live_enabled=False,
        )

    print("=" * 60)
    print(f"hard:            {result['hard']}")
    print(f"soft:            {result['soft']:.3f}")
    print(f"agent_ok:        {result['agent_ok']}")
    print(f"n_turns:         {result['n_turns']}")
    print(f"fail_reason:     {result['fail_reason'][:200] or '(none)'}")
    print(f"response (head): {result['response'][:300].strip()}")
    print(f"predicted_answer: {result['predicted_answer'][:120]}")
    print("=" * 60)

    if not result["agent_ok"]:
        print("FAIL: agent did not produce a response. Check Copilot CLI auth.")
        sys.exit(1)

    print("OK: end-to-end rollout produced a valid result dict.")


if __name__ == "__main__":
    main()
