"""The actions exposed by Settings run on the collector process, using real data."""

from __future__ import annotations

import csv
from pathlib import Path

from test_release_regressions import seed


def test_backup_restore_and_export_roundtrip(tmp_path: Path) -> None:
    from santhau_studio.workers.maintenance import perform

    root = tmp_path / "data"
    seed(root)
    saved = perform("backup", root)
    assert saved["ok"] and Path(saved["path"]).is_dir()
    fresh = tmp_path / "restored"
    restored = perform("restore", fresh, saved["path"])
    assert restored["ok"], restored
    exported = perform("export", fresh)
    assert exported["ok"], exported
    with Path(exported["path"]).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["natural_key"] == "RC001"
    verified = perform("verify", fresh)
    assert verified["ok"], verified
