"""Bounded, session-free public deep fetcher used by the ordinary Windows worker."""

import json
from dataclasses import asdict
from typing import ClassVar

from santhau_studio.collectors.budget import RequestBudget
from santhau_studio.collectors.cgttrg import ENDPOINTS, PREFIX, public_spec
from santhau_studio.collectors.deep import DeepResponse, DetailShapeError
from santhau_studio.collectors.http_client import AccessBlocked, FetchLane
from santhau_studio.collectors.network_policy import RetryPolicy


class PublicDeepFetcher:
    blocked_endpoints: ClassVar[set[str]] = set()

    def __init__(self, writer, request, stop_requested=lambda: False):
        self.writer, self.request, self.stop_requested = writer, request, stop_requested
        limits = request.request_limits
        self.budget = RequestBudget(
            min(30, limits.get("max_requests", 30)), limits.get("max_seconds", 300)
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
        if (
            raw.get("bidForm") != "CGTTRG"
            or raw.get("notifyNo") != record["natural_key"]
            or raw.get("processApply") not in (None, "LDT")
        ):
            raise DetailShapeError("UNVERIFIED_PUBLIC_SCOPE")
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


def identity_problem(payload, record, lane, expected_id=None):
    """Never project an unrelated notice or different publication into this record."""
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
