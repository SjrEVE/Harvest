#!/usr/bin/env python3
"""Secret scanner for the G0-A quarantine audit and the pre-commit gate.

Two modes:
  gate  — high precision only, used by the pre-commit hook. A finding here blocks a commit.
  audit — gate plus a contextual entropy heuristic, used by the one-off quarantine sweeps.

Findings are always reported masked (rule, location, line, length, entropy,
sha256 fingerprint, truncated prefix). Secret material never reaches stdout, so
this tool's output is safe to keep in a committed evidence report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

SCANNER_VERSION = 2
EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

# The "secret" group must isolate credential material so masking is exact.
PRECISE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "url_query_token",
        re.compile(
            r"[?&](?:t|token|access_token|api_?key|key|auth|sig|signature)=(?P<secret>[^\s&\"'`]{6,})"
        ),
    ),
    (
        "credential_in_url",
        re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@\"']+:(?P<secret>[^/\s@\"]{3,})@", re.IGNORECASE),
    ),
    ("private_key_block", re.compile(r"-----BEGIN (?P<secret>[A-Z ]*PRIVATE KEY)-----")),
    (
        "bearer_authorization",
        re.compile(
            r"Authoriz\w*\s*[:,=]?\s*[\"']?(?:Bearer|Basic|Token)\s+(?P<secret>[A-Za-z0-9+/_.\-]{8,}=*)",
            re.IGNORECASE,
        ),
    ),
    (
        "known_provider_key",
        re.compile(
            r"(?P<secret>(?:sk|pk|rk|api|sec)[-_.][A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"xox[abprs]-[A-Za-z0-9\-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|"
            r"SG\.[A-Za-z0-9_\-]{20,}|npm_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_\-]{20,}|"
            r"ya29\.[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35})"
        ),
    ),
    (
        "assigned_secret_literal",
        re.compile(
            r"(?P<name>[A-Za-z0-9_.\-]*(?:token|secret|password|passwd|pwd|api_?key|access_?key|"
            r"private_?key|session_?id|cookie|credential|_psk|authticket))[A-Za-z0-9_.\-]*"
            r"\s*(?:=|:|,|\s)\s*[\"'](?P<secret>[^\"'\n]{4,})[\"']"
        ),
    ),
    (
        "env_assignment_secret",
        re.compile(
            r"^(?P<name>[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_?KEY|ACCESS_?KEY|CREDENTIAL|PRIVATE_?KEY)[A-Z0-9_]*)"
            r"\s*=\s*(?P<secret>\S{4,})\s*$"
        ),
    ),
    (
        "cookie_header",
        re.compile(
            r"(?:Set-)?Cookie\s*[:=]\s*[\"']?(?P<secret>[A-Za-z0-9%_=.\-]{12,})[\"']?",
            re.IGNORECASE,
        ),
    ),
    (
        "dsn_with_password",
        re.compile(
            r"(?P<secret>(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|mssql|redis|amqp)://[^\s\"']*?[^\s\"'/@]+:"
            r"[^\s\"'@]+@[^\s\"']+)"
        ),
    ),
)

# Secret material sitting in prose/JSON without quotes.
LOOSE_ASSIGNMENT_RULE = (
    "loose_secret_assignment",
    re.compile(
        r"(?P<name>[A-Za-z0-9_.\-]*(?:token|secret|password|passwd|api_?key|access_?key|session|cookie)[A-Za-z0-9_.\-]*)"
        r"\s*(?:=|:)\s*[\"']?(?P<secret>[A-Za-z0-9+/_.\-]{8,})[\"']?"
    ),
)

CONTEXT_KEYWORDS = re.compile(
    r"(token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key|private[_-]?key|session|cookie|"
    r"credential|authorization|bearer|auth|psk|webhook|dsn|connection[_-]?string)",
    re.IGNORECASE,
)

# Values that are hashes, digests or identifiers rather than credentials.
HASH_SHAPED = (
    re.compile(r"^[0-9a-fA-F]{7}$"),
    re.compile(r"^[0-9a-fA-F]{40}$"),
    re.compile(r"^[0-9a-fA-F]{64}$"),
)
VARIABLE_SHAPED = re.compile(r"^(?:\$|\{\{|\$\{|%[A-Z0-9_]+%|<|\()")
# An f-string/templating placeholder is code, not credential material.
INTERPOLATION_MARKER = re.compile(r"[{}]")
CODE_REFERENCE = re.compile(
    r"^(?:self\.|cls\.|args\.|kwargs\.|env\.|os\.|os\.)[A-Za-z0-9_.\[\]\"'\-]*$|"
    r"^(?:https?|file|ws|wss)://|"
    r"^[A-Za-z0-9_.\-\[\]]+\.(?:py|md|json|jsonl|gz|bin|sqlite|txt|csv|tsv|html|toml|ya?ml|lock|service|timer|qml|exe|zip)$"
)
PLACEHOLDER = re.compile(
    r"(?:example|changeme|change_me|your[_-]?here|your[_-]?token|placeholder|dummy|fake|sample|redacted|"
    r"masked|notarealsecret|insert[_-]?here|todo|xxx+|\*{3,}|-{3,}|0{6,})",
    re.IGNORECASE,
)
ALLOWED_TEST_MARKER = "secret-scan:allow"
# Whole-file opt-out for files whose purpose is to hold secret-shaped test material.
# Line markers do not survive a formatter reflowing a data table, which would make
# the gate block its own test suite. The directive is greppable and shows in review.
FILE_ALLOW_MARKER = "secret-scan:allow-file"
MIN_GATE_ASSIGN_ENTROPY = 2.6
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".gradle",
        "target",
        "site-packages",
        ".uv-cache",
        ".mypy",
    }
)
TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".rst",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".cfg",
        ".ini",
        ".env",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        ".sql",
        ".html",
        ".htm",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".qml",
        ".qrc",
        ".iss",
        ".service",
        ".timer",
        ".lock",
        ".csv",
        ".tsv",
        ".template",
        ".tpl",
    }
)
NO_SUFFIX_OK = frozenset(
    {".env", ".env.example", ".env.local", ".gitignore", ".gitattributes", "dockerfile", "makefile"}
)


@dataclass
class Finding:
    rule: str
    location: str
    line: int
    length: int
    fingerprint: str
    preview: str
    entropy: float
    name_hint: str | None = None
    secret: str = field(default="", repr=False)

    def as_json(self) -> dict[str, object]:
        return {
            "scanner_version": SCANNER_VERSION,
            "rule": self.rule,
            "location": self.location,
            "line": self.line,
            "length": self.length,
            "fingerprint": self.fingerprint,
            "preview": self.preview,
            "entropy": round(self.entropy, 3),
            "name_hint": self.name_hint,
        }


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: Counter[str] = Counter(value)
    total = len(value)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def is_hash_shaped(value: str) -> bool:
    return any(pattern.match(value) for pattern in HASH_SHAPED)


def is_variable_shaped(value: str) -> bool:
    return bool(VARIABLE_SHAPED.match(value)) or bool(INTERPOLATION_MARKER.search(value))


def is_code_reference(value: str) -> bool:
    return bool(CODE_REFERENCE.match(value)) or "/" in value or "\\" in value


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.search(value)) or _single_word_no_digit(value)


def _single_word_no_digit(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z_\-]{0,40}", value))


def has_low_information(value: str) -> bool:
    stripped = value.replace("_", "").replace("-", "").replace(".", "")
    return stripped.isdigit() or len(set(stripped)) <= 3


def evaluate(secret: str, line: str, rule: str, mode: str) -> bool:
    """Return True when the candidate should be reported."""
    if is_variable_shaped(secret) or is_code_reference(secret) or is_placeholder(secret):
        return False
    if has_low_information(secret):
        return False
    if rule == "private_key_block":
        return True
    if rule == "known_provider_key":
        return True
    if is_hash_shaped(secret) and rule not in {
        "url_query_token",
        "credential_in_url",
        "dsn_with_password",
    }:
        return False
    entropy = shannon_entropy(secret)
    if mode == "gate":
        if rule in {"url_query_token", "credential_in_url", "dsn_with_password", "cookie_header"}:
            return entropy >= 2.0 and len(secret) >= 6
        if rule in {"assigned_secret_literal", "env_assignment_secret", "bearer_authorization"}:
            return entropy >= MIN_GATE_ASSIGN_ENTROPY and len(secret) >= 6
        return False
    contextual = bool(CONTEXT_KEYWORDS.search(line))
    if rule == "loose_secret_assignment":
        return contextual and entropy >= 3.0 and len(secret) >= 10
    if contextual:
        return entropy >= 3.0 and len(secret) >= 12
    return entropy >= 4.0 and len(secret) >= 20 and not is_hash_shaped(secret)


def scan_text(text: str, location: str, mode: str) -> Iterator[Finding]:
    if FILE_ALLOW_MARKER in text:
        return
    rules = list(PRECISE_RULES)
    if mode == "audit":
        rules.append(LOOSE_ASSIGNMENT_RULE)
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > 4000 or ALLOWED_TEST_MARKER in line:
            continue
        for rule, pattern in rules:
            for match in pattern.finditer(line):
                secret = match.group("secret")
                if secret is None or not evaluate(secret, line, rule, mode):
                    continue
                name_hint = match.groupdict().get("name")
                yield Finding(
                    rule=rule,
                    location=location,
                    line=lineno,
                    length=len(secret),
                    fingerprint=fingerprint(secret),
                    preview=f"{secret[:3]}***[len={len(secret)}]"
                    if len(secret) > 6
                    else f"***[len={len(secret)}]",
                    entropy=shannon_entropy(secret),
                    name_hint=name_hint,
                    secret=secret,
                )


def iter_candidate_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for name in filenames:
            path = Path(dirpath) / name
            lowered = name.lower()
            if lowered in NO_SUFFIX_OK or path.suffix.lower() in TEXT_SUFFIXES:
                try:
                    if path.is_file() and path.stat().st_size <= 4_000_000:
                        yield path
                except OSError:
                    continue


def scan_file(path: Path, root: Path, mode: str) -> list[Finding]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:8192]:
        return []
    try:
        location = str(path.relative_to(root))
    except ValueError:
        location = str(path)
    return list(scan_text(raw.decode("utf-8", "replace"), location, mode))


def scan_git_rev(repo: Path, rev: str, prefix: str | None, mode: str) -> list[Finding]:
    """Scan tracked blobs at a revision without touching any working tree."""
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", rev],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "replace")
    findings: list[Finding] = []
    for rel in listing.splitlines():
        if not rel or (prefix and not rel.startswith(prefix)):
            continue
        lowered = Path(rel).name.lower()
        if lowered not in NO_SUFFIX_OK and Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            continue
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{rev}:{rel}"],
            capture_output=True,
            check=False,
        )
        if blob.returncode != 0 or b"\x00" in blob.stdout[:8192] or len(blob.stdout) > 4_000_000:
            continue
        findings.extend(scan_text(blob.stdout.decode("utf-8", "replace"), f"{rev}:{rel}", mode))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="files or directories to scan")
    parser.add_argument(
        "--git-rev", nargs="+", metavar="ARG", help="REPO REV [path-prefix] to scan tracked blobs"
    )
    parser.add_argument("--mode", choices=("gate", "audit"), default="gate")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args(argv)

    if args.git_rev:
        if len(args.git_rev) < 2:
            parser.error("--git-rev needs REPO REV [path-prefix]")
        prefix = args.git_rev[2] if len(args.git_rev) > 2 else None
        findings = scan_git_rev(Path(args.git_rev[0]), args.git_rev[1], prefix, args.mode)
    elif args.paths:
        findings = []
        for target in args.paths:
            if target.is_file():
                findings.extend(scan_file(target, target.parent, args.mode))
            elif target.is_dir():
                findings.extend(
                    finding
                    for path in iter_candidate_files(target)
                    for finding in scan_file(path, target, args.mode)
                )
            else:
                parser.error(f"no such path: {target}")
    else:
        parser.error("provide paths or --git-rev")

    findings.sort(key=lambda f: (f.location, f.line, f.rule))
    for finding in findings:
        print(json.dumps(finding.as_json(), ensure_ascii=False) if args.json else _plain(finding))
    print(f"secret_scan mode={args.mode} findings={len(findings)}", file=sys.stderr)
    return EXIT_FINDINGS if (findings and args.fail_on_findings) else EXIT_CLEAN


def _plain(finding: Finding) -> str:
    hint = f"\tname={finding.name_hint}" if finding.name_hint else ""
    return (
        f"{finding.location}:{finding.line}\t{finding.rule}\tlen={finding.length}\t"
        f"h={round(finding.entropy, 2)}\tfp={finding.fingerprint}{hint}\t{finding.preview}"
    )


if __name__ == "__main__":
    sys.exit(main())
