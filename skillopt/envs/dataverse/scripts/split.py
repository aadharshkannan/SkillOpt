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
