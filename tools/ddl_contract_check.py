#!/usr/bin/env python3
"""G0-B — machine-check the §3.10 DDL against the invariants the spec itself states.

The spec froze B1-B7 as decisions, so the job here is not to re-litigate them but to
verify that the DDL in §3.10 actually expresses them, and to surface places where two
frozen clauses cannot both be implemented as written.

Every check reports PASS / FAIL / GAP with the exact SQL fragment it looked at, so a
reviewer can confirm the finding without re-reading the spec.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SPEC_VERSION = "v1.3"

ALL_TABLES_RE = re.compile(r"CREATE TABLE (\w+)")


@dataclass
class Check:
    check_id: str
    contract: str
    verdict: str
    detail: str
    evidence: str = ""

    def row(self) -> str:
        return f"[{self.verdict:5}] {self.check_id} ({self.contract}) — {self.detail}"


def strip_sql_comments(text: str) -> str:
    """Remove -- line comments before splitting on commas.

    The spec's DDL comments are Vietnamese and contain commas ("vận hành, KHÔNG chặn"),
    which otherwise read as column separators and swallow the following definition.
    """
    return "\n".join(line.split("--", 1)[0].rstrip() for line in text.splitlines())


def table_body(sql: str, table: str) -> str:
    clean = strip_sql_comments(sql)
    match = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", clean, re.DOTALL)
    return match.group(1) if match else ""


CONSTRAINT_KEYWORDS = frozenset({"UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "CONSTRAINT"})


def split_definitions(body: str) -> list[str]:
    """Split a CREATE TABLE body on top-level commas.

    Column definitions wrap across lines (version_state carries its CHECK on the next
    line), so a line-based parse silently loses constraints.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(_normalize(current))
            current = []
            continue
        current.append(char)
    tail = _normalize(current)
    if tail:
        parts.append(tail)
    return [part for part in parts if part]


def _normalize(chars: list[str]) -> str:
    return " ".join("".join(chars).split())


def columns(sql: str, table: str) -> dict[str, str]:
    """column name -> its complete definition, continuation lines included."""
    body = table_body(sql, table)
    out: dict[str, str] = {}
    for definition in split_definitions(body):
        if definition.startswith("--"):
            continue
        match = re.match(r"([a-z_][a-z0-9_]*)\b", definition)
        if not match:
            continue
        name = match.group(1)
        if name.upper() in CONSTRAINT_KEYWORDS:
            continue
        out[name] = definition
    return out


