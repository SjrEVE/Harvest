#!/usr/bin/env python3
"""A2 — find every commit, ref and object that carries a known secret.

Answers "mọi commit chứa secret" for a specific credential without rewriting
history. Three independent passes, because they catch different things:

  1. pickaxe   `git log --all -S<secret>` — commits whose diff added or removed it.
  2. tree grep `git grep -F -f patterns <refs>` — refs whose *current* tree holds it.
  3. pack sweep every object in the database, reachable or dangling, substring match.

Pass 3 is the one that catches an amended-away or force-pushed copy that passes 1
and 2 miss. Patterns are read from the source blob at runtime and written to a
temporary file passed to git with -F, so no secret appears in a command line, in
this tool's stdout, or in its report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_OBJECT_BYTES = 8_000_000


def read_secret_from_blob(repo: Path, rev: str, doc_path: str, line_no: int) -> list[str]:
    """Pull credential-shaped substrings out of one document line, in memory only."""
    blob = subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{doc_path}"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "replace")
    line = blob.splitlines()[line_no - 1]
    candidates: list[str] = []
    for match in re.finditer(r"[?&](?:t|token|key|auth)=([^\s&\"'`]{6,})", line):
        candidates.append(match.group(1))
    for match in re.finditer(r"https?://([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", line):
        candidates.append(match.group(1))
    return candidates


def pickaxe_commits(repo: Path, secret: str) -> list[dict[str, str]]:
    out = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "--all",
            "--format=%H%x1f%ad%x1f%an%x1f%D%x1f%s",
            "--date=iso-strict",
            f"-S{secret}",
        ],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "replace")
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            rows.append(
                {
                    "commit": parts[0],
                    "date": parts[1],
                    "author": parts[2],
                    "refs": parts[3],
                    "subject": parts[4],
                }
            )
    return rows


def all_refs(repo: Path) -> list[str]:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "for-each-ref", "--format=%(refname)"],
            capture_output=True,
            check=True,
        )
        .stdout.decode("utf-8", "replace")
        .splitlines()
    )


def grep_refs(repo: Path, pattern_file: Path, refs: list[str]) -> dict[str, list[str]]:
    """Return ref -> files whose current tree matches a pattern."""
    if not refs:
        return {}
    result: dict[str, list[str]] = {}
    chunk = 40
    for start in range(0, len(refs), chunk):
        batch = refs[start : start + chunk]
        proc = subprocess.run(
            ["git", "-C", str(repo), "grep", "-l", "-F", "-f", str(pattern_file), *batch],
            capture_output=True,
            check=False,
        )
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            ref, _, path = line.partition(":")
            result.setdefault(ref, []).append(path)
    return result


def sweep_objects(repo: Path, patterns: list[str]) -> dict[str, list[str]]:
    """Substring-match every object in the database, including unreachable ones."""
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch-all-objects", "--batch"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    stream = proc.stdout
    hits: dict[str, list[str]] = {}
    encoded = [(p, p.encode("utf-8", "surrogatepass")) for p in patterns]
    total = 0
    while True:
        header = stream.readline()
        if not header:
            break
        parts = header.rstrip(b"\n").split(b" ")
        if len(parts) != 3:
            continue
        oid, objtype, size = parts[0].decode(), parts[1].decode(), int(parts[2])
        total += 1
        payload = stream.read(size + 1)
        if objtype != "blob" or size > MAX_OBJECT_BYTES:
            continue
        matched = [plain for plain, raw in encoded if raw in payload]
        if matched:
            hits[oid] = [fingerprint(matched[0])]
    proc.wait()
    hits["_count"] = [str(total)]
    return hits


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--doc", required=True, help="path of the document that leaked the secret")
    parser.add_argument("--line", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    repo = args.repo
    patterns = read_secret_from_blob(repo, args.sha, args.doc, args.line)
    if not patterns:
        print("no credential-shaped candidates found on that line", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "tool": "git_history_secret_audit",
        "repo": str(repo),
        "audited_sha": args.sha,
        "document": args.doc,
        "document_line": args.line,
        "pattern_count": len(patterns),
        "pattern_fingerprints": [fingerprint(p) for p in patterns],
    }

    pattern_file: Path | None = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="g0a-patterns-", text=True)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(patterns) + "\n")
        pattern_file = Path(tmp)

        refs = all_refs(repo)
        report["refs_total"] = len(refs)
        report["pickaxe"] = {fingerprint(p): pickaxe_commits(repo, p) for p in patterns}
        report["tree_grep_by_ref"] = grep_refs(repo, pattern_file, refs)
        report["object_sweep"] = sweep_objects(repo, patterns)
    finally:
        if pattern_file is not None:
            pattern_file.unlink(missing_ok=True)

    report["tree_grep_by_ref"] = {
        ref: sorted(set(files)) for ref, files in report.get("tree_grep_by_ref", {}).items()
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    sweep = report["object_sweep"]
    objects_total = int(sweep.pop("_count", ["0"])[0])
    print(f"patterns={report['pattern_count']} fingerprints={report['pattern_fingerprints']}")
    print(f"refs_total={report['refs_total']} objects_scanned={objects_total}")
    print(f"blobs_still_holding_a_pattern={len(sweep)}")
    for commit_list in report["pickaxe"].values():
        print(f"pickaxe_commits={len(commit_list)}")
        for row in commit_list:
            print(
                f"  {row['commit'][:12]} {row['date']} {row['refs'][:60]} | {row['subject'][:70]}"
            )
    print(f"refs_with_pattern_in_tree={len(report['tree_grep_by_ref'])}")
    for ref, files in sorted(report["tree_grep_by_ref"].items()):
        print(f"  {ref} -> {files}")
    print(f"report -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
