"""One-time Dataverse env setup for SkillOpt evals.

Creates the SkillOptEvals solution + sko publisher if missing.
Idempotent: re-running confirms state and reports drift.

Pre-seed reference data is left as a TODO — it depends on the eval set's
live-item requirements, which are decided in Task 28.

Usage:
    python -m skillopt.envs.dataverse.scripts.setup_live_env
"""
from __future__ import annotations

import os
import sys


def main():
    plugin_src = os.environ.get("DATAVERSE_PLUGIN_SRC")
    if not plugin_src:
        raise SystemExit("DATAVERSE_PLUGIN_SRC env var is required (path to Dataverse-skills clone)")

    sys.path.insert(0, os.path.join(plugin_src, ".github", "plugins", "dataverse", "scripts"))
    from auth import get_client  # noqa: E402 — late import via runtime sys.path
    client = get_client("dv-data")

    solution_name = os.environ.get("SKILLOPT_EVALS_SOLUTION", "SkillOptEvals")
    prefix = os.environ.get("SKILLOPT_EVALS_PREFIX", "sko")

    # 1) Ensure publisher exists.
    existing = list(client.records.get("publisher", filter=f"uniquename eq '{prefix}'", select=["publisherid"]))
    publisher_id: str
    if not existing:
        publisher_id = client.records.create("publisher", {
            "uniquename": prefix,
            "friendlyname": "SkillOpt Evals",
            "customizationprefix": prefix,
            "description": "Publisher for SkillOpt eval artifacts. Auto-created.",
        })
        print(f"created publisher {prefix} = {publisher_id}", flush=True)
    else:
        publisher_id = existing[0][0]["publisherid"] if existing[0] else None
        print(f"publisher {prefix} already exists ({publisher_id})", flush=True)

    # 2) Ensure solution exists.
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

    # 3) TODO once eval set finalized: pre-seed reference data
    # (e.g., 500 sko_ref_account rows, 1000 sko_ref_contact rows).
    # Skipped in v1 — gets filled in when Task 28 finalizes which live items need
    # what reference data.
    print("setup complete (reference data seeding deferred — see Task 28)", flush=True)


if __name__ == "__main__":
    main()