def check(sql: str) -> list[Check]:
    """Verify contract obligations.

    Each check is satisfied by *any* legitimate mechanism, not by one hard-coded column
    location. That matters: the G0-B proposal moves several obligations to different
    places (boundary kind onto a referenced rule table, orphan kind into a view,
    unresolved handling into a quarantine table), and a checker that only recognised the
    original spelling would report those fixes as regressions.
    """
    results: list[Check] = []
    obs = columns(sql, "observations")
    proofs = columns(sql, "coverage_proofs")
    intervals = columns(sql, "coverage_intervals")
    records = columns(sql, "source_records")
    versions = columns(sql, "source_record_versions")
    raw = columns(sql, "raw_objects")

    # ---- B1: two raw identifiers, both recorded, with the extraction locator
    has_both = "response_object_sha" in obs and "record_object_sha" in obs
    results.append(
        Check(
            "B1.1",
            "B1 hai định danh raw",
            "PASS" if has_both else "FAIL",
            "observations lưu cả response_object_sha và record_object_sha"
            if has_both
            else "observations thiếu một trong hai định danh",
            obs.get("response_object_sha", "") + " | " + obs.get("record_object_sha", ""),
        )
    )
    results.append(
        Check(
            "B1.2",
            "B1 record_locator",
            "PASS" if "record_locator" in obs and "NOT NULL" in obs["record_locator"] else "FAIL",
            "record_locator bắt buộc NOT NULL",
            obs.get("record_locator", "(absent)"),
        )
    )
    results.append(
        Check(
            "B1.3",
            "B1 object_kind",
            "PASS"
            if (
                "object_kind" in raw
                and "RESPONSE" in raw["object_kind"]
                and "RECORD" in raw["object_kind"]
            )
            or "raw_object_roles" in sql
            else "FAIL",
            "vai trò phân biệt bằng cột có CHECK"
            if "object_kind" in raw
            else "vai trò suy ra từ view raw_object_roles (cách G0-B chọn để tránh xung đột PRIMARY KEY)",
            raw.get("object_kind", "(no column; role derived from edges)"),
        )
    )

    # ---- B2: raw_objects row inside the commit transaction
    results.append(
        Check(
            "B2.1",
            "B2 FK thật để transaction có nghĩa",
            "PASS" if "REFERENCES raw_objects" in obs.get("record_object_sha", "") else "FAIL",
            "observations tham chiếu raw_objects bằng FK khai báo, không chỉ là hash tự do",
            obs.get("record_object_sha", ""),
        )
    )
    row_orphan_derivable = "orphan_object_rows" in sql or "orphan_object_files" in sql
    results.append(
        Check(
            "B2.2",
            "B2 phân biệt file mồ côi vs row mồ côi",
            "PASS" if row_orphan_derivable else "GAP",
            "row mồ côi suy ra được từ cạnh tham chiếu; file mồ côi thuộc bước quét filesystem "
            "(SQL không nhìn thấy file không có row, và không nên giả là thấy)"
            if row_orphan_derivable
            else "DDL không có chỗ nào phân biệt hai loại mồ côi ở §5.2",
        )
    )

    # ---- B3: operational vs blocking counters
    has_counters = "attempt_error_count" in proofs and "unrecovered_request_count" in proofs
    results.append(
        Check(
            "B3.1",
            "B3 hai bộ đếm tách bạch",
            "PASS" if has_counters else "FAIL",
            "coverage_proofs có attempt_error_count (vận hành) và unrecovered_request_count (chặn COMPLETE)",
            proofs.get("attempt_error_count", "")
            + " | "
            + proofs.get("unrecovered_request_count", ""),
        )
    )
    results.append(
        Check(
            "B3.2",
            "B3 lịch sử attempt giữ đầy đủ",
            "PASS"
            if "UNIQUE (request_id, attempt_no)" in table_body(sql, "fetch_attempts")
            else "FAIL",
            "fetch_attempts có khóa UNIQUE(request_id, attempt_no) nên retry là attempt mới, không ghi đè",
        )
    )

    # ---- B4: notifyVersion is a publication version, not a content version
    results.append(
        Check(
            "B4.1",
            "B4 notice group tồn tại",
            "PASS" if "notice_groups" in sql and "notice_group_members" in sql else "FAIL",
            "có notice_groups + notice_group_members để nhóm các bản công bố",
        )
    )
    all_column_names = {
        name for table in ALL_TABLES_RE.findall(sql) for name in columns(sql, table)
    }
    results.append(
        Check(
            "B4.2",
            "B4 tên trường tách bạch",
            "PASS"
            if "content_version_seq" in versions
            and not any("notifyversion" in name.lower() for name in all_column_names)
            else "FAIL",
            "không có CỘT nào tên notifyVersion; bộ đếm của ta là content_version_seq. "
            "So trên tên cột, không so trên toàn văn bản — comment mô tả thì buộc phải nhắc tên nguồn",
            versions.get("content_version_seq", ""),
        )
    )

    # ---- B5: adapter filter-boundary contract recorded in the proof
    # Satisfied either by inline columns (§3.10 spelling) or by a referenced rule table
    # (G0-B spelling). What must hold is that a proof names a boundary contract that was
    # actually verified — not where the two words live.
    inline_boundary = (
        "filter_boundary_kind" in proofs and "filter_boundary_verified_at_utc" in proofs
    )
    rule_table = columns(sql, "filter_boundary_rules")
    referenced_boundary = bool(rule_table) and "boundary_rule_id" in proofs
    has_boundary = inline_boundary or referenced_boundary
    results.append(
        Check(
            "B5.1",
            "B5 hợp đồng biên trong proof",
            "PASS" if has_boundary else "FAIL",
            "proof ghi hợp đồng biên inline"
            if inline_boundary
            else "proof trỏ boundary_rule_id tới bảng rule có kind + verified_at"
            if referenced_boundary
            else "proof không nêu hợp đồng biên nào",
            f"inline={inline_boundary} referenced={referenced_boundary}",
        )
    )
    results.append(
        Check(
            "B5.2",
            "B5 granularity ở cả hai bảng",
            "PASS" if "granularity" in proofs and "granularity" in intervals else "FAIL",
            "granularity có mặt trên coverage_intervals và coverage_proofs",
        )
    )
    conversion_ok = any("conversion" in name or "params" in name for name in rule_table) or any(
        "conversion" in name for name in proofs
    )
    results.append(
        Check(
            "B5.3",
            "B5 quy tắc quy đổi UTC -> tham số nguồn",
            "PASS" if conversion_ok else "GAP",
            "quy tắc quy đổi được lưu tái lập được (params + version + timezone + precision)"
            if conversion_ok
            else "không cột nào lưu quy tắc; source_query_fingerprint và adapter_sha là hash của thứ khác",
            ", ".join(sorted(rule_table)) or "(no rule table)",
        )
    )

    # ---- B6: deep capture refresh policy needs a schema surface
    deep_tables = [
        t for t in ("deep_captures", "deep_capture_history") if f"CREATE TABLE {t}" in sql
    ]
    deep_columns = [
        name
        for table in (*deep_tables, "documents", "lots", "goods_rows")
        for name in columns(sql, table)
        if "captured" in name or "refresh" in name
    ]
    results.append(
        Check(
            "B6.1",
            "B6 captured_at / refresh_due_at",
            "PASS" if deep_tables and deep_columns else "GAP",
            f"có {', '.join(deep_tables)} với {len(set(deep_columns))} cột captured/refresh; lịch sử tách latest"
            if deep_tables and deep_columns
            else "không bảng nào cho trạng thái deep-capture; done_ids không có chỗ trả captured_at + refresh_due_at",
            ", ".join(sorted(set(deep_columns))) or "(none)",
        )
    )

    # ---- B7: token transport is policy, but the schema must not become a leak
    tokenish = [
        f"{table}.{name}"
        for table in ("fetch_requests", "fetch_attempts", "observations", "coverage_proofs")
        for name in columns(sql, table)
        if re.search(r"token|secret|cookie|authorization|password", name, re.I)
    ]
    results.append(
        Check(
            "B7.1",
            "B7 schema không chứa credential",
            "PASS" if not tokenish else "FAIL",
            "không có cột nào lưu token/cookie/authorization trong các bảng mạng",
            ", ".join(tokenish),
        )
    )
    access_events = "CREATE TABLE source_access_events" in sql
    results.append(
        Check(
            "B7.2",
            "B7 nơi ghi lý do BLOCKED",
            "PASS" if access_events else "GAP",
            "có bảng sự kiện truy cập với reason_kind đóng và probe_uri cấm query string, nên lý do ghi được "
            "mà không mở chỗ cho URL mang token"
            if access_events
            else "không bảng nào ghi nguồn-lệnh-blocked kèm lý do đã scrub",
        )
    )

    # ---- §3.6 vocabulary must be enforced, otherwise BUG-03 can recur
    vocabulary = {
        "coverage_intervals": (
            "status",
            "interval_status",
            ("COMPLETE", "PARTIAL", "FAILED", "BLOCKED", "INTERRUPTED"),
        ),
        "fetch_attempts": (
            "outcome",
            "attempt_outcome",
            ("OK", "HTTP_ERROR", "TIMEOUT", "BLOCKED", "MALFORMED"),
        ),
        "observations": (
            "status",
            "observation_status",
            ("NEW", "UPDATED", "UPDATED_RAW", "UNCHANGED", "DUPLICATE_IN_RUN", "UNRESOLVED"),
        ),
    }
    for table, (column, vocab, _values) in vocabulary.items():
        definition = columns(sql, table).get(column, "")
        guarded = "CHECK" in definition.upper()
        detail = (
            f"cột {column} có CHECK chặn giá trị ngoài từ vựng"
            if guarded
            else f"cột {column} KHÔNG có CHECK — đây đúng là lỗ BUG-03 (đường code ghi COMPLETED vào interval_status)"
        )
        results.append(
            Check(
                f"VOCAB.{table}",
                f"§3.6 {vocab}",
                "PASS" if guarded else "FAIL",
                detail,
                definition or "(absent)",
            )
        )
    results.append(
        Check(
            "VOCAB.source_records",
            "§3.4 version_state",
            "PASS" if "CHECK" in records.get("version_state", "").upper() else "FAIL",
            "version_state có CHECK ba giá trị KNOWN/MISSING/UNPARSABLE",
            records.get("version_state", ""),
        )
    )

    # ---- §3.4 identity_key: NULL encoding and delimiter safety are undefined
    identity = records.get("identity_key", "")
    null_rule = re.search(r"identity_key[^\n]*", sql)
    canonical_identity = "GLOB 'id1:" in identity
    results.append(
        Check(
            "IDK.1",
            "§3.4 mã hoá version_raw NULL",
            "PASS" if canonical_identity else "GAP",
            "identity_key là canonical digest có version prefix: NULL đi vào encoding như một giá trị phân biệt, "
            "không bị render thành 'None' hay '00'"
            if canonical_identity
            else "identity_key vẫn là chuỗi nối nên NULL có hai cách render hợp lệ",
            identity or (null_rule.group(0) if null_rule else ""),
        )
    )
    results.append(
        Check(
            "IDK.2",
            "§3.4 escape ký tự phân cách",
            "PASS" if canonical_identity else "GAP",
            "không có delimiter nào để escape: thành phần được length-tag rồi hash"
            if canonical_identity
            else "natural_key là dữ liệu nguồn; ký tự phân cách có thể gây collision",
        )
    )

    # ---- §3.7 vs §3.4: notifyVersion encoded twice
    results.append(
        Check(
            "KEY.1",
            "§3.7 vs §3.4 notifyVersion",
            "PASS" if canonical_identity else "FAIL",
            "O-3 phương án A: natural_key = notifyNo đơn thuần, version chỉ nằm ở version_state/version_raw; "
            "identity_key là digest nên không còn hai bản sao của cùng một thông tin để mà lệch. "
            "Định nghĩa natural_key từng family nằm ở docs/DATA_CONTRACT.md, không suy ra được từ DDL"
            if canonical_identity
            else "notifyVersion vừa nằm trong natural_key vừa nằm ở version_raw — hai chỗ phải tự lệch nhau",
            records.get("natural_key", "") + " | " + identity,
        )
    )

    # ---- §3.2 vs observations NOT NULL: UNRESOLVED cannot be represented
    quarantine_page_table = "CREATE TABLE quarantined_responses" in sql
    results.append(
        Check(
            "UNRES.1",
            "§3.2 vs observations NOT NULL",
            "PASS"
            if quarantine_page_table and "NOT NULL" in obs.get("record_object_sha", "")
            else "FAIL",
            "O-4: record trích được nhưng thiếu identity -> có RECORD object + observation UNRESOLVED; "
            "response không parse được -> quarantined_responses mức page, không tạo observation. "
            "Nên record_object_sha NOT NULL là đúng, và CHECK response<>record cấm gán lẫn nhau"
            if quarantine_page_table
            else "observation_key cho phép UNRESOLVED nhưng record_object_sha NOT NULL mà không có bảng quarantine page-level",
            obs.get("observation_key", "") + " | " + obs.get("record_object_sha", ""),
        )
    )

    # ---- current_content_hash nullability vs versions.content_hash NOT NULL
    hash_not_null = "NOT NULL" in records.get("current_content_hash", "")
    results.append(
        Check(
            "HASH.1",
            "§3.5 nullability nhất quán",
            "PASS" if hash_not_null else "GAP",
            "current_content_hash NOT NULL: không tồn tại record có content version mà chưa có hash"
            if hash_not_null
            else "versions.content_hash NOT NULL nhưng records.current_content_hash để NULL",
            records.get("current_content_hash", "") + " | " + versions.get("content_hash", ""),
        )
    )

    # ---- forward reference ordering
    interval_pos = sql.find("CREATE TABLE coverage_intervals")
    proof_pos = sql.find("CREATE TABLE coverage_proofs")
    results.append(
        Check(
            "ORD.1",
            "§5.2 thứ tự ghi proof và interval",
            "GAP" if 0 <= interval_pos < proof_pos else "PASS",
            "coverage_intervals.proof_id tham chiếu coverage_proofs được khai báo SAU nó. SQLite cho phép "
            "forward reference, nhưng §4.1 mục 8 yêu cầu ghi proof cùng transaction với interval, nên migration "
            "phải insert proof trước rồi interval — thứ tự ngược với DDL, dễ gây lỗi khi viết",
            f"intervals@{interval_pos} proofs@{proof_pos}",
        )
    )

    results += closure_checks(sql, records, proofs, intervals, obs, raw)
    return results


