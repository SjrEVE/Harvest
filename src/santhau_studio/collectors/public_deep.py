"""Bounded, session-free public deep fetcher used by the ordinary Windows worker."""

import json
from dataclasses import asdict
from typing import ClassVar

from santhau_studio.collectors.budget import RequestBudget
from santhau_studio.collectors.cgttrg import ENDPOINTS, PREFIX, public_spec
from santhau_studio.collectors.deep import DeepResponse, DetailShapeError
from santhau_studio.collectors.http_client import AccessBlocked, FetchLane
from santhau_studio.collectors.network_policy import RetryPolicy

ADAPTER_VERSION = 2


class UnsupportedAdapter(DetailShapeError):
    def __init__(self, reason, capability="UNSUPPORTED_ADAPTER"):
        super().__init__(reason)
        self.capability = capability


class PublicDeepFetcher:
    blocked_endpoints: ClassVar[set[str]] = set()

    def __init__(self, writer, request, stop_requested=lambda: False):
        self.writer, self.request, self.stop_requested = writer, request, stop_requested
        limits = request.request_limits
        self.budget = RequestBudget(
            min(30, limits.get("max_requests", 30)), limits.get("max_seconds", 300), stop_requested=stop_requested,
        )
        self.pacing = max(3, limits.get("pacing_seconds", 3))
        self.retry = RetryPolicy(max_attempts=limits.get("max_attempts", 4))
        self.connect_timeout_seconds = limits.get("connect_timeout_seconds", 15)
        self.receipt = {}

    def fetch_record(self, record):
        self.receipt = {}
        if self.stop_requested():
            raise DetailShapeError("STOP_REQUESTED")
        if public_spec(record["family"], self.request.job_kind) is None:
            raise DetailShapeError("UNVERIFIED_PUBLIC_SCOPE")
        raw = json.loads(self.writer.store.read(record["current_record_object_sha"]))
        self.receipt = {"source_record_id": record["id"], "listing_object_sha": record["current_record_object_sha"],
                        "processApply": raw.get("processApply"), "adapter_version": ADAPTER_VERSION, "attempts": []}
        if self.request.job_kind == "M1":
            return self._fetch_m1(record, raw)
        if raw.get("bidForm") != "CGTTRG":
            raise UnsupportedAdapter("UNSUPPORTED_BID_FORM:" + str(raw.get("bidForm")), "UNSUPPORTED_BID_FORM")
        if raw.get("notifyNo") != record["natural_key"]:
            raise UnsupportedAdapter("LISTING_IDENTITY_MISMATCH", "UNVERIFIED_LISTING")
        if raw.get("processApply") not in (None, "LDT"):
            raise UnsupportedAdapter("UNSUPPORTED_PROCESS_APPLY:" + str(raw.get("processApply")), "UNSUPPORTED_PROCESS_APPLY")
        notify_id = raw.get("notifyId") or raw.get("id")
        if not isinstance(notify_id, str) or not notify_id:
            raise DetailShapeError("NOTIFY_ID_MISSING")
        endpoint = PREFIX + ENDPOINTS[self.request.job_kind]
        if endpoint in self.blocked_endpoints:
            raise DetailShapeError("ENDPOINT_BLOCKED_THIS_WORKER")
        body = {"id": notify_id}
        if self.request.job_kind == "HSMT":
            body["processApply"] = "LDT"
        self.receipt = {
            "endpoint": endpoint,
            "request": body,
            "source_record_id": record["id"],
            "expected_notice": record["natural_key"],
            "expected_version": raw.get("notifyVersion"),
        }
        with FetchLane(
            url=endpoint, source_key="egp_public_cgttrg", budget=self.budget, retry=self.retry,
            connect_timeout_seconds=self.connect_timeout_seconds,
        ) as lane:
            try:
                result = lane.post(body, min_interval=self.pacing)
            except AccessBlocked:
                self.blocked_endpoints.add(endpoint)
                raise
        self.receipt.update(
            http_status=result.http_status,
            attempts=[asdict(a) for a in result.attempts],
            requests_used=self.budget.used,
        )
        return DeepResponse(result.payload, result.payload)

    def _fetch_m1(self, record, raw):
        """M1: one KQLCNT result's per-item winning prices, by inputResultId.

        The portal's `expose/` endpoint returns HTTP 400 unless a `token=`
        query parameter is present; the server does not validate its value
        (canary 2026-11-13: dummy and empty values both return HTTP 200 with
        identical payloads), so the lane sends the parameter with an empty
        value — no captcha is solved and no credential material travels.
        KQLCNT listings carry no bidForm/processApply, so none of the CGTTRG
        gates apply here.
        """
        if raw.get("notifyNo") != record["natural_key"]:
            raise UnsupportedAdapter("LISTING_IDENTITY_MISMATCH", "UNVERIFIED_LISTING")
        input_result_id = raw.get("inputResultId")
        if not isinstance(input_result_id, str) or not input_result_id:
            raise UnsupportedAdapter("INPUT_RESULT_ID_MISSING", "UNSUPPORTED_INPUT_RESULT_ID")
        endpoint = PREFIX + ENDPOINTS["M1"]
        if endpoint in self.blocked_endpoints:
            raise DetailShapeError("ENDPOINT_BLOCKED_THIS_WORKER")
        body = {"id": input_result_id}
        self.receipt = {
            "endpoint": endpoint,
            "request": body,
            "source_record_id": record["id"],
            "expected_notice": record["natural_key"],
            "expected_version": raw.get("notifyVersion"),
        }
        with FetchLane(
            url=endpoint, source_key="egp_public_cgttrg", budget=self.budget, retry=self.retry,
            connect_timeout_seconds=self.connect_timeout_seconds, query="token=",
        ) as lane:
            try:
                result = lane.post(body, min_interval=self.pacing)
            except AccessBlocked:
                self.blocked_endpoints.add(endpoint)
                raise
        self.receipt.update(
            http_status=result.http_status,
            attempts=[asdict(a) for a in result.attempts],
            requests_used=self.budget.used,
        )
        return DeepResponse(result.payload, result.payload)


