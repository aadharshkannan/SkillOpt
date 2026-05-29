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
