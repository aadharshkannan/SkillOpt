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

    def setup(self, cfg: dict) -> None:
        """Override to allow split_mode override from config."""
        # Allow split_mode to be overridden from cfg even if set in constructor
        if "split_mode" in cfg:
            self.split_mode = cfg.get("split_mode", "ratio")
        super().setup(cfg)

    def load_raw_items(self, data_path: str) -> list[dict]:
        return _load_items(data_path)