def identity_problem(payload, record, lane, expected_id=None):
    """Never project an unrelated notice or different publication into this record."""
    if lane == "M1":
        # M1 payloads nest the result under bideContractorInputResultDTO;
        # expected_id here is the inputResultId the request carried.
        dto = payload.get("bideContractorInputResultDTO") if isinstance(payload, dict) else None
        if not isinstance(dto, dict):
            return "IDENTITY_SHAPE_INVALID"
        if dto.get("notifyNo") != record["natural_key"]:
            return "NOTICE_IDENTITY_MISMATCH"
        if expected_id and dto.get("id") not in (None, expected_id):
            return "NOTICE_UUID_MISMATCH"
        actual, expected = dto.get("notifyVersion"), record.get("version_raw")
        if (
            actual is not None
            and expected is not None
            and str(actual).lstrip("0") != str(expected).lstrip("0")
        ):
            return "NOTICE_VERSION_MISMATCH"
        return None
    values = (
        payload.get("bidoInvBiddingDTO")
        if lane == "HSMT" and isinstance(payload, dict)
        else [payload]
    )
    if values is None:
        return None
    if not isinstance(values, list):
        return "IDENTITY_SHAPE_INVALID"
    for value in values:
        if not isinstance(value, dict) or value.get("notifyNo") != record["natural_key"]:
            return "NOTICE_IDENTITY_MISMATCH"
        if expected_id and value.get("notifyId") not in (None, expected_id):
            return "NOTICE_UUID_MISMATCH"
        actual, expected = value.get("notifyVersion"), record.get("version_raw")
        if (
            actual is not None
            and expected is not None
            and str(actual).lstrip("0") != str(expected).lstrip("0")
        ):
            return "NOTICE_VERSION_MISMATCH"
    return None
