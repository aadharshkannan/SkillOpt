"""Tests for the orphan sweeper — mocks the Dataverse client."""
import json
from pathlib import Path
from unittest.mock import MagicMock

from skillopt.envs.dataverse.scripts.sweep_orphans import sweep_orphans_file


def test_sweep_deletes_all_when_successful(tmp_path: Path):
    orphans = tmp_path / "orphans.jsonl"
    with orphans.open("w") as f:
        for i in range(3):
            f.write(json.dumps({"table": "sko_eval", "guid": f"guid-{i}"}) + "\n")
    client = MagicMock()
    client.records.delete.return_value = None
    counts = sweep_orphans_file(str(orphans), client)
    assert counts["deleted"] == 3
    assert counts["failed"] == 0
    # File should be empty now (no keepers)
    assert orphans.read_text().strip() == ""


def test_sweep_keeps_failures(tmp_path: Path):
    orphans = tmp_path / "orphans.jsonl"
    with orphans.open("w") as f:
        for i in range(3):
            f.write(json.dumps({"table": "sko_eval", "guid": f"guid-{i}"}) + "\n")
    client = MagicMock()
    # First call succeeds, second raises, third succeeds.
    client.records.delete.side_effect = [None, RuntimeError("transient"), None]
    counts = sweep_orphans_file(str(orphans), client)
    assert counts["deleted"] == 2
    assert counts["failed"] == 1
    # Only the failure should remain in the file.
    remaining = [json.loads(line) for line in orphans.read_text().strip().splitlines()]
    assert len(remaining) == 1
    assert remaining[0]["guid"] == "guid-1"


def test_sweep_missing_file_returns_zero(tmp_path: Path):
    counts = sweep_orphans_file(str(tmp_path / "does-not-exist.jsonl"), MagicMock())
    assert counts == {"deleted": 0, "failed": 0}
