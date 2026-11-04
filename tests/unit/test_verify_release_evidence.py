"""Unit tests for tools/verify_release_evidence.py.

RED first: the verifier must catch a changed source input, not just count files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from verify_release_evidence import verify_manifest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_repo(tmp_path: Path, *, source: str = "print('hi')\n"):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "build").mkdir()
    src = root / "src" / "a.py"
    src.write_text(source, encoding="utf-8")
    exe = root / "build" / "app.exe"
    exe.write_bytes(b"binary")
    manifest = root / "build" / "release-manifest.json"
    manifest.write_text(
        json.dumps({"base_commit": "abc123", "inputs": {"src/a.py": _sha(src)}}),
        encoding="utf-8",
    )
    return root, manifest, exe, src


def test_verify_manifest_happy_path(tmp_path: Path):
    root, manifest, exe, _ = _make_repo(tmp_path)
    result = verify_manifest(root, manifest, [exe])
    assert result["status"] == "PASS"
    assert result["base_commit"] == "abc123"
    assert result["inputs_checked"] == 1
    assert result["inputs_mismatch"] == []
    assert result["artifacts_missing"] == []
    assert result["artifacts"] == [
        {"name": "app.exe", "sha256": _sha(exe), "bytes": 6}
    ]


def test_verify_manifest_detects_changed_source(tmp_path: Path):
    root, manifest, exe, src = _make_repo(tmp_path)
    src.write_text("print('tampered')\n", encoding="utf-8")
    result = verify_manifest(root, manifest, [exe])
    assert result["status"] == "FAIL"
    assert result["inputs_mismatch"] == ["src/a.py"]


def test_verify_manifest_detects_missing_input(tmp_path: Path):
    root, manifest, exe, src = _make_repo(tmp_path)
    src.unlink()
    result = verify_manifest(root, manifest, [exe])
    assert result["status"] == "FAIL"
    assert "src/a.py" in result["inputs_mismatch"]


def test_verify_manifest_reports_missing_artifact(tmp_path: Path):
    root, manifest, _, _ = _make_repo(tmp_path)
    result = verify_manifest(root, manifest, [root / "build" / "gone.exe"])
    assert result["status"] == "FAIL"
    assert result["artifacts_missing"] == ["gone.exe"]
