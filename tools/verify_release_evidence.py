#!/usr/bin/env python3
"""RC3 release evidence verifier.

Answers one question: does a packaged build still match the source inputs and
commit recorded in its release manifest? Hashes are computed from bytes on disk,
never from a claim in a document. The SHA256SUMS it writes covers artifacts only
(never the checksum file itself), matching the Linux RC3 handoff format.

Git state is attached by the CLI, not by verify_manifest(), so the pure function
stays usable on a throwaway directory with no .git (see unit tests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(root: Path, manifest_path: Path, artifacts: list[Path]) -> dict:
    """Hash every manifest input and every artifact; report mismatches by path."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs: dict[str, str] = data.get("inputs", {})
    mismatches: list[str] = []
    for rel in sorted(inputs):
        candidate = root / rel
        if not candidate.is_file():
            mismatches.append(rel)
            continue
        if sha256_file(candidate) != inputs[rel]:
            mismatches.append(rel)

    hashed: list[dict] = []
    missing: list[str] = []
    for artifact in artifacts:
        if not artifact.is_file():
            missing.append(artifact.name)
            continue
        hashed.append(
            {
                "name": artifact.name,
                "sha256": sha256_file(artifact),
                "bytes": artifact.stat().st_size,
            }
        )

    return {
        "status": "PASS" if not mismatches and not missing else "FAIL",
        "base_commit": data.get("base_commit", ""),
        "manifest_working_tree_dirty": bool(data.get("working_tree_dirty", False)),
        "captured_at_utc": data.get("captured_at_utc"),
        "inputs_checked": len(inputs),
        "inputs_mismatch": mismatches,
        "artifacts": hashed,
        "artifacts_missing": missing,
    }


def attach_git_state(repo: Path, result: dict) -> dict:
    """Bind evidence to the build's source commit. Fail closed.

    Evidence files are committed *after* the build, so HEAD is a descendant of
    base_commit, not equal to it. Requiring equality would fail every honest
    verification. What must hold: the recorded source commit exists, is an
    ancestor of HEAD, and the tree was clean when hashes were taken.
    """
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    exists = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", result["base_commit"] + "^{commit}"],
        capture_output=True, text=True, check=False,
    ).returncode == 0
    ancestor = exists and bool(head) and subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", result["base_commit"], head],
        capture_output=True, text=True, check=False,
    ).returncode == 0
    result["head_commit"] = head
    result["source_commit_exists"] = exists
    result["source_commit_is_ancestor_of_head"] = bool(ancestor)
    result["working_tree_clean"] = dirty == ""
    if not ancestor or not result["working_tree_clean"]:
        result["status"] = "FAIL"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, nargs="*", default=[])
    parser.add_argument("--sha256sums", type=Path, default=None,
                        help="write '<sha256>  <filename>' lines (LF, artifacts only)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = verify_manifest(args.repo.resolve(), args.manifest.resolve(), args.artifacts)
    result = attach_git_state(args.repo.resolve(), result)

    if args.sha256sums:
        lines = [f"{a['sha256']}  {a['name']}" for a in result["artifacts"]]
        args.sha256sums.parent.mkdir(parents=True, exist_ok=True)
        args.sha256sums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
