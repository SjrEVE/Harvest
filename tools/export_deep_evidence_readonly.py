"""Read-only, standard-library audit export to stdout; no source HTTP or server files written."""

import argparse
import base64
import gzip
import hashlib
import json
import sqlite3
import struct
import zlib
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

HEADER = struct.Struct("<4s32sBIQI")
MAX_BYTES = 64 * 1024 * 1024
VN = timezone(timedelta(hours=7))


def read_raw(root, conn, sha):
    row = conn.execute("SELECT storage_rel_path FROM raw_objects WHERE sha256=?", (sha,)).fetchone()
    if row is None:
        raise ValueError("RAW_OBJECT_NOT_REGISTERED")
    relative = row[0].replace("\\", "/")
    name, separator, offset = relative.partition("#")
    path = (root / name).resolve()
    path.relative_to(root.resolve())
    if not name.startswith("raw/"):
        raise ValueError("RAW_PATH_OUT_OF_SCOPE")
    if separator:
        with path.open("rb") as stream:
            stream.seek(int(offset))
            header = stream.read(HEADER.size)
            magic, stored_sha, codec, crc, raw_len, blob_len = HEADER.unpack(header)
            if magic != b"SGS1" or stored_sha.hex() != sha or codec != 1 or max(raw_len, blob_len) > MAX_BYTES:
                raise ValueError("RAW_FRAME_INVALID")
            blob = stream.read(blob_len)
        if len(blob) != blob_len or zlib.crc32(blob) != crc:
            raise ValueError("RAW_FRAME_CHECKSUM_INVALID")
        raw = gzip.decompress(blob)
        if len(raw) != raw_len:
            raise ValueError("RAW_LENGTH_INVALID")
    else:
        with gzip.open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("RAW_HASH_INVALID")
    return raw


def publication_day(listing):
    value = listing.get("publicDate")
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (stamp.replace(tzinfo=VN) if stamp.tzinfo is None else stamp.astimezone(VN)).date()
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--from", dest="first", required=True, type=date.fromisoformat)
    parser.add_argument("--to", dest="last", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    root = args.data_root.resolve()
    if args.first > args.last:
        raise ValueError("Invalid scope")
    conn = sqlite3.connect((root / "warehouse.sqlite").as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    try:
        records = conn.execute("SELECT r.*,t.response_object_sha,t.error,t.lane,t.state,t.updated_at_utc "
            "FROM deep_tasks t JOIN source_records r ON r.id=t.source_record_id "
            "WHERE r.source_key='egp_home' AND r.family='TBMT' AND t.lane='HSMT' AND t.state='PARSE_ERROR' "
            "AND t.response_object_sha IS NOT NULL ORDER BY r.id").fetchall()
        print(json.dumps({"type": "snapshot", "root": str(root), "from": str(args.first), "to": str(args.last),
                          "snapshot_at_utc": datetime.now(UTC).isoformat(), "candidate_tasks": len(records), "read_only": True}), flush=True)
        exported = outside = unknown = 0
        for row in records:
            listing = json.loads(read_raw(root, conn, row["current_record_object_sha"]))
            day = publication_day(listing)
            if day is None:
                unknown += 1
                continue
            if not args.first <= day <= args.last:
                outside += 1
                continue
            raw = read_raw(root, conn, row["response_object_sha"])
            print(json.dumps({"type": "record", "source_record_id": row["id"], "natural_key": row["natural_key"],
                "version_raw": row["version_raw"], "family": row["family"], "published_day_vn": str(day),
                "source_error": row["error"], "response_sha": row["response_object_sha"],
                "listing_context": {k: listing.get(k) for k in ("notifyNo", "notifyVersion", "notifyId", "id", "processApply", "bidForm")},
                "raw_base64": base64.b64encode(raw).decode("ascii")}), flush=True)
            exported += 1
        print(json.dumps({"type": "summary", "exported": exported, "outside_scope": outside, "unknown_date": unknown,
                          "network_requests": 0, "writes": 0}), flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
