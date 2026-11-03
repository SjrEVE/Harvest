"""Read legacy SQLite v1-v5 / export bundles without executing or modifying their contents."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from santhau_studio.domain.identity import canonical_bytes
from santhau_studio.domain.redaction import redact_payload

CATALOG_TABLES = ("runs", "source_runs", "records", "record_versions", "observations", "coverage_windows",
                  "sync_state", "checkpoints", "reconcile_gaps", "integrity_repairs")


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def safe_path(root: Path, reference: str) -> Path:
    path = (root / reference).resolve()
    if Path(reference).is_absolute() or not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("legacy raw reference escapes archive root")
    return path


def inspect_legacy(source: Path) -> dict:
    root = source.parent if source.is_file() else source
    warehouse = root / "warehouse.sqlite"
    if warehouse.is_file():
        conn = sqlite3.connect(warehouse.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"schema_version", "records", "record_versions", "observations"} <= tables:
                raise ValueError("not a supported legacy harvester warehouse")
            version = int(conn.execute("SELECT version FROM schema_version").fetchone()[0])
            if not 1 <= version <= 5:
                raise ValueError(f"unsupported legacy schema {version}")
            return {"root": str(root.resolve()), "format": "warehouse", "schema_version": version,
                    "counts": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                               for t in CATALOG_TABLES if t in tables}}
        finally:
            conn.close()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("export_schema_version") != 1:
        raise ValueError("unsupported legacy export schema")
    entry = next((e for e in manifest.get("files", []) if e.get("path") == "records.jsonl"), None)
    if not entry or digest_file(root / "records.jsonl") != entry.get("sha256"):
        raise ValueError("legacy export checksum mismatch")
    return {"root": str(root.resolve()), "format": "bundle", "schema_version": manifest.get("warehouse_schema_version"),
            "counts": {"records": manifest.get("record_count")}, "manifest": manifest}


class LegacyReader:
    """Disk-backed staging index: raw gzip files are decoded once, not once per observation."""

    def __init__(self, source: Path, scratch: Path) -> None:
        self.info = inspect_legacy(source)
        self.root = Path(self.info["root"])
        self.cache = sqlite3.connect(scratch / "legacy-cache.sqlite")
        self.cache.row_factory = sqlite3.Row
        self.cache.executescript("""
          CREATE TABLE events(seq INTEGER PRIMARY KEY,kind TEXT,event_key TEXT,metadata TEXT,
            raw_file TEXT,raw_line INTEGER,payload TEXT,error TEXT,legacy_raw_hash TEXT);
          CREATE INDEX refs ON events(raw_file,raw_line);
          CREATE TABLE catalog(kind TEXT,payload TEXT);
        """)
        self.fingerprint = hashlib.sha256()
        try:
            if self.info["format"] == "warehouse":
                self._warehouse()
            else:
                self._bundle()
            self._raw_files()
            self.cache.commit()
            self.key = self.fingerprint.hexdigest()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.cache.close()

    def _catalog(self, kind: str, row: dict) -> None:
        clean = redact_payload(row)
        payload = canonical_bytes(clean)
        self.fingerprint.update(kind.encode() + b"\0" + payload + b"\n")
        self.cache.execute("INSERT INTO catalog VALUES(?,?)", (kind, payload.decode("utf-8")))

    def _event(self, kind: str, row: dict) -> None:
        clean = redact_payload(row)
        identity = [kind, clean.get("source"), clean.get("source_record_id"),
                    clean.get("version_no") if kind == "version" else clean.get("run_id"),
                    None if kind == "version" else clean.get("id")]
        key = hashlib.sha256(canonical_bytes(identity)).hexdigest()
        self.cache.execute("INSERT INTO events(kind,event_key,metadata,raw_file,raw_line) VALUES(?,?,?,?,?)",
                           (kind, key, json.dumps(clean, ensure_ascii=False), clean.get("raw_file"), clean.get("raw_line")))

    def _warehouse(self) -> None:
        conn = sqlite3.connect((self.root / "warehouse.sqlite").as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in CATALOG_TABLES:
                if table in tables:
                    for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                        self._catalog(table, dict(row))
            for row in conn.execute(
                "SELECT v.*, r.record_type FROM record_versions v LEFT JOIN records r"
                " ON r.source=v.source AND r.source_record_id=v.source_record_id"
                " ORDER BY v.source,v.source_record_id,v.version_no"
            ):
                self._event("version", dict(row))
            for row in conn.execute("SELECT * FROM observations ORDER BY fetched_at,id"):
                self._event("observation", dict(row))
        finally:
            conn.close()

    def _bundle(self) -> None:
        with (self.root / "records.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                self._catalog("records", row)
                for version in row.get("versions", []):
                    self._event("version", {**version, "source": row.get("source"),
                        "source_record_id": row.get("source_record_id"), "record_type": row.get("record_type")})

    def _raw_files(self) -> None:
        references = [r[0] for r in self.cache.execute("SELECT DISTINCT raw_file FROM events ORDER BY raw_file")]
        for reference in references:
            try:
                if not reference:
                    raise ValueError("legacy event has no raw reference")
                path = safe_path(self.root, reference)
                self.fingerprint.update(reference.encode() + digest_file(path).encode())
                wanted = {r[0] for r in self.cache.execute("SELECT raw_line FROM events WHERE raw_file=?", (reference,))}
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    for number, line in enumerate(handle, 1):
                        if number not in wanted:
                            continue
                        value = json.loads(line)
                        if not isinstance(value, dict) or not isinstance(value.get("raw_payload"), dict):
                            raise ValueError("legacy line lacks a real raw_payload object")
                        original_hash = hashlib.sha256(json.dumps(value["raw_payload"], ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                        payload = json.dumps(redact_payload(value), ensure_ascii=False)
                        self.cache.execute("UPDATE events SET payload=?,legacy_raw_hash=? WHERE raw_file=? AND raw_line=?",
                                           (payload, original_hash, reference, number))
                self.cache.execute("UPDATE events SET error='RAW_LINE_MISSING' WHERE raw_file=? AND payload IS NULL", (reference,))
            except (OSError, EOFError, ValueError) as exc:
                self.fingerprint.update(str(reference).encode() + type(exc).__name__.encode())
                self.cache.execute("UPDATE events SET payload=NULL,error=? WHERE raw_file IS ?",
                                   (f"{type(exc).__name__}: {exc}", reference))

    def events(self):
        yield from self.cache.execute("SELECT * FROM events ORDER BY seq")

    def catalog(self, kind: str):
        for row in self.cache.execute("SELECT payload FROM catalog WHERE kind=?", (kind,)):
            yield json.loads(row[0])
