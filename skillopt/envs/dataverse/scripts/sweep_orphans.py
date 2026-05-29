"""Clean up leaked GUIDs from crashed SkillOpt training runs.

Reads orphans.jsonl files under outputs/ and deletes each GUID via the
Dataverse SDK. Records that fail to delete stay in orphans.jsonl for the
next sweep.

Usage:
    python -m skillopt.envs.dataverse.scripts.sweep_orphans
    python -m skillopt.envs.dataverse.scripts.sweep_orphans --outputs outputs/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def sweep_orphans_file(orphans_path: str, dataverse_client) -> dict[str, int]:
    """Delete every GUID listed in orphans_path. Failures stay in the file."""
    counts = {"deleted": 0, "failed": 0}
    if not os.path.exists(orphans_path):
        return counts
    with open(orphans_path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    keep = []
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
    ap.add_argument("--outputs", default="outputs", help="root directory to scan for orphans.jsonl files")
    ap.add_argument("--plugin-src", default=os.environ.get("DATAVERSE_PLUGIN_SRC", ""))
    args = ap.parse_args()

    if not args.plugin_src:
        raise SystemExit("--plugin-src or DATAVERSE_PLUGIN_SRC env var required (path to Dataverse-skills clone)")

    sys.path.insert(0, os.path.join(args.plugin_src, ".github", "plugins", "dataverse", "scripts"))
    from auth import get_client  # noqa: E402 — late import
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
