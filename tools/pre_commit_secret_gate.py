#!/usr/bin/env python3
"""A7 — pre-commit gate: scan staged blobs for secret material.

Scans what is actually being committed (`git show :path`), not the working tree,
so a file edited after staging cannot smuggle a different payload through.

Findings print masked. Exit 1 blocks the commit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from secret_scan import scan_text

BINARY_SNIFF = 8192
MAX_FILE_BYTES = 4_000_000


def staged_paths() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "replace")
    return [line for line in out.splitlines() if line]


def staged_blob(path: str) -> bytes | None:
    result = subprocess.run(["git", "show", f":{path}"], capture_output=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def main() -> int:
    paths = staged_paths()
    if not paths:
        return 0
    findings = []
    for path in paths:
        blob = staged_blob(path)
        if blob is None or len(blob) > MAX_FILE_BYTES or b"\x00" in blob[:BINARY_SNIFF]:
            continue
        findings.extend(scan_text(blob.decode("utf-8", "replace"), path, "gate"))

    if not findings:
        # Hooks write status to stderr: git routes hook stdout in ways that make
        # "assert on the success line" unreliable across versions.
        print(f"secret-gate: OK ({len(paths)} staged file(s) scanned, 0 findings)", file=sys.stderr)
        return 0

    print(
        f"secret-gate: BLOCKED - {len(findings)} possible secret(s) in staged content",
        file=sys.stderr,
    )
    for finding in findings:
        print(
            f"  {finding.location}:{finding.line}  rule={finding.rule}  "
            f"len={finding.length}  fp={finding.fingerprint}  {finding.preview}",
            file=sys.stderr,
        )
    print(
        "Fix: remove the credential from the file and rotate it wherever it was issued.\n"
        "If this is a deliberate test fixture or a documented placeholder, mark the line\n"
        "with a trailing comment containing 'secret-scan:allow'.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
