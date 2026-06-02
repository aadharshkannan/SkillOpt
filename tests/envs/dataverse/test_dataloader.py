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
