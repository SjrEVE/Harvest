"""Bind the packaged executables to the exact source inputs, including uncommitted work."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inputs() -> dict[str, str]:
    files = [ROOT / name for name in ("pyproject.toml", "uv.lock", "THIRD_PARTY_NOTICES.txt")]
    for directory in ("src", "packaging", "tools", "LICENSES", "docs/ddl"):
        files.extend(path for path in (ROOT / directory).rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts and path.suffix != ".pyc")
    return {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted(files)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("capture", "seal", "installer"))
    parser.add_argument("--installer", type=Path)
    args = parser.parse_args()
    captured = ROOT / "build" / "source-manifest.json"
    if args.action == "capture":
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
        report = {"base_commit": head, "working_tree_dirty": dirty,
                  "captured_at_utc": datetime.now(UTC).isoformat(), "inputs": inputs()}
        captured.parent.mkdir(exist_ok=True)
        captured.write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        report = json.loads(captured.read_text(encoding="utf-8"))
        if report["inputs"] != inputs():
            raise RuntimeError("source changed during/after build; refusing to attest these binaries")
        report["artifacts"] = {
            name: sha(ROOT / "build" / "stage" / name)
            for name in ("SanThauDataStudio.exe", "SanThauCollector.exe")
        }
        if args.action == "installer":
            if args.installer is None:
                parser.error("installer action requires --installer")
            report["artifacts"][args.installer.name] = sha(args.installer)
            target = ROOT / "build" / "release-manifest.json"
        else:
            target = ROOT / "build" / "stage" / "build-provenance.json"
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("MANIFEST_OK", args.action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
