"""RC3-6: reconciliation must target only drifted day/family windows.

The UI label "Cần đối chiếu" comes from stored > trusted source total, and the
UI discards legacy/drifted proofs. The planner must use the very same proof set.
"""
import sqlite3
from datetime import date

from santhau_studio.domain.coverage import vn_calendar_day

FAMILY = "TBMT"


def _iso(dt):
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


SCHEMA = """
CREATE TABLE record_fields (source_key TEXT, family TEXT, collection_date TEXT);
CREATE TABLE coverage_proofs (
    id INTEGER PRIMARY KEY, proof_version INTEGER, source_key TEXT, family TEXT,
    interval_from_utc TEXT, interval_to_utc TEXT, boundary_rule_id INTEGER,
    total_first INTEGER, total_last INTEGER, total_trusted INTEGER,
    verified_at_utc TEXT);
CREATE TABLE coverage_intervals (
    id INTEGER PRIMARY KEY, source_key TEXT, family TEXT,
    interval_from_utc TEXT, interval_to_utc TEXT, granularity TEXT,
    status TEXT, proof_id INTEGER);
CREATE TABLE filter_boundary_rules (
    id INTEGER PRIMARY KEY, rule_key TEXT, rule_version INTEGER,
    source_key TEXT, family TEXT, endpoint TEXT, adapter_sha TEXT,
    boundary_kind TEXT, precision TEXT, source_timezone TEXT,
    offset_minutes INTEGER, conversion_params TEXT, canary_verified INTEGER,
    verified_at_utc TEXT);
CREATE TABLE reconcile_gaps (
    id INTEGER PRIMARY KEY, source_key TEXT, family TEXT,
    interval_from_utc TEXT, interval_to_utc TEXT, kind TEXT,
    acknowledged_at_utc TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT, updated_at_utc TEXT);
"""


def _serving(tmp_path, stored_by_day, *, proof_versions=None, gaps=()):
    conn = sqlite3.connect(tmp_path / "serving.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO filter_boundary_rules VALUES "
        "(1,'rule',1,'egp_home',?,'endpoint','sha','CLOSED_LOCAL','DAY',"
        "'Asia/Ho_Chi_Minh',0,'{}',1,'2026-11-01T00:00:00Z')",
        (FAMILY,),
    )
    proof_versions = proof_versions or {}
    for proof_id, (day, (stored, total)) in enumerate(stored_by_day.items(), start=1):
        span = vn_calendar_day(day)
        conn.execute(
            "INSERT INTO coverage_proofs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (proof_id, proof_versions.get(day, 1), "egp_home", FAMILY,
             _iso(span.from_utc), _iso(span.to_utc), 1,
             total, total, 1, "2026-11-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO coverage_intervals VALUES (?,?,?,?,?,?,?,?)",
            (proof_id, "egp_home", FAMILY, _iso(span.from_utc),
             _iso(span.to_utc), "DAY", "COMPLETE", proof_id),
        )
        for _ in range(stored):
            conn.execute(
                "INSERT INTO record_fields VALUES (?,?,?)",
                ("egp_home", FAMILY, day.isoformat()),
            )
    for gap_id, (day, kind) in enumerate(gaps, start=1):
        span = vn_calendar_day(day)
        conn.execute(
            "INSERT INTO reconcile_gaps VALUES (?,?,?,?,?,?,?)",
            (gap_id, "egp_home", FAMILY, _iso(span.from_utc),
             _iso(span.to_utc), kind, None),
        )
    conn.commit()
    return conn


def _scope(first, last):
    return {
        "date_from": first,
        "date_to": last,
        "sources": [{"source_key": "egp_home", "family": FAMILY}],
    }


def test_only_drifted_day_is_selected(tmp_path):
    from santhau_studio.storage.coverage_targets import targeted_reconcile_windows

    conn = _serving(tmp_path, {
        date(2026, 11, 1): (12, 10),
        date(2026, 11, 2): (10, 10),
    })
    try:
        targets = targeted_reconcile_windows(conn, _scope("2026-11-01", "2026-11-02"))
    finally:
        conn.close()
    assert len(targets) == 1
    assert targets[0]["family"] == FAMILY
    assert targets[0]["stored"] == 12 and targets[0]["source_total"] == 10
    assert targets[0]["from"] == "2026-10-31T17:00:00+00:00"


def test_clean_snapshot_selects_nothing(tmp_path):
    from santhau_studio.storage.coverage_targets import targeted_reconcile_windows

    conn = _serving(tmp_path, {date(2026, 11, 1): (10, 10)})
    try:
        assert targeted_reconcile_windows(conn, _scope("2026-11-01", "2026-11-01")) == []
    finally:
        conn.close()


def test_quota_caps_the_number_of_targets(tmp_path):
    from santhau_studio.storage.coverage_targets import targeted_reconcile_windows

    conn = _serving(tmp_path, {
        date(2026, 11, 1): (12, 10),
        date(2026, 11, 2): (13, 10),
        date(2026, 11, 3): (14, 10),
    })
    try:
        targets = targeted_reconcile_windows(
            conn, _scope("2026-11-01", "2026-11-03"), limit=2
        )
    finally:
        conn.close()
    assert len(targets) == 2
    assert [t["stored"] for t in targets] == [12, 13]


def test_missing_serving_tables_are_fail_safe(tmp_path):
    from santhau_studio.storage.coverage_targets import targeted_reconcile_windows

    conn = sqlite3.connect(tmp_path / "empty.sqlite")
    try:
        assert targeted_reconcile_windows(conn, _scope("2026-11-01", "2026-11-01")) == []
    finally:
        conn.close()


def test_legacy_proof_excluded_like_ui(tmp_path):
    """P1: the planner must not trust a proof the coverage UI already discards."""
    from santhau_studio.storage.coverage_targets import targeted_reconcile_windows

    first, second = date(2026, 11, 1), date(2026, 11, 2)
    conn = _serving(
        tmp_path, {first: (12, 10), second: (12, 10)},
        proof_versions={first: 2, second: 1},
    )
    try:
        targets = targeted_reconcile_windows(conn, _scope("2026-11-01", "2026-11-02"))
    finally:
        conn.close()
    assert len(targets) == 1
    assert targets[0]["from"] == "2026-11-01T17:00:00+00:00"


def test_open_drift_gap_is_selected_even_without_count_drift(tmp_path):
    """P2: an unacknowledged HASH_DRIFT gap is also a 'Cần đối chiếu lại' target."""
    from santhau_studio.storage.coverage_targets import targeted_reconcile_windows

    day = date(2026, 11, 1)
    conn = _serving(
        tmp_path, {day: (10, 10)}, gaps=[(day, "HASH_DRIFT")]
    )
    try:
        targets = targeted_reconcile_windows(conn, _scope("2026-11-01", "2026-11-01"))
    finally:
        conn.close()
    assert len(targets) == 1
    assert targets[0]["reason"] == "NEEDS_RECONCILE"
