"""Synthetic v5 archive matching the pinned harvester's public storage layout."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path


def make_legacy(root: Path) -> Path:
    root.mkdir(parents=True)
    raw = root / "raw" / "old-run" / "egp_home.jsonl.gz"
    raw.parent.mkdir(parents=True)
    states = [("A", 1), ("B", 1), ("B", 2), ("A", 1)]
    conn = sqlite3.connect(root / "warehouse.sqlite")
    conn.executescript("""
      CREATE TABLE schema_version(version INTEGER); INSERT INTO schema_version VALUES(5);
      CREATE TABLE records(source TEXT,source_record_id TEXT,record_type TEXT,current_hash TEXT,current_version INTEGER,
        title TEXT,source_url TEXT,first_seen_at TEXT,last_seen_at TEXT,first_run_id TEXT,last_run_id TEXT,
        latest_raw_file TEXT,latest_raw_line INTEGER,raw_hash TEXT);
      CREATE TABLE record_versions(id INTEGER PRIMARY KEY,source TEXT,source_record_id TEXT,version_no INTEGER,
        content_hash TEXT,raw_file TEXT,raw_line INTEGER,raw_hash TEXT,fetched_at TEXT,run_id TEXT,change_kind TEXT);
      CREATE TABLE observations(id INTEGER PRIMARY KEY,run_id TEXT,source TEXT,source_record_id TEXT,record_type TEXT,
        status TEXT,identity_reliable INTEGER,content_hash TEXT,title TEXT,source_url TEXT,source_published_at TEXT,
        fetched_at TEXT,raw_file TEXT,raw_line INTEGER,raw_hash TEXT);
      CREATE TABLE coverage_windows(id INTEGER PRIMARY KEY,run_id TEXT,source TEXT,record_type TEXT,
        window_from TEXT,window_to TEXT,status TEXT,proof_version INTEGER);
      CREATE TABLE runs(run_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,end_status TEXT,config_json TEXT);
    """)
    with gzip.open(raw, "wt", encoding="utf-8") as handle:
        for n, (title, ignored) in enumerate(states, 1):
            stamp = f"2026-10-{n:02d}T03:00:00Z"
            payload = {"notifyNo": "LEGACY-001", "notifyVersion": "00", "bidName": title, "ignored": ignored}
            normalized = {"title": title}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            chash = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            handle.write(json.dumps({"fetched_at": stamp, "normalized": normalized, "raw_payload": payload}) + "\n")
            conn.execute("INSERT INTO record_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (n,"egp_home","LEGACY-001:00",n,chash,raw.relative_to(root).as_posix(),n,digest,stamp,"old-run","NEW" if n==1 else "UPDATED"))
            conn.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (n,"old-run","egp_home","LEGACY-001:00","TBMT","NEW" if n==1 else "UPDATED",1,chash,title,None,None,stamp,raw.relative_to(root).as_posix(),n,digest))
    conn.execute("INSERT INTO observations SELECT 5,run_id,source,source_record_id,record_type,'UNCHANGED',identity_reliable,content_hash,title,source_url,source_published_at,'2026-10-05T03:00:00Z',raw_file,raw_line,raw_hash FROM observations WHERE id=4")
    conn.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("egp_home","LEGACY-001:00","TBMT",chash,4,"A",None,"2026-10-01T03:00:00Z","2026-10-05T03:00:00Z","old-run","old-run",raw.relative_to(root).as_posix(),4,digest))
    conn.execute("INSERT INTO coverage_windows VALUES(1,'old-run','egp_home','TBMT','2026-10-01','2026-10-06','COMPLETE',1)")
    conn.execute("INSERT INTO runs VALUES('old-run','2026-10-01T03:00:00Z','2026-10-05T03:00:00Z','COMPLETED','{}')")
    conn.commit()
    conn.close()
    return root