def closure_checks(
    sql: str,
    records: dict[str, str],
    proofs: dict[str, str],
    intervals: dict[str, str],
    obs: dict[str, str],
    raw: dict[str, str],
) -> list[Check]:
    """Does this DDL actually close each G0-A finding? Run against both the §3.10 baseline
    and the G0-B proposal; the diff is the closure evidence."""
    out: list[Check] = []

    def verdict(check_id: str, contract: str, ok: bool, detail: str, evidence: str = "") -> None:
        out.append(Check(check_id, contract, "PASS" if ok else "FAIL", detail, evidence))

    verdict(
        "CL-C2",
        "C-2 CHECK đủ 3 từ vựng",
        all(
            "CHECK" in intervals.get("status", "")
            and "CHECK" in obs.get("status", "")
            and "CHECK" in columns(sql, "fetch_attempts").get("outcome", "")
            and "CHECK" in records.get("version_state", "")
            for _ in [0]
        ),
        "interval_status / observation_status / attempt_outcome / version_state đều bị CHECK",
    )
    rules = columns(sql, "filter_boundary_rules")
    verdict(
        "CL-C6",
        "C-6 boundary rule tái lập được",
        all(
            key in rules
            for key in (
                "rule_key",
                "rule_version",
                "source_timezone",
                "precision",
                "conversion_params",
                "offset_minutes",
            )
        )
        and "boundary_rule_id" in proofs,
        "bảng rule có id/version/timezone/precision/params và proof trỏ vào nó",
        ", ".join(sorted(rules)),
    )
    verdict(
        "CL-C7",
        "C-7 MISSING ⟺ version_raw NULL",
        "CHECK" in records.get("version_state", "")
        and "version_raw IS NULL" in table_body(sql, "source_records"),
        "schema buộc MISSING mang NULL và KNOWN/UNPARSABLE mang giá trị",
    )
    verdict(
        "CL-C8",
        "C-8 identity không nối delimiter",
        "GLOB 'id1:" in records.get("identity_key", ""),
        "identity_key là canonical digest có version prefix, không phải chuỗi nối",
        records.get("identity_key", ""),
    )
    verdict(
        "CL-C9",
        "C-9 current_content_hash NOT NULL",
        "NOT NULL" in records.get("current_content_hash", ""),
        "record tin cậy luôn có content hash",
        records.get("current_content_hash", ""),
    )
    verdict(
        "CL-C10",
        "C-10 proof insert trước interval",
        0 <= sql.find("CREATE TABLE coverage_proofs") < sql.find("CREATE TABLE coverage_intervals"),
        "thứ tự khai báo khớp với thứ tự ghi bắt buộc trong transaction",
    )
    money = columns(sql, "money_amounts")
    verdict(
        "CL-U2",
        "U-2 UNKNOWN ≠ 0",
        "presence_state" in money
        and "DEFAULT 0" not in money.get("value", "")
        and "NOT NULL" not in money.get("value", ""),
        "value để NULL được và không có DEFAULT 0; presence_state buộc KNOWN mới có value",
    )
    for table in ("page_receipts", "watermarks", "reconcile_gaps", "unresolved_records"):
        verdict(
            f"CL-U3.{table}",
            "U-3 bảng tối thiểu",
            f"CREATE TABLE {table}" in sql,
            f"{table} tồn tại với cột định nghĩa",
        )
    verdict(
        "CL-O4",
        "O-4 quarantine mức response",
        "CREATE TABLE quarantined_responses" in sql
        and "CREATE TABLE object_derivation" in sql
        and "trg_observation_derivation" in sql,
        "có bảng quarantine page-level, và object_derivation + trigger chứng minh record được trích "
        "thật từ response tại locator (thay cho CHECK so-hash đã bỏ vì trái D-0)",
    )
    verdict(
        "CL-O5",
        "O-5 lịch sử capture bất biến tách khỏi latest",
        all(f"CREATE TABLE {t}" in sql for t in ("deep_captures", "deep_capture_history"))
        and "CREATE VIEW deep_capture_latest" in sql,
        "refresh INSERT seq mới; latest là con trỏ, không phải bản ghi bị ghi đè",
    )
    verdict(
        "CL-SHA",
        "Xung đột raw SHA vs hai vai trò",
        "object_kind" not in raw or "raw_object_roles" in sql,
        "hoặc bỏ object_kind và suy vai trò từ cạnh, hoặc không còn PK xung đột",
        f"object_kind present: {'object_kind' in raw}; role view: {'raw_object_roles' in sql}",
    )
    # Compare against comment-stripped SQL: the proposal documents the removal of the
    # old CHECK in prose, and a naive substring test reads that as the constraint living on.
    executable_sql = strip_sql_comments(sql)
    verdict(
        "CL-ROLE",
        "Sửa #1: dẫn xuất thay CHECK so-hash",
        "CREATE TABLE object_derivation" in executable_sql
        and "trg_observation_derivation" in executable_sql
        and "response_object_sha <> record_object_sha" not in executable_sql,
        "record phải chứng minh trích được từ response tại locator; không còn CHECK phủ nhận hash trùng hợp pháp",
    )
    reds = columns(sql, "raw_revisions")
    verdict(
        "CL-REDACT",
        "Sửa #3: đổi redaction policy không sinh revision nghiệp vụ",
        "redaction_policy_version" in reds
        and "policy_driven_revisions" in sql
        and "business_raw_revisions" in sql,
        "revision phân biệt được nguồn đổi vs policy đổi; counter nghiệp vụ không bị phình",
    )
    verdict(
        "CL-IMMUT",
        "Sửa #4: rule/proof đã tham chiếu là bất biến",
        "trg_boundary_rule_immutable_after_use" in sql
        and "trg_boundary_rule_no_delete_after_use" in sql
        and "trg_proof_immutable" in sql
        and "trg_proof_no_delete" in sql,
        "proof lịch sử không bị rút thảm bằng cách sửa rule tại chỗ; muốn đổi thì thêm rule_version",
    )
    runs_cols = columns(sql, "runs")
    verdict(
        "CL-LIFECYCLE",
        "Sửa #5: lifecycle tách khỏi outcome",
        "lifecycle" in runs_cols
        and "status" in runs_cols
        and "NOT NULL" not in runs_cols.get("status", ""),
        "runs.lifecycle là trạng thái điều khiển (§2.3), runs.status là kết quả (§3.6) và NULL tới khi dừng",
        runs_cols.get("lifecycle", "") + " | " + runs_cols.get("status", ""),
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", type=Path, default=Path("docs/ddl/v1_3_reference.sql"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    # Path.write_text translated \n to \r\n on Windows, which breaks the table-body
    # regex; normalise before parsing.
    sql = args.sql.read_text(encoding="utf-8").replace("\r\n", "\n")
    results = check(sql)
    tally: dict[str, int] = {}
    for item in results:
        tally[item.verdict] = tally.get(item.verdict, 0) + 1

    if args.json:
        payload = {
            "spec_version": SPEC_VERSION,
            "sql": str(args.sql),
            "tally": tally,
            "checks": [asdict(r) for r in results],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        lines = [f"# DDL contract check — Master Spec {SPEC_VERSION}", ""]
        lines += [item.row() for item in results]
        lines += ["", f"tally: {tally}"]
        text = "\n".join(lines)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
