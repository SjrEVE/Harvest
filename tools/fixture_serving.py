"""Build a 1M-record serving snapshot for performance work (fixture only, never production).

The 1M scale run through `commit_page` takes ~2h; UI performance depends only on the serving
snapshot, so this tool materialises an equivalent read model directly. It is clearly a fixture:
no raw objects, no coverage proofs, synthetic titles — enough to profile SQL and FTS at scale.

Run: uv run python tools/fixture_serving.py --records 1000000 --out _evidence/rc/perf-1m
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from santhau_studio.collectors.source_registry import FAMILY_ORDER, SOURCE_EGP_HOME
from santhau_studio.domain.text import fold_vietnamese
from santhau_studio.storage.serving import FTS_DDL
from santhau_studio.storage.warehouse import connect

BATCH = 5_000

TITLES = (
    "Gói thầu xây lắp trường học {n}", "Mua sắm thiết bị y tế {n}", "Tư vấn giám sát công trình {n}",
    "Sửa chữa đường giao thông {n}", "Cung cấp hàng hóa văn phòng {n}",
)
INVESTORS = ("Ban QLDA {n}", "Sở Y tế {n}", "Sở Giao thông {n}", "UBND huyện {n}")


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def build(out: Path, records: int) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    warehouse = out / "warehouse.sqlite"
    conn = connect(warehouse, create_schema=True)
    conn.close()
    (out / "serving.sqlite").unlink(missing_ok=True)
    warehouse.replace(out / "serving.sqlite")
    for side in ("-wal", "-shm"):
        (out / f"warehouse.sqlite{side}").unlink(missing_ok=True)

    conn = connect(out / "serving.sqlite")
    conn.execute("PRAGMA synchronous=OFF")
    started = time.perf_counter()
    try:
        conn.execute("BEGIN")
        object_sha = _sha("fixture-object")
        conn.execute(
            "INSERT INTO raw_objects (sha256, byte_len, codec, storage_rel_path, integrity_status,"
            " redaction_policy_version, created_at_utc) VALUES (?,?,?,?,?,?,?)",
            (object_sha, 1, "gzip", "fixture", "OK", 1, "2026-11-03T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO source_registry (source_key, display_name, endpoint_family, access_state)"
            " VALUES (?,?,?,?)",
            (SOURCE_EGP_HOME, "e-GP (fixture)", "egp", "ALLOWED_AND_VERIFIED"),
        )
        written = 0
        while written < records:
            take = min(BATCH, records - written)
            rec_rows = []
            ver_rows = []
            fts_rows = []
            for i in range(written, written + take):
                family = FAMILY_ORDER[i % len(FAMILY_ORDER)]
                natural = f"IB-{i:08d}"
                identity = "id1:" + _sha(f"{family}|{natural}|00")
                title = TITLES[i % len(TITLES)].format(n=i)
                investor = INVESTORS[i % len(INVESTORS)].format(n=i % 900)
                normalized = json.dumps(
                    {"title": title, "investorName": investor, "notifyNo": natural,
                     "notifyVersion": "00"}, ensure_ascii=False
                )
                content_hash = "ch1:" + _sha(f"c|{i}")
                rec_rows.append((
                    i + 1, SOURCE_EGP_HOME, family, natural, "KNOWN", "00", identity,
                    "2026-11-01T00:00:00Z", "2026-11-03T00:00:00Z", 1, content_hash,
                    object_sha, "OK", 1,
                ))
                ver_rows.append((
                    i + 1, i + 1, 1, content_hash, normalized, object_sha, None,
                    "2026-11-03T00:00:00Z", "NEW", 1,
                ))
                fts_rows.append((i + 1, fold_vietnamese(family), fold_vietnamese(natural),
                                 fold_vietnamese(title), fold_vietnamese(investor),
                                 fold_vietnamese(normalized)))
            conn.executemany(
                "INSERT INTO source_records (id, source_key, family, natural_key, version_state,"
                " version_raw, identity_key, first_seen_at_utc, last_seen_at_utc,"
                " current_content_version, current_content_hash, current_record_object_sha,"
                " integrity_status, redaction_policy_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rec_rows,
            )
            conn.executemany(
                "INSERT INTO source_record_versions (id, source_record_id, content_version_seq,"
                " content_hash, normalized_json, introducing_record_sha, introducing_response_sha,"
                " fetched_at_utc, change_kind, normalize_schema) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ver_rows,
            )
            written += take
        conn.execute(FTS_DDL)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS serving_family_stats ("
            " family TEXT PRIMARY KEY, records INTEGER NOT NULL, versions INTEGER NOT NULL,"
            " raw_revisions INTEGER NOT NULL, observations INTEGER NOT NULL,"
            " last_verified_at_utc TEXT)"
        )
        conn.execute(
            "INSERT INTO serving_family_stats (family, records, versions, raw_revisions,"
            " observations, last_verified_at_utc)"
            " SELECT family, COUNT(*), COUNT(*), 0, 0, NULL FROM source_records GROUP BY family"
        )
        conn.execute(
            "INSERT INTO serving_meta (snapshot_built_at_utc, source_warehouse_sha, row_counts_json,"
            " stale, stale_reason) VALUES (?,?,?,0,NULL)",
            ("2026-11-03T00:00:00Z", "fixture", json.dumps({"source_records": records})),
        )
        cursor = conn.execute(
            "SELECT sr.id, sr.family, sr.natural_key, v.normalized_json"
            " FROM source_records sr JOIN source_record_versions v ON v.source_record_id = sr.id"
            " ORDER BY sr.id"
        )
        inserted = 0
        while True:
            rows = cursor.fetchmany(BATCH)
            if not rows:
                break
            conn.executemany(
                "INSERT INTO search_index (record_id, family, natural_key, title, investor_name, body)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (rid, fold_vietnamese(family), fold_vietnamese(natural),
                     fold_vietnamese(json.loads(norm).get("title", "")),
                     fold_vietnamese(json.loads(norm).get("investorName", "")),
                     fold_vietnamese(norm))
                    for rid, family, natural, norm in rows
                ],
            )
            inserted += len(rows)
        conn.commit()
    finally:
        conn.close()
    elapsed = time.perf_counter() - started
    size = (out / "serving.sqlite").stat().st_size
    return {"records": records, "build_seconds": round(elapsed, 1),
            "serving_bytes": size, "fts_rows": inserted}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=1_000_000)
    parser.add_argument("--out", type=Path, default=Path("_evidence/rc/perf-1m"))
    args = parser.parse_args(argv)
    report = build(args.out, args.records)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
