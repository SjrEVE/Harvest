import json
from contextlib import closing
from pathlib import Path

import pytest

from santhau_studio.linux.config import initialize, make_jobs
from santhau_studio.storage.warehouse import connect_read_only


def profile():
    return Path(__file__).resolve().parents[2] / "packaging/linux/config/egp-profile.json"


def test_verified_profile_only_creates_rules_not_collection_proof(tmp_path):
    initialize(tmp_path, profile())
    with closing(connect_read_only(tmp_path / "warehouse.sqlite")) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM filter_boundary_rules WHERE canary_verified=1"
            ).fetchone()[0]
            == 6
        )
        assert conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 0
    jobs = make_jobs(tmp_path, mode="BACKFILL", family="ALL", first="2026-01-01", last="2026-01-10")
    assert len(jobs) == 1 and len(jobs[0].sources) == 6 and jobs[0].scope["cadence"] == "WEEK"
    assert jobs[0].interval().duration.days == 10
    assert not list(tmp_path.glob("serving*"))


def test_no_profile_means_unverified_source_cannot_queue(tmp_path):
    initialize(tmp_path, None)
    with pytest.raises(ValueError, match="chưa"):
        make_jobs(tmp_path, mode="BACKFILL", family="ALL", first="2026-01-01", last="2026-01-10")


def test_bad_profile_rejected_before_any_rules_are_inserted(tmp_path):
    value = json.loads(profile().read_text())
    value["rules"][-1]["endpoint"] = "https://other.example.invalid/"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        initialize(tmp_path / "root", bad)
    assert not (tmp_path / "root/warehouse.sqlite").exists()


@pytest.mark.parametrize(
    ("first", "last"),
    [("2026-02-31", "2026-03-01"), ("2026-02-03", "2026-02-01"), ("2099-01-01", "2099-01-02")],
)
def test_dates_validate_before_queue_write(tmp_path, first, last):
    initialize(tmp_path, profile())
    with pytest.raises(ValueError):
        make_jobs(tmp_path, mode="BACKFILL", family="ALL", first=first, last=last)
    assert not list((tmp_path / "jobs/queued").glob("*"))
