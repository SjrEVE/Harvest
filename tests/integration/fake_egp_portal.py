"""Fake e-GP portal over an `httpx.MockTransport` — HTTP-shaped, policy-compliant.

The transport swap keeps everything above the socket real: the lane still checks HTTPS and the
host allowlist, still paces per host, still retries and classifies status codes, and the parser
still reads `page.content` / `page.totalElements`. Only the bytes come from this module.

Documents are shaped by `source_registry._FAMILY_SHAPE`, so `extract_page` sees exactly the
field names the projection expects. `YCBG` records omit `notifyVersion` on purpose: the source's
`or "00"` coercion would have invented a version, and the end-to-end test asserts MISSING instead.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

import httpx

from santhau_studio.collectors.http_client import FetchLane
from santhau_studio.collectors.network_policy import RetryPolicy
from santhau_studio.collectors.source_registry import SOURCE_EGP_HOME, SOURCES

PORTAL_URL = f"https://{SOURCES[SOURCE_EGP_HOME].host}{SOURCES[SOURCE_EGP_HOME].endpoint}"
ES_TYPE_TO_FAMILY = {
    "es-ct-contract": "HOP_DONG",
    "es-plan-project-p": "KHLCNT",
    "es-bidp-project-p": "DU_AN",
    "es-ycbg": "YCBG",
}


def documents(family: str, page_no: int, per_page: int) -> list[dict]:
    """Synthetic documents for one page of one family, keyed like the real portal's fields."""
    out: list[dict] = []
    for i in range(per_page):
        seq = page_no * per_page + i
        if family == "TBMT":
            out.append({
                "notifyNo": f"IB-TBMT-{seq}", "notifyNoStand": f"TBMT-{seq}", "notifyVersion": "00",
                "bidName": f"Gói thầu xây lắp số {seq}", "investorName": f"Ban QLDA {seq % 7}",
                "procuringEntityName": "Sở Xây dựng", "publicDate": "2026-11-01T03:00:00Z",
                "status": "Đã có KQ", "bidForm": "1", "bidMode": "1",
            })
        elif family == "KQLCNT":
            out.append({
                "notifyNo": f"IB-KQ-{seq}", "notifyVersion": "01", "inputResultId": f"R{seq}",
                "bidName": f"Kết quả gói {seq}", "investorName": f"Ban QLDA {seq % 5}",
                "winningContractorName": f"Công ty TNHH {seq}", "winningCode": f"MN-{seq}",
                "bidWinningPrice": 1_000_000_000 + seq, "numBidderJoin": 3,
                "publicDateKqlcnt": "2026-11-01T04:00:00Z", "planNo": f"PL-{seq}",
                "statusForNotify": "Đã duyệt",
            })
        elif family == "HOP_DONG":
            out.append({
                "contractCode": f"CT-{seq}", "contractVersion": "00", "contractNo": f"HĐ-{seq}",
                "notifyNo": f"IB-TBMT-{seq}", "contractSignDate": "2026-11-01T05:00:00Z",
                "ctPriceAfter": 900_000_000 + seq, "winningContractorName": f"Công ty {seq}",
                "winningCode": f"MN-{seq}", "isDomestic": True, "investorName": "Ban QLDA 1",
                "ctName": f"Hợp đồng số {seq}",
            })
        elif family == "KHLCNT":
            out.append({
                "planNo": f"PL-{seq}", "planVersion": "02", "name": f"Kế hoạch lựa chọn {seq}",
                "investorName": f"Ban QLDA {seq % 4}", "bidPrice": 5_000_000_000 + seq,
                "investTotal": 6_000_000_000 + seq, "decisionDate": "2026-10-30T00:00:00Z",
                "approvalStatus": "Đã duyệt", "planType": "1",
            })
        elif family == "DU_AN":
            version = "abc" if seq == 1 else "01"
            out.append({
                "pno": f"DA-{seq}", "pversion": version, "pname": f"Dự án đầu tư {seq}",
                "investorName": f"Ban QLDA {seq % 3}", "investTotal": 8_000_000_000 + seq,
                "decisionDate": "2026-10-29T00:00:00Z", "pgroup": "A", "location": "Hà Nội",
            })
        else:  # YCBG — no notifyVersion on purpose
            out.append({
                "notifyNo": f"IB-YC-{seq}", "bidName": f"Yêu cầu báo giá {seq}",
                "investorName": f"Ban QLDA {seq % 2}", "decisionDate": "2026-10-28T00:00:00Z",
                "bidOpenDate": "2026-11-02T00:00:00Z",
            })
    return out


def family_of(body: object) -> str:
    query = body[0]["query"][0] if isinstance(body, list) and body else {}
    filters = query.get("filters", [])
    es_type = next((f["fieldValues"][0] for f in filters if f.get("fieldName") == "type"), "")
    case_filter = next((f for f in filters if f.get("fieldName") == "caseKHKQ"), None)
    if es_type == "es-notify-contractor":
        # TBMT excludes caseKHKQ=1 (searchType not_in); KQLCNT selects it (searchType in).
        return "TBMT" if case_filter and case_filter.get("searchType") == "not_in" else "KQLCNT"
    return ES_TYPE_TO_FAMILY[es_type]


class FakePortal:
    """Answers the real endpoint URL; faults are scripted per family, consumed in order."""

    def __init__(
        self,
        *,
        per_page: int = 10,
        total_pages: int = 2,
        last_page_rows: int = 3,
        faults: dict[str, Iterable[str]] | None = None,
        drop_doc: tuple[str, int] | None = None,
    ) -> None:
        self.per_page = per_page
        self.total_pages = total_pages
        self.last_page_rows = last_page_rows
        self.faults = defaultdict(list, {family: list(script) for family, script in (faults or {}).items()})
        self.drop_doc = drop_doc
        self.calls: list[tuple[str, int]] = []
        self.total = (total_pages - 1) * per_page + last_page_rows

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        family = family_of(body)
        page_no = body[0]["pageNumber"]
        self.calls.append((family, page_no))
        script = self.faults[family]
        while script:
            action = script.pop(0)
            if action == "429":
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
            if action == "403":
                return httpx.Response(403, json={"error": "forbidden"})
            if action == "timeout":
                raise httpx.ReadTimeout("fake portal timeout")
            break
        rows = documents(family, page_no, self.per_page)
        if page_no == self.total_pages - 1:
            rows = rows[: self.last_page_rows]
        if self.drop_doc and self.drop_doc[0] == family and self.drop_doc[1] < len(rows):
            rows = rows[: self.drop_doc[1]] + rows[self.drop_doc[1] + 1 :]
        return httpx.Response(200, json={"page": {"content": rows, "totalElements": self.total}})

    def lane(self) -> FetchLane:
        client = httpx.Client(transport=self.transport())
        return FetchLane(
            url=PORTAL_URL,
            source_key=SOURCE_EGP_HOME,
            client=client,
            retry=RetryPolicy(max_attempts=3, backoff_seconds=(0.0, 0.0, 0.0)),
            sleeper=lambda _seconds: None,
            pacing_sleeper=lambda _seconds: None,
        )
