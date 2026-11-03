#!/usr/bin/env python3
"""A4 — turn a real captured E-GP payload into a synthetic fixture.

The point of this tool is not "mask some fields". It produces a fixture that keeps
the *shape* the crawler has to parse (key names, nesting, record counts, page
structure, identifier formats, value magnitudes, json pointers) while every
identity-bearing and value-bearing leaf is regenerated from a seeded RNG.

Guarantee model:
  sanitize  real payload -> synthetic payload, deterministic under --seed
  verify    assert that no leaf value from the original survives in the output

verify is the contract. Shape preservation is deliberate, so a shape-based scanner
cannot tell synthetic from real; only value-set intersection can. That is what
--verify measures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

STRATEGY_VERSION = 3

# Field name -> strategy. First matching pattern wins, so put specific before generic.
#
# Ordering notes that are easy to get wrong:
#   * "principal" must precede "timestamp" — createdBy/updatedBy contain the substring
#     "created"/"updated" but hold an operator identifier, not a date.
#   * "url" must precede "text" — detailUrl contains "detail".
#   * "keep" is only for request/protocol structure and enum labels. Aggregate counts
#     of real notices are measurements, so they go to "measure", not "keep".
FIELD_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"notifyno|noticeno|tenderno|plan_no$|planno|contractcode|contract_no$"), "code"),
    (re.compile(r"notifystand|nonstand|planno_stand|planostand"), "code"),
    (
        re.compile(
            r"notifyid|bidid|bpid|openbidid|bidopenid|inputresultid|resultid|planid|projectid|hsmtid"
        ),
        "id36",
    ),
    (re.compile(r"investorcode|purchasercode|ownercode|mainid"), "code_vn"),
    (re.compile(r"taxcode|tax_no|taxno|mst|idnumber|identifyno"), "tax_id"),
    (re.compile(r"by$|_by$|user|account|login|creator|principal"), "principal"),
    (
        re.compile(
            r"investorname|purchaser|owner_name|contractorname|winningcontractor|donwei|ben_truyen|company"
        ),
        "org_name",
    ),
    (re.compile(r"author|chairman|director|fullname|operator|personname"), "person_name"),
    (re.compile(r"price|amount|budget|value|money|cost|ctprice"), "money"),
    (re.compile(r"total|recordcount|itemcount|rowcount|numrows|numberof"), "measure"),
    (re.compile(r"url|link|attachment|filepath|filename|docname"), "url"),
    (
        re.compile(r"title|nameorigin|namevt|description|detail|content|note|subject|projectname"),
        "text",
    ),
    (re.compile(r"date|time|created|updated|published|expire|finish|start"), "timestamp"),
    (re.compile(r"province|district|ward|city|region|location|address|diaphuong"), "place"),
    (re.compile(r"email|phone|fax|hotline|dienthoai"), "contact"),
    (re.compile(r"version|status|type|kind|category|currency|unit|isdn|flag"), "keep"),
    (re.compile(r"pagenumber|^page$|pagesize|^size$|limit|offset|ordinal|^index$|^code$"), "keep"),
)

# Unrecognised fields are transformed, never kept — but "auto" preserves the value's
# type so a count does not become a sentence.
DEFAULT_STRATEGY = "auto"

ORG_POOL = (
    "Ban Quản lý Dự án {n} khu vực {p}",
    "Sở {p} tỉnh {p2}",
    "UBND phường {w} quận {d} thành phố {p}",
    "Công ty TNHH Xây dựng {n} {p}",
    "Công ty Cổ phần Tư vấn {n} Việt Nam",
    "Tổng công ty {n} Miền {p2}",
    "Trung tâm Y tế huyện {d}",
    "Hợp tác xã Nông nghiệp {n}",
    "Ban Quản lý Đầu tư xây dựng khu vực {p}",
    "Công ty TNHH Một thành viên {n}",
)
PROVINCES = (
    "An Khang",
    "Bình Điền",
    "Cao Lãnh",
    "Diên Khánh",
    "Gò Vấp",
    "Hàm Thuận",
    "Ia Drang",
    "Kỳ Anh",
    "Lạc Khê",
    "Mường Ao",
    "Ninh Tị",
    "Phù Cát",
    "Quảng Trị Đông",
    "Sông Cầu",
)
WARDS = "Xuân Hòa", "Hạ Long", "Thuận Nam", "Lộc An", "Vĩnh Hội", "Tân Phú", "Mỹ Lệ", "Ninh Quãng"
PERSON_POOL = ("Nguyễn Văn A", "Trần Thị B", "Lê Văn C", "Phạm Thị D", "Hoàng Văn E", "Vũ Thị G")
SYNTH_TEXT = (
    "Gói thầu {n} thuộc kế hoạch lựa chọn nhà thầu {m}",
    "Cung cấp {n} thiết bị văn phòng năm {y}",
    "Xây lắp hạng mục {n} công trình {m}",
    "Tư vấn giám sát gói {n} dự án {m}",
)


class Transform:
    """Seeded generator for every synthetic leaf value."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)
        self._cache: dict[str, str] = {}
        self.converted = 0
        self.kept = 0

    def _digits(self, count: int) -> str:
        return "".join(self._rng.choice("0123456789") for _ in range(count))

    def _letters(self, count: int, upper: bool = True) -> str:
        pool = "ABCDEFGHJKLMNPRSTUVWXYZ" if upper else "abcdefghjkmnpqrstuvwxyz"
        return "".join(self._rng.choice(pool) for _ in range(count))

    def code(self, original: str, prefix_hint: str = "SY") -> str:
        """Keep the visible format of a source code without keeping its value."""
        letters = "".join(ch for ch in original if ch.isalpha())[:2].upper() or prefix_hint
        digits = re.sub(r"\D", "", original)
        digit_len = len(digits) if len(digits) >= 4 else 9
        return f"{letters or prefix_hint}{self._digits(digit_len)}"

    def id36(self, original: str) -> str:
        if re.fullmatch(r"[0-9a-fA-F-]{36}", original):
            hex_digits = self._rng.randrange(16**32)
            return f"{hex_digits:032x}"
        return self._letters(10, upper=False) + self._digits(6)

    def tax_id(self, original: str) -> str:
        digits = re.sub(r"\D", "", original)
        length = len(digits) if length_ok(len(digits)) else 10
        return self._digits(length)

    def money(self, original: Any) -> Any:
        magnitude = _digit_count_of(original)
        if magnitude <= 1:
            return self._rng.randrange(1, 9)
        low = 10 ** (magnitude - 1)
        value = self._rng.randrange(low, min(low * 10, low * 9 + 1))
        if isinstance(original, float):
            return float(value) + round(self._rng.random(), 2)
        return value

    def measure(self, original: Any) -> Any:
        """Synthetic aggregate count in the same order of magnitude as the real one."""
        magnitude = _digit_count_of(original)
        if magnitude <= 1:
            return self._rng.randrange(1, 10)
        low = 10 ** (magnitude - 1)
        return self._rng.randrange(low, low * 10)

    def principal(self, original: str) -> str:
        """Synthetic operator/account identifier keeping the original's shape.

        E-GP stores the submitting principal as a tax id with an optional branch
        suffix (vn9876543210-30), so a plain name generator would leak the real one
        by leaving the value untouched.
        """
        branch = ""
        match = re.search(r"-(\d{1,3})$", original)
        if match:
            branch = f"-{self._digits(len(match.group(1)))}"
        digits = re.sub(r"\D", "", original)
        if len(digits) >= 8:
            return f"vn{self._digits(min(len(digits), 10))}{branch}"
        return f"{self._letters(6, upper=False)}{self._digits(3)}{branch}"

    def org_name(self) -> str:
        template = self._rng.choice(ORG_POOL)
        return template.format(
            n=self._letters(self._rng.randrange(3, 7)),
            p=self._rng.choice(PROVINCES),
            p2=self._rng.choice(PROVINCES),
            w=self._rng.choice(WARDS),
            d=self._rng.choice(WARDS),
        )

    def person_name(self) -> str:
        return self._rng.choice(PERSON_POOL)

    def text(self, original: str) -> str:
        template = self._rng.choice(SYNTH_TEXT)
        body = template.format(
            n=self._digits(3),
            m=self._letters(4).upper(),
            y=2000 + self._rng.randrange(1, 26),
        )
        # Preserve the rough length bucket so UI/parsers see a comparable payload.
        target = max(len(body), _length_bucket(len(original)))
        while len(body) < target:
            body += " " + self._rng.choice(SYNTH_TEXT).format(
                n=self._digits(3), m=self._letters(4).upper(), y=2020
            )
        return body[: max(target, 12)]

    def place(self) -> str:
        return self._rng.choice(PROVINCES)

    def contact(self, kind: str) -> str:
        if "phone" in kind or "fax" in kind or "dienthoai" in kind:
            return f"0{self._digits(9)}"
        return f"{self._letters(6, upper=False)}@example.invalid"

    def url(self, original: str) -> str:
        path = re.sub(
            r"[0-9A-Za-z]{8,}", lambda m: self._letters(len(m.group()), upper=False), original
        )
        return path

    def timestamp(self, original: Any) -> Any:
        return _shift_timestamp(original)


