"""RC3-12: one unified build — seven public detail lanes + the ALL sweep.

Fixtures are trimmed, redacted live captures from the 2026-11-17 canary
(see ~/workspace/msc-evidence-verification-2026-11-17.md for the endpoint
table): TBMT detail/HSMT IB2600612345 (DTRR, LDT), YCBG RQ2600103987,
eShop BP2600926605, KHLCNT PL2600409994, DU_AN PR2600075800,
HOP_DONG HD2600304139. Lists are trimmed to <=2 items; the large unparsed
formValue/productList/lotResultDTO strings and the YCBG responsible*
contact fields / DU_AN competentPersons are dropped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest

from santhau_studio.collectors.cgttrg import (
    DETAIL_ENDPOINTS,
    DETAIL_PARSER_KINDS,
    PREFIX,
    parse_public,
    public_spec,
)
from santhau_studio.collectors.deep import DeepLaneSpec
from santhau_studio.collectors.egp_home import Page
from santhau_studio.collectors.http_client import Attempt
from santhau_studio.collectors.public_deep import (
    ADAPTER_VERSION,
    PublicDeepFetcher,
    identity_problem,
)
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES
from santhau_studio.domain.coverage import vn_calendar_day
from santhau_studio.storage.warehouse import WarehouseWriter
from santhau_studio.workers.jobs import JobRequest
from santhau_studio.workers.runner import Runner

ENDPOINT = SOURCES[SOURCE_EGP_HOME].endpoint
ADAPTER_SHA = hashlib.sha256(b"deep-offline").hexdigest()
DAY = vn_calendar_day(date(2026, 11, 1))
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rc312"

ES_ID = "11111111-1111-4111-8111-111111111111"


def load_fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


# ---------------------------------------------------------------- spec coverage

NEW_PAIRS = [
    ("TBMT", "DETAIL", "DETAIL"),
    ("TBMT", "HSMT", "HSMT"),
    ("YCBG", "DETAIL", "YCBG_DETAIL"),
    ("ESHOP", "DETAIL", "ESHOP_DETAIL"),
    ("KHLCNT", "DETAIL", "KHLCNT_DETAIL"),
    ("DU_AN", "DETAIL", "DUAN_DETAIL"),
    ("HOP_DONG", "DETAIL", "CONTRACT_DETAIL"),
]


@pytest.mark.parametrize("family,lane,kind", NEW_PAIRS)
def test_public_spec_covers_new_pairs(family, lane, kind):
    spec = public_spec(family, lane)
    assert isinstance(spec, DeepLaneSpec)
    assert spec.parser_kind == kind
    assert spec.requires_session is False
    assert spec.parser_version == 7
    path, _query = DETAIL_ENDPOINTS.get((family, lane), (None, None))
    assert path is not None
    if (family, lane) in DETAIL_PARSER_KINDS:
        assert spec.endpoint_kind == "public-" + family.lower() + "-" + path.replace("/", "-")
    assert spec.version_fields


@pytest.mark.parametrize(
    "family,lane",
    [
        ("TBMT", "M1"),      # M1 is KQLCNT-only.
        ("KQLCNT", "DETAIL"),  # KQLCNT has no detail endpoint.
        ("YCBG", "HSMT"),
        ("YCBG", "REOFFER"),
        ("ESHOP", "M1"),
        ("ESHOP", "HSMT"),
        ("KHLCNT", "REOFFER"),
        ("DU_AN", "M1"),
        ("HOP_DONG", "HSMT"),
    ],
)
def test_public_spec_rejects_invalid_pairs(family, lane):
    assert public_spec(family, lane) is None


def test_tbmt_legacy_specs_unchanged():
    # One spec serves both bidForms of the TBMT lanes; the parser and the
    # fetcher dispatch on bidForm/payload shape, so the CGTTRG specs keep
    # their exact endpoint_kind and parser_kind.
    assert public_spec("TBMT", "DETAIL").endpoint_kind == "public-cgttrg-online-reoffer/detail"
    assert public_spec("TBMT", "DETAIL").parser_kind == "DETAIL"
    assert public_spec("TBMT", "HSMT").endpoint_kind == "public-cgttrg-lcnt_tbmcgtt_hsmt"
    assert public_spec("TBMT", "HSMT").parser_kind == "HSMT"
    assert public_spec("TBMT", "REOFFER").parser_kind == "REOFFER"


# ---------------------------------------------------------------- parsers on live fixtures

def test_tbmt_detail_parses_plan_price_and_relation():
    parsed = parse_public(public_spec("TBMT", "DETAIL"), load_fixture("tbmt_detail"))
    assert parsed.parser_version == 7
    assert parsed.capability == "NOTICE_METADATA"
    assert parsed.problems == ()
    assert [m.role for m in parsed.money] == ["BID_PRICE"]
    assert parsed.money[0].value == 8_775_977_000.0
    assert parsed.money[0].currency_code == "VND"
    assert parsed.money[0].owner_kind == "PACKAGE"
    assert len(parsed.relations) == 1
    assert parsed.relations[0]["relation"] == "PLAN_HAS_NOTICE"
    assert parsed.relations[0]["key"] == "PL2600409112"
    assert json.loads(parsed.source_version_json)["notifyNo"] == "IB2600612345"


def test_tbmt_hsmt_parses_chapters_without_lots():
    parsed = parse_public(public_spec("TBMT", "HSMT"), load_fixture("tbmt_hsmt"))
    assert parsed.capability == "NOTICE_METADATA"
    assert parsed.money == ()
    assert parsed.lots == ()
    chapters = parsed.hsmt["chapters"]
    assert len(chapters) == 2
    assert {c["chapterCode"] for c in chapters} <= {"BD_CONTRACT_CONDITION", "C4", "C3", "BD_DATA_TABLE", "P2"}
    assert all(c["bidForm"] == "DTRR" for c in chapters)
    assert json.loads(parsed.source_version_json)["notifyNo"] == "IB2600612345"


def test_tbmt_hsmt_dispatch_keeps_cgtttrg_parser():
    # A CGTTRG HSMT payload must still take the _hsmt parser (Table lots),
    # never the generic chapter parser: the dispatch is on the payload's
    # bidForm, and the CGTTRG path stays byte-identical.
    root = Path(__file__).resolve().parents[1] / "fixtures" / "hsmt-linode-20261106"
    cases = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    case = next(c for c in cases if c["expected"]["identity_problem"] is None)
    parsed = parse_public(public_spec("TBMT", "HSMT"), case["payload"])
    assert not parsed.problems
    assert parsed.capability == case["expected"]["capability"]
    assert len(parsed.lots) == case["expected"]["lots"]


def test_ycbg_detail_parses_reference_documents():
    parsed = parse_public(public_spec("YCBG", "DETAIL"), load_fixture("ycbg_detail"))
    assert parsed.capability == "NOTICE_METADATA"
    assert parsed.money == ()
    assert len(parsed.documents) == 2
    kinds = {d["document_kind"] for d in parsed.documents}
    assert kinds == {"REQUEST_QUOTE_DECISION", "REQUEST_QUOTE_ATTACHMENT"}
    assert all(d["download_status"] == "REFERENCE_ONLY" for d in parsed.documents)
    assert all(d["download_reason"] == "DOWNLOAD_ENDPOINT_UNVERIFIED" for d in parsed.documents)
    assert json.loads(parsed.source_version_json)["requestQuoteNo"] == "RQ2600103987"


def test_ycbg_detail_tolerates_missing_request_quote_file():
    payload = load_fixture("ycbg_detail")
    payload["bidoRequestQuote"]["requestQuoteFile"] = None
    parsed = parse_public(public_spec("YCBG", "DETAIL"), payload)
    assert parsed.documents == ()
    assert parsed.problems == ()


def test_eshop_detail_parses_prices_and_offerors():
    parsed = parse_public(public_spec("ESHOP", "DETAIL"), load_fixture("eshop_detail"))
    assert parsed.capability == "NOTICE_METADATA"
    assert [(m.role, m.value) for m in parsed.money] == [
        ("BID_PRICE", 489_347_500.0),
        ("WIN_PRICE", 430_185_000.0),
    ]
    assert all(m.currency_code == "VND" for m in parsed.money)
    assert len(parsed.members) == 2
    assert {m.party_name for m in parsed.members} == {
        "CÔNG TY TNHH SG PHARMA",
        "CÔNG TY TNHH THƯƠNG MẠI BÁCH QUANG",
    }
    assert all(m.role == "OFFEROR" for m in parsed.members)
    assert json.loads(parsed.source_version_json) == {"bidNo": "BP2600926605", "orderNo": "OR2600000023"}


def test_khlcnt_detail_is_envelope_only():
    parsed = parse_public(public_spec("KHLCNT", "DETAIL"), load_fixture("khlcnt_detail"))
    assert parsed.capability == "NOTICE_METADATA"
    assert parsed.money == () and parsed.documents == () and parsed.relations == () and parsed.members == ()
    assert json.loads(parsed.source_version_json) == {"planNo": "PL2600409994", "planVersion": "01"}


def test_duan_detail_parses_budget_and_decision_document():
    parsed = parse_public(public_spec("DU_AN", "DETAIL"), load_fixture("duan_detail"))
    assert [(m.role, m.value) for m in parsed.money] == [("BUDGET", 701_213_000.0)]
    assert parsed.money[0].currency_code == "VND"
    assert len(parsed.documents) == 1
    assert parsed.documents[0]["document_kind"] == "PROJECT_DECISION"
    assert parsed.documents[0]["source_file_id"] == "71e95408-acef-4415-ab1f-9123a9d842d7"
    assert json.loads(parsed.source_version_json) == {"pno": "PR2600075800", "pversion": "01"}


def test_contract_detail_parses_contract_value():
    parsed = parse_public(public_spec("HOP_DONG", "DETAIL"), load_fixture("contract_detail"))
    assert [(m.role, m.value) for m in parsed.money] == [("CONTRACT_VALUE", 20_000_000.0)]
    assert parsed.money[0].currency_code == "VND"
    assert parsed.money[0].owner_kind == "PACKAGE"
    assert json.loads(parsed.source_version_json) == {"contractCode": "HD2600304139", "contractVersion": "00"}


def test_detail_parsers_reject_missing_envelopes():
    assert public_spec("YCBG", "DETAIL") is not None
    with pytest.raises(Exception, match="YCBG_DETAIL_ENVELOPE_INVALID"):
        parse_public(public_spec("YCBG", "DETAIL"), {"bidoRequestQuote": None})
    with pytest.raises(Exception, match="KHLCNT_DETAIL_ENVELOPE_INVALID"):
        parse_public(public_spec("KHLCNT", "DETAIL"), {})
    with pytest.raises(Exception, match="CONTRACT_DETAIL_ENVELOPE_INVALID"):
        parse_public(public_spec("HOP_DONG", "DETAIL"), {"contract": []})


# ---------------------------------------------------------------- identity

IDENTITY_CASES = [
    # (fixture, family, lane, natural key, version) — the expected request id
    # is read from the fixture itself (the live ES id each payload carries).
    ("tbmt_detail", "TBMT", "DETAIL", "IB2600612345", "00"),
    ("tbmt_hsmt", "TBMT", "HSMT", "IB2600612345", "00"),
    ("ycbg_detail", "YCBG", "DETAIL", "RQ2600103987", "00"),
    ("eshop_detail", "ESHOP", "DETAIL", "BP2600926605", "OR2600000023"),
    ("khlcnt_detail", "KHLCNT", "DETAIL", "PL2600409994", "01"),
    ("duan_detail", "DU_AN", "DETAIL", "PR2600075800", "01"),
    ("contract_detail", "HOP_DONG", "DETAIL", "HD2600304139", "00"),
]


def _expected_id(fixture, family, lane):
    payload = load_fixture(fixture)
    if lane == "HSMT":
        return payload["bidoInvBiddingDTO"][0]["notifyId"]
    if family == "TBMT":
        return payload["bidoNotifyContractorM"]["id"]
    if family == "YCBG":
        return payload["bidoRequestQuote"]["id"]
    if family == "ESHOP":
        return None  # the eShop request carries bidNo, not id.
    if family == "KHLCNT":
        return payload["bidPoBidpPlanProjectDetailView"]["id"]
    if family == "DU_AN":
        return payload["id"]
    if family == "HOP_DONG":
        return payload["contract"]["id"]
    raise AssertionError(family)


@pytest.mark.parametrize("fixture,family,lane,key,version", IDENTITY_CASES)
def test_identity_accepts_matching_payload(fixture, family, lane, key, version):
    payload = load_fixture(fixture)
    record = {"family": family, "natural_key": key, "version_raw": version}
    assert identity_problem(payload, record, lane, _expected_id(fixture, family, lane)) is None


@pytest.mark.parametrize("fixture,family,lane,key,version", IDENTITY_CASES)
def test_identity_rejects_wrong_notice(fixture, family, lane, key, version):
    assert key != "NO-SUCH-KEY"  # the wrong notice must actually differ.
    payload = load_fixture(fixture)
    record = {"family": family, "natural_key": "NO-SUCH-KEY", "version_raw": version}
    assert identity_problem(payload, record, lane, _expected_id(fixture, family, lane)) == "NOTICE_IDENTITY_MISMATCH"


@pytest.mark.parametrize("fixture,family,lane,key,version", IDENTITY_CASES)
def test_identity_rejects_wrong_version(fixture, family, lane, key, version):
    assert version != "99"  # the wrong version must actually differ.
    payload = load_fixture(fixture)
    record = {"family": family, "natural_key": key, "version_raw": "99"}
    assert identity_problem(payload, record, lane, _expected_id(fixture, family, lane)) == "NOTICE_VERSION_MISMATCH"


def test_identity_rejects_missing_envelope():
    assert identity_problem({}, {"family": "YCBG", "natural_key": "RQ1", "version_raw": "00"}, "DETAIL", None) == "IDENTITY_SHAPE_INVALID"
    assert identity_problem({}, {"family": "KHLCNT", "natural_key": "PL1", "version_raw": "00"}, "DETAIL", None) == "IDENTITY_SHAPE_INVALID"
    assert identity_problem({}, {"family": "HOP_DONG", "natural_key": "HD1", "version_raw": "00"}, "DETAIL", None) == "IDENTITY_SHAPE_INVALID"
    # DU_AN carries identity at the top level: {} has no pno, so it is a
    # mismatch, not a missing envelope.
    assert identity_problem({}, {"family": "DU_AN", "natural_key": "PR1", "version_raw": "00"}, "DETAIL", None) == "NOTICE_IDENTITY_MISMATCH"


def test_identity_rejects_wrong_uuid():
    payload = load_fixture("duan_detail")
    record = {"family": "DU_AN", "natural_key": "PR2600075800", "version_raw": "01"}
    assert identity_problem(payload, record, "DETAIL", "00000000-0000-0000-0000-000000000000") == "NOTICE_UUID_MISMATCH"


# ---------------------------------------------------------------- fetcher routing (fake lane, no HTTP)

@dataclass
class FakeOutcome:
    payload: dict
    http_status: int = 200
    attempts: tuple = (Attempt(1, "2026-11-17T00:00:00Z", http_status=200),)


class FakeLane:
    calls: ClassVar[list] = []
    payload: ClassVar[dict] = {}

    def __init__(self, *, url, source_key, budget, retry, connect_timeout_seconds, query=None):
        self.url = url
        self.query = query
        self.source_key = source_key
        self.budget = budget
        self.retry = retry
        self.connect_timeout_seconds = connect_timeout_seconds

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, body, min_interval):
        FakeLane.calls.append({"url": self.url, "query": self.query, "body": body,
                               "min_interval": min_interval})
        return FakeOutcome(payload=FakeLane.payload)


def _make_request(family, lane):
    return JobRequest(
        job_id="test-deep-1",
        job_kind=lane,
        requested_by="test",
        sources=({"source_key": "egp_home", "family": family},),
        scope={},
        request_limits={"max_requests": 30, "max_seconds": 300, "pacing_seconds": 3, "max_attempts": 4, "connect_timeout_seconds": 15},
        created_at="2026-11-17T00:00:00Z",
    )


def _fetch_with_fake(monkeypatch, writer, record, payload):
    import santhau_studio.collectors.public_deep as public_deep

    FakeLane.calls = []
    FakeLane.payload = payload
    monkeypatch.setattr(public_deep, "FetchLane", FakeLane)
    fetcher = PublicDeepFetcher(writer, _make_request(record["family"], record["lane"]), stop_requested=lambda: False)
    result = fetcher.fetch_record(record)
    assert result.response_payload == payload
    return fetcher.receipt


def test_fetcher_routes_ycbg_without_token(monkeypatch, tmp_path):
    writer = seed_detail_store(tmp_path / "data")
    try:
        record = dict(writer.conn.execute("SELECT * FROM source_records WHERE natural_key='RQ-0'").fetchone())
        record["lane"] = "DETAIL"
        receipt = _fetch_with_fake(monkeypatch, writer, record, load_fixture("ycbg_detail"))
        assert FakeLane.calls == [{"url": PREFIX + "get-request-quote", "query": None, "body": {"id": ES_ID}, "min_interval": 3}]
        assert receipt["endpoint"] == PREFIX + "get-request-quote"
        assert receipt["expected_notice"] == "RQ-0"
    finally:
        writer.close()


def test_fetcher_routes_eshop_by_bid_no(monkeypatch, tmp_path):
    writer = seed_detail_store(tmp_path / "data")
    try:
        record = dict(writer.conn.execute("SELECT * FROM source_records WHERE natural_key='BP-ESHOP-0'").fetchone())
        record["lane"] = "DETAIL"
        receipt = _fetch_with_fake(monkeypatch, writer, record, load_fixture("eshop_detail"))
        assert FakeLane.calls == [{
            "url": PREFIX + "eshop/shopping-result/detail-public-by-bidNo",
            "query": None,
            "body": {"bidNo": "BP-ESHOP-0"},
            "min_interval": 3,
        }]
        assert receipt["expected_notice"] == "BP-ESHOP-0"
        assert receipt["expected_version"] == "OR-0"
    finally:
        writer.close()


def test_fetcher_routes_tbmt_detail_and_hsmt_by_bid_form(monkeypatch, tmp_path):
    writer = seed_detail_store(tmp_path / "data")
    try:
        record = dict(writer.conn.execute("SELECT * FROM source_records WHERE natural_key='IB-DTRR-0'").fetchone())
        record["lane"] = "DETAIL"
        _fetch_with_fake(monkeypatch, writer, record, load_fixture("tbmt_detail"))
        assert FakeLane.calls == [{"url": PREFIX + "lcnt_tbmt_ttc_ldt", "query": "token=", "body": {"id": ES_ID}, "min_interval": 3}]
        record["lane"] = "HSMT"
        _fetch_with_fake(monkeypatch, writer, record, load_fixture("tbmt_hsmt"))
        assert FakeLane.calls == [{
            "url": PREFIX + "lcnt_tbmt_hsmt", "query": "token=",
            "body": {"id": ES_ID, "processApply": "LDT"},
            "min_interval": 3,
        }]
    finally:
        writer.close()


def test_fetcher_keeps_cgtttrg_on_legacy_endpoint(monkeypatch, tmp_path):
    writer = seed_detail_store(tmp_path / "data")
    try:
        # The CGTTRG row keeps the legacy path: the reoffer endpoint, no
        # token query, no processApply in the body.
        record = dict(writer.conn.execute("SELECT * FROM source_records WHERE natural_key='IB-CG-0'").fetchone())
        record["lane"] = "DETAIL"
        _fetch_with_fake(monkeypatch, writer, record, {
            "bidForm": "CGTTRG", "notifyNo": "IB-CG-0", "notifyVersion": "00",
            "contractorsResult": [],
        })
        assert FakeLane.calls == [{"url": PREFIX + "online-reoffer/detail", "query": None, "body": {"id": ES_ID}, "min_interval": 3}]
    finally:
        writer.close()


# ---------------------------------------------------------------- candidates + sweep

DETAIL_ROWS = {
    "TBMT": {"notifyNo": "IB-DTRR-0", "notifyVersion": "00", "bidName": "Gói DTRR 0",
             "bidForm": "DTRR", "processApply": "LDT", "id": ES_ID, "notifyId": ES_ID,
             "publicDate": "2026-11-01T03:00:00Z"},
    "YCBG": {"notifyNo": "RQ-0", "notifyVersion": "00", "bidName": "Yêu cầu báo giá 0",
             "id": ES_ID, "publicDate": "2026-11-01T03:00:00Z"},
    "KHLCNT": {"planNo": "PL-0", "planVersion": "01", "name": "Kế hoạch 0",
               "id": ES_ID, "publicDate": "2026-11-01T03:00:00Z"},
    "DU_AN": {"pno": "PR-0", "pversion": "01", "pname": "Dự án 0",
              "id": ES_ID, "publicDate": "2026-11-01T03:00:00Z"},
    "HOP_DONG": {"contractCode": "HD-0", "contractVersion": "00", "contractNo": "HĐ-0",
                 "id": ES_ID, "publicDate": "2026-11-01T03:00:00Z"},
    "ESHOP": {"bidNo": "BP-ESHOP-0", "orderNo": ["OR-0"], "planNo": "PL-0",
              "bidName": "Mua sắm trực tuyến 0", "pname": "Mua sắm trực tuyến 0",
              "investorName": "Ban QLDA 0", "bidPrice": 100_000_000, "status": "01",
              "id": ES_ID, "publicDate": "2026-11-01T06:00:00Z"},
}


def seed_detail_store(root):
    """One record per detail family (plus a CGTTRG TBMT row for REOFFER)."""
    writer = WarehouseWriter.open(root, create_schema=True, role="deep")
    for family in DETAIL_ROWS:
        writer.conn.execute(
            "INSERT OR IGNORE INTO filter_boundary_rules (rule_key, rule_version, source_key, family, endpoint,"
            " adapter_sha, boundary_kind, precision, source_timezone, offset_minutes, conversion_params,"
            " canary_verified, verified_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("deep-offline-" + family, 1, SOURCE_EGP_HOME, family, ENDPOINT, ADAPTER_SHA,
             "CLOSED_LOCAL", "DAY", "Asia/Ho_Chi_Minh", 420, '{"to_minus":"1s"}', 1, "2026-10-31T00:00:00Z"),
        )
    writer.conn.commit()
    writer.register_run("run-list", "job-list", "BACKFILL")

    def portal_for(rows):
        def portal(_interval, _granularity, _window):
            yield Page(
                payload={"page": {"content": rows, "totalElements": len(rows)}},
                page_no=0, rows=len(rows), has_more=False, total_hint=len(rows),
                total_hint_trusted=True, http_status=200, attempt_log=(),
            )
        return portal

    tbmt_rows = [DETAIL_ROWS["TBMT"], {**DETAIL_ROWS["TBMT"], "notifyNo": "IB-CG-0", "bidForm": "CGTTRG"}]
    for family, rows in [("TBMT", tbmt_rows)] + [(f, [r]) for f, r in DETAIL_ROWS.items() if f != "TBMT"]:
        runner = Runner(writer=writer, source_key=SOURCE_EGP_HOME, family=family,
                        endpoint=ENDPOINT, adapter_sha=ADAPTER_SHA, page_source=portal_for(rows))
        outcome = runner.backfill("run-list", DAY)[0]
        assert outcome.status == "COMPLETE", (family, outcome.status, outcome.blockers)
    return writer


def test_candidates_cover_all_detail_families(tmp_path):
    from santhau_studio.workers.auto_deep import candidates, configure

    writer = seed_detail_store(tmp_path / "data")
    try:
        configure(writer.conn, {"enabled": True, "batch_requests": 30})
        found = {(c["family"], c["lane"]) for c in candidates(writer.conn, 30)}
        assert ("TBMT", "DETAIL") in found
        assert ("TBMT", "HSMT") in found
        assert ("TBMT", "REOFFER") in found  # only the CGTTRG row
        assert ("YCBG", "DETAIL") in found
        assert ("KHLCNT", "DETAIL") in found
        assert ("DU_AN", "DETAIL") in found
        assert ("HOP_DONG", "DETAIL") in found
        assert ("ESHOP", "DETAIL") in found
        assert not any(lane == "M1" for _fam, lane in found)
        # The DTRR row must not be offered REOFFER; the CGTTRG row must.
        reoffer = [c for c in candidates(writer.conn, 30) if c["lane"] == "REOFFER"]
        assert len(reoffer) == 1 and reoffer[0]["natural_key"] == "IB-CG-0"
    finally:
        writer.close()


def test_sweep_all_enqueues_one_job_per_pair(tmp_path):
    from santhau_studio.linux.deep import sweep_deep

    writer = seed_detail_store(tmp_path / "data")
    try:
        preview = sweep_deep(tmp_path / "data", lane="ALL", limit=100)
        assert preview["executed"] is False
        # 7 records, 10 (record, lane) pairs across 8 (lane, family) jobs:
        # DTRR 2, CGTTRG 3, five DETAIL singles.
        assert preview["records_matched"] == 10
        assert preview["jobs"] == 8
        by_pair = {(e["lane"], e["family"]): e["records_matched"] for e in preview["lanes"]}
        assert by_pair[("DETAIL", "TBMT")] == 2
        assert by_pair[("HSMT", "TBMT")] == 2
        assert by_pair[("REOFFER", "TBMT")] == 1
        assert by_pair[("DETAIL", "ESHOP")] == 1
        detail = sweep_deep(tmp_path / "data", lane="DETAIL", limit=100)
        assert detail["records_matched"] == 7
        assert detail["jobs"] == 6
        m1 = sweep_deep(tmp_path / "data", lane="M1", limit=100)
        assert m1["records_matched"] == 0 and m1["jobs"] == 0
    finally:
        writer.close()


def test_adapter_version_bumped_for_unsupported_release():
    # RC3-12 releases previously-UNSUPPORTED tasks (e.g. a non-CGTTRG TBMT
    # DETAIL was UNSUPPORTED_BID_FORM under RC3-11.2) for retry.
    assert ADAPTER_VERSION == 3


@pytest.mark.parametrize(
    ("family", "natural_key", "real_key", "fixture", "role", "value"),
    [
        ("HOP_DONG", "HD-0", "HD2600304139", "contract_detail", "CONTRACT_VALUE", 20_000_000.0),
        ("TBMT", "IB-DTRR-0", "IB2600612345", "tbmt_detail", "BID_PRICE", 8_775_977_000.0),
    ],
)
def test_money_only_capture_is_fetched_not_source_empty(tmp_path, family, natural_key, real_key, fixture, role, value):
    # RC3-12 canary: the new DETAIL lanes are money-only by design, and the
    # run used to label them SOURCE_EMPTY because the emptiness check only
    # counted lots/goods/members/documents. A capture that stored its price
    # is FETCHED.
    from santhau_studio.workers.deep_run import run_deep_batch

    writer = seed_detail_store(tmp_path / "data")
    try:
        rid = writer.conn.execute(
            "SELECT id FROM source_records WHERE natural_key=?", (natural_key,)
        ).fetchone()[0]
        # The fixture carries the live natural key; the seeded row must match
        # it or the run's identity check rejects the capture as PARSE_ERROR.
        writer.conn.execute(
            "UPDATE source_records SET natural_key=? WHERE id=?", (real_key, rid)
        )
        writer.conn.commit()
        result = run_deep_batch(
            writer,
            run_id="run-money-only",
            family=family,
            lane="DETAIL",
            scope={"record_ids": [rid]},
            fetcher=lambda _family, _key: load_fixture(fixture),
            spec_provider=public_spec,
        )
        assert result.detail["source_empty"] == 0
        assert result.detail["captured"] == 1
        state = writer.conn.execute(
            "SELECT state FROM deep_tasks WHERE source_record_id=? AND lane='DETAIL'",
            (rid,),
        ).fetchone()[0]
        assert state == "FETCHED"
        row = writer.conn.execute(
            "SELECT value FROM money_amounts WHERE role=?", (role,)
        ).fetchone()
        assert row is not None and row[0] == value
    finally:
        writer.close()


def test_fetcher_routes_contract_detail_despite_cgtttrg_bid_form(monkeypatch, tmp_path):
    # RC3-12 canary #2: live HOP_DONG rows inherit the package's bidForm, so a
    # contract awarded from a CGTTRG package carries bidForm CGTTRG. The
    # CGTTRG carve-out is TBMT-only — routing such a row to the TBMT legacy
    # path fails it with LISTING_IDENTITY_MISMATCH (no notifyNo on a
    # contract row). The public DETAIL lane must run whatever the bidForm is.
    import json

    from santhau_studio.domain.identity import canonical_bytes

    writer = seed_detail_store(tmp_path / "data")
    try:
        record = dict(writer.conn.execute("SELECT * FROM source_records WHERE natural_key='HD-0'").fetchone())
        record["lane"] = "DETAIL"
        raw = json.loads(writer.store.read(record["current_record_object_sha"]))
        raw["bidForm"] = "CGTTRG"
        ref = writer.store.put(canonical_bytes(raw))
        writer.register_object(ref)
        writer.conn.execute(
            "UPDATE source_records SET current_record_object_sha=? WHERE id=?",
            (ref.sha256, record["id"]),
        )
        writer.conn.commit()
        record["current_record_object_sha"] = ref.sha256
        receipt = _fetch_with_fake(monkeypatch, writer, record, load_fixture("contract_detail"))
        assert FakeLane.calls == [{
            "url": PREFIX + "econsign/contract-info/detail-by-id-for-po",
            "query": "token=", "body": {"id": ES_ID}, "min_interval": 3,
        }]
        assert receipt["endpoint"] == PREFIX + "econsign/contract-info/detail-by-id-for-po"
        assert receipt["expected_notice"] == "HD-0"
    finally:
        writer.close()
