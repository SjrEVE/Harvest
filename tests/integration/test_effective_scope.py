import hashlib
import json

import pytest
from test_coverage_store import day_interval, make_proof
from test_coverage_store import env as env

from santhau_studio.storage import coverage


def test_new_adapter_context_does_not_reuse_old_complete_proof(env):
    conn, rule_id, _sha = env
    span = day_interval()
    coverage.record_interval(
        conn,
        run_id="old",
        status="COMPLETE",
        proof=make_proof(span, boundary_rule_id=rule_id),
        source_key="egp_home",
        family="TBMT",
        interval=span,
        granularity="DAY",
    )
    conn.execute(
        "INSERT INTO filter_boundary_rules(rule_key,rule_version,source_key,family,endpoint,adapter_sha,boundary_kind,precision,source_timezone,offset_minutes,conversion_params,canary_verified,verified_at_utc) "
        "SELECT rule_key,2,source_key,family,endpoint,?,boundary_kind,precision,source_timezone,offset_minutes,conversion_params,canary_verified,'2026-11-03T00:00:00Z' FROM filter_boundary_rules WHERE id=?",
        (hashlib.sha256(b"new-adapter").hexdigest(), rule_id),
    )
    resolved = coverage.resolve(conn, "egp_home", "TBMT", span.from_utc, span.to_utc)
    assert not resolved.can_skip and resolved.gaps == (span,) and not resolved.proven
    assert "PROOF_CONTEXT_INCOMPATIBLE" in resolved.reasons


def test_effective_scope_uses_proof_union_and_keeps_middle_gap(env):
    from santhau_studio.domain.coverage import Interval

    conn, rule_id, _sha = env
    for index in (0, 2):
        day = day_interval(index)
        coverage.record_interval(
            conn,
            run_id="complete",
            status="COMPLETE",
            proof=make_proof(day, boundary_rule_id=rule_id),
            source_key="egp_home",
            family="TBMT",
            interval=day,
            granularity="DAY",
        )
    scope = Interval(day_interval().from_utc, day_interval(2).to_utc)
    coverage.record_interval(
        conn,
        run_id="old-partial",
        status="PARTIAL",
        proof=None,
        source_key="egp_home",
        family="TBMT",
        interval=scope,
        granularity="DAY",
    )
    assert coverage.resolve(conn, "egp_home", "TBMT", scope.from_utc, scope.to_utc).gaps == (
        day_interval(1),
    )
    middle = day_interval(1)
    coverage.record_interval(
        conn,
        run_id="repaired",
        status="COMPLETE",
        proof=make_proof(middle, boundary_rule_id=rule_id),
        source_key="egp_home",
        family="TBMT",
        interval=middle,
        granularity="DAY",
    )
    assert coverage.resolve(conn, "egp_home", "TBMT", scope.from_utc, scope.to_utc).can_skip
    assert (
        conn.execute("SELECT COUNT(*) FROM coverage_intervals WHERE status='PARTIAL'").fetchone()[0]
        == 1
    )


def test_scope_config_and_report_keep_data_roots_and_date_ranges_separate(tmp_path):
    from santhau_studio.linux.fixture import initialize_fixture
    from santhau_studio.linux.repair import configure_scope, coverage_report

    box, linode = tmp_path / "box", tmp_path / "linode"
    initialize_fixture(box)
    initialize_fixture(linode)
    configure_scope(box, first="2025-07-01", last="2025-12-31", families=("TBMT", "KQLCNT"))
    configure_scope(linode, first="2026-09-01", last="2026-09-30")
    a, b = coverage_report(box), coverage_report(linode)
    assert a["scope"]["date_from"] == "2025-07-01" and b["scope"]["date_from"] == "2026-09-01"
    assert a["summary"]["day_family_windows"] == 368 and b["summary"]["day_family_windows"] == 180
    assert a["summary"]["never_attempted"] == 368 and b["summary"]["never_attempted"] == 180
    assert not a["summary"]["proved"] and not b["summary"]["proved"]
    assert "2026-11-03" not in json.dumps(a["windows"])


def test_scope_report_requires_explicit_configuration(tmp_path):
    from santhau_studio.linux.fixture import initialize_fixture
    from santhau_studio.linux.repair import coverage_report

    initialize_fixture(tmp_path)
    with pytest.raises(ValueError, match="SCOPE_NOT_CONFIGURED"):
        coverage_report(tmp_path)