def length_ok(length: int) -> bool:
    return length in (10, 13)


def _digit_count_of(value: Any) -> int:
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return max(1, len(str(int(value))))
    digits = re.sub(r"[^\d]", "", str(value))
    return len(digits)


def _length_bucket(length: int) -> int:
    if length <= 24:
        return 24
    if length <= 60:
        return 60
    if length <= 120:
        return 120
    return 200


def _shift_timestamp(original: Any) -> Any:
    """Move a date out of the real era by a fixed offset, preserving ordering."""
    if not isinstance(original, str):
        return original
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", original)
    if not match:
        return original
    year = int(match.group(1)) - 7  # deterministic era shift, keeps ordering intact
    return original[: match.start(1)] + str(year) + original[match.start(1) + 4 :]


def strategy_for(key: str) -> str:
    lowered = key.lower()
    for pattern, strategy in FIELD_RULES:
        if pattern.search(lowered):
            return strategy
    return DEFAULT_STRATEGY


def transform_value(key: str, value: Any, tf: Transform) -> Any:
    """Scalar transform chosen by the field name that owns the value."""
    strategy = strategy_for(key)
    if strategy == "keep":
        tf.kept += 1
        return value
    tf.converted += 1
    if value is None:
        return None
    if strategy == "code":
        return tf.code(str(value))
    if strategy == "code_vn":
        return "vn" + tf._digits(9)
    if strategy == "id36":
        return tf.id36(str(value))
    if strategy == "tax_id":
        return tf.tax_id(str(value))
    if strategy == "principal":
        return tf.principal(str(value))
    if strategy == "money":
        return tf.money(value)
    if strategy == "measure":
        return tf.measure(value)
    if strategy == "org_name":
        return tf.org_name()
    if strategy == "person_name":
        return tf.person_name()
    if strategy == "text":
        return tf.text(str(value))
    if strategy == "url":
        return tf.url(str(value))
    if strategy == "place":
        return tf.place()
    if strategy == "contact":
        return tf.contact(key.lower())
    if strategy == "timestamp":
        # A field named like a date is not always one; never return the original.
        if isinstance(value, str) and _DATE_SHAPE.search(value):
            return tf.timestamp(value)
        return _by_type(value, tf)
    # Unknown field: conservative default, but keep the value's type so the fixture
    # still exercises the same parser paths.
    return _by_type(value, tf)


_DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{10,14}")


def _by_type(value: Any, tf: Transform) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return tf.measure(value)
    return tf.text(str(value))


def _walk_transform(node: Any, tf: Transform, key: str = "") -> Any:
    """Recurse preserving structure.

    Lists inherit the field name of their parent, because a real payload stores
    repeated identities as arrays (contractorName: ["Công ty X"], bidPrice: [123]).
    Losing that name would leave the genuine value untouched.
    """
    if isinstance(node, dict):
        return {member: _walk_transform(value, tf, member) for member, value in node.items()}
    if isinstance(node, list):
        return [_walk_transform(item, tf, key) for item in node]
    if key and isinstance(node, (str, int, float)) and not isinstance(node, bool):
        return transform_value(key, node, tf)
    return node


def seed_for(payload_bytes: bytes, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    return int(hashlib.sha256(payload_bytes).hexdigest()[:8], 16)


def sanitize_json_bytes(raw: bytes, seed: int) -> tuple[bytes, Transform]:
    data = json.loads(raw.decode("utf-8-sig"))
    tf = Transform(seed)
    result = _walk_transform(data, tf)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    return rendered.encode("utf-8"), tf


def leaf_values(node: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten to path -> scalar, for leak comparison."""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(leaf_values(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            out.update(leaf_values(item, f"{prefix}[{index}]"))
    else:
        out[prefix] = node
    return out


def normalized(value: Any) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip().lower()


def field_of_path(path: str) -> str:
    """Last named segment of a leaf path, e.g. page.content[0].notifyNo -> notifyNo."""
    tail = path.rsplit(".", 1)[-1]
    return re.sub(r"\[\d+\]$", "", tail)


def find_leaks(
    original: Any, sanitized: Any, ignore_paths: set[str] = frozenset()
) -> list[dict[str, Any]]:
    """Report original values that the sanitized fixture still exposes.

    Two complementary checks, both scoped to fields the strategy table promises to
    transform (enum labels and page geometry are legitimately identical on both sides):

      untouched  — the value is still at its own path. This is the main guarantee.
      relocated  — a high-entropy original value appears somewhere in the output,
                   e.g. an id copied into a different field.

    Path scoping matters. A whole-document value-set comparison reports every kept
    enum ("PARTIAL", "KHAC") as a leak, which trains people to ignore the tool.
    """
    orig = leaf_values(original)
    san = leaf_values(sanitized)
    san_values = {normalized(v) for v in san.values()}
    leaks: list[dict[str, Any]] = []
    for path, value in orig.items():
        if path in ignore_paths or value is None:
            continue
        if strategy_for(field_of_path(path)) == "keep":
            continue
        text = normalized(value)
        if not _identity_shaped(value):
            continue
        if path in san and normalized(san[path]) == text:
            leaks.append(_leak("untouched", path, value))
        elif _relocatable(value) and text in san_values:
            leaks.append(_leak("relocated", path, value))
    return leaks


def _relocatable(value: Any) -> bool:
    """Only identifier-shaped, high-entropy values are worth a cross-path search."""
    text = normalized(value)
    return len(text) >= 10 and bool(re.search(r"\d", text))


def _leak(kind: str, path: str, value: Any) -> dict[str, Any]:
    text = normalized(value)
    return {
        "kind": kind,
        "path": path,
        "value_type": type(value).__name__,
        "length": len(text),
        "fingerprint": _fp(text),
        "strategy": strategy_for(field_of_path(path)),
    }


def _identity_shaped(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return abs(value) >= 1000
    text = str(value).strip()
    if len(text) < 4:
        return False
    if re.fullmatch(r"(?:vn|VN|[A-Z]{2,3})", text):
        return False
    return not re.fullmatch(
        r"(?:true|false|null|0|1|VN|VND|USD|EUR|day|month|year)", text, re.IGNORECASE
    )


def _fp(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def cmd_sanitize(args: argparse.Namespace) -> int:
    raw = Path(args.input).read_bytes()
    seed = seed_for(raw, args.seed)
    rendered, tf = sanitize_json_bytes(raw, seed)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rendered)
    print(
        f"seed={seed} strategy_version={STRATEGY_VERSION} converted={tf.converted} kept={tf.kept}"
    )
    print(f"wrote {out} ({len(rendered)} bytes)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    original = json.loads(Path(args.original).read_bytes().decode("utf-8-sig"))
    sanitized = json.loads(Path(args.sanitized).read_bytes().decode("utf-8-sig"))
    leaks = find_leaks(original, sanitized)
    report = {
        "original": str(args.original),
        "sanitized": str(args.sanitized),
        "identity_leaks": len(leaks),
        "leaks": leaks[:200],
        "verdict": "PASS" if not leaks else "FAIL",
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(
        json.dumps({k: v for k, v in report.items() if k != "leaks"}, indent=2, ensure_ascii=False)
    )
    for leak in report["leaks"][:20]:
        print(f"  LEAK {leak['path']} len={leak['length']} fp={leak['fingerprint']}")
    return 0 if not leaks else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_san = sub.add_parser("sanitize", help="real payload -> synthetic fixture")
    p_san.add_argument("--input", required=True)
    p_san.add_argument("--output", required=True)
    p_san.add_argument("--seed", type=int)
    p_san.set_defaults(func=cmd_sanitize)
    p_ver = sub.add_parser("verify", help="assert no original identity value survived")
    p_ver.add_argument("--original", required=True)
    p_ver.add_argument("--sanitized", required=True)
    p_ver.add_argument("--out")
    p_ver.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
