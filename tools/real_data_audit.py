#!/usr/bin/env python3
"""A3 — classify source-repo artifacts as REAL procurement data vs synthetic.

Reads a JSON payload and reports, per field, only the value *shape* (type, length,
character class, masked prefix for identifier-like fields) plus how many distinct
values look like live Vietnamese procurement records (notifyNo, MST, prices,
investor/contractor names).

Nothing here is designed to print a complete real record.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

NOTIFY_NO = re.compile(r"^(?:B20|B1[0-9]|B2[0-9]|[A-Z]{2}20)\d{6,}$")
MST = re.compile(r"^\d{10}$|^\d{13}$|^\d{10}-\d{1}$")
ID_LIKE = re.compile(r"^[0-9A-Za-z]{6,}$")
VIET_ORG_HINT = re.compile(
    r"\b(?:Công ty|Doanh nghiệp|Hợp tác xã|Ban Quản lý|Sở |UBND|UBND|Trung tâm|Cục )\b"
)

SENSITIVE_KEY_HINTS = (
    "notifyno",
    "notifyid",
    "investor",
    "investorname",
    "investorcode",
    "taxcode",
    "mst",
    "contractor",
    "contractorname",
    "bidprice",
    "winprice",
    "price",
    "amount",
    "planNo",
    "planno",
    "contractcode",
    "inputresultid",
    "bpid",
    "tenderno",
    "nameorigin",
    "namevt",
    "ben_truyen",
    "chairman",
    "author",
    "donwei",
)


def shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return f"int[{digits(value)}]"
    if isinstance(value, float):
        return f"float[{digits(int(value))}]"
    if isinstance(value, str):
        return f"str[len={len(value)}]"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"dict[{len(value)}]"
    return type(value).__name__


def digits(value: int) -> int:
    return len(str(abs(value)))


def classify(key: str, value: Any) -> list[str]:
    tags: list[str] = []
    kl = key.lower()
    if any(h in kl for h in SENSITIVE_KEY_HINTS):
        tags.append("sensitive-key")
    if isinstance(value, str):
        if NOTIFY_NO.match(value):
            tags.append("real-notifyNo-shape")
        if MST.match(value.replace("-", "")):
            tags.append("real-MST-shape")
        if VIET_ORG_HINT.search(value):
            tags.append("real-VN-org-name")
    money_key = kl.endswith("price") or kl in {"bidprice", "winprice", "amount"}
    if money_key and isinstance(value, (int, float)) and value and value > 1_000_000:
        tags.append("real-money-magnitude")
    return tags


def walk(node: Any, path: str, out: dict[str, dict[str, Any]], limit: int = 200_000) -> None:
    if len(out) > limit:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else key
            record(out, child, key, value)
            walk(value, child, out, limit)
    elif isinstance(node, list):
        for item in node[:200]:
            walk(item, f"{path}[]", out, limit)


def record(out: dict[str, dict[str, Any]], path: str, key: str, value: Any) -> None:
    entry = out.setdefault(path, {"shape": shape(value), "count": 0, "tags": set(), "masked": None})
    entry["count"] += 1
    tags = classify(key, value)
    entry["tags"].update(tags)
    if entry["masked"] is None and tags:
        entry["masked"] = mask(value)


def mask(value: Any) -> str:
    if isinstance(value, str):
        return f"{value[:3]}***[{len(value)}]"
    if isinstance(value, (int, float)):
        return f"***[{len(str(value))} digits]"
    return shape(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--paths", nargs="+", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    report: dict[str, Any] = {}
    for rel in args.paths:
        result = subprocess.run(
            ["git", "-C", str(args.repo), "show", f"{args.sha}:{rel}"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            report[rel] = {"error": "absent at sha"}
            continue
        raw = result.stdout
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            text = raw.decode("utf-8", "replace")
            report[rel] = {
                "kind": "non-json",
                "bytes": len(raw),
                "vn_org_matches": len(VIET_ORG_HINT.findall(text)),
                "notify_no_matches": len(NOTIFY_NO.findall(text)),
                "mst_matches": len(MST.findall(text)),
            }
            continue
        fields: dict[str, dict[str, Any]] = {}
        walk(data, "", fields)
        flagged = {
            path: {
                "shape": f["shape"],
                "count": f["count"],
                "tags": sorted(f["tags"]),
                "masked": f["masked"],
            }
            for path, f in fields.items()
            if f["tags"]
        }
        report[rel] = {
            "kind": "json",
            "bytes": len(raw),
            "distinct_field_paths": len(fields),
            "flagged_field_paths": len(flagged),
            "top_tags": sorted({tag for f in fields.values() for tag in f["tags"]}),
            "flagged_sample": dict(list(flagged.items())[:40]),
        }

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
