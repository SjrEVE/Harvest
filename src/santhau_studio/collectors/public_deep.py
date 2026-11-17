"""Bounded, session-free public deep fetcher used by the ordinary Windows worker."""

import json
from dataclasses import asdict
from typing import ClassVar

from santhau_studio.collectors.budget import RequestBudget
from santhau_studio.collectors.cgttrg import (
    _DETAIL_KEY_FIELD,
    _DETAIL_LISTING_FIELD,
    _DETAIL_VERSION_FIELD,
    DETAIL_ENDPOINTS,
    ENDPOINTS,
    PREFIX,
    public_spec,
)
from santhau_studio.collectors.deep import DeepResponse, DetailShapeError
from santhau_studio.collectors.http_client import AccessBlocked, FetchLane
from santhau_studio.collectors.network_policy import RetryPolicy

# RC3-12: the public detail lanes release previously-UNSUPPORTED tasks for
# retry under the new lanes (e.g. a non-CGTTRG TBMT DETAIL was
# UNSUPPORTED_BID_FORM under RC3-11.2), so the adapter version bumps.
ADAPTER_VERSION = 3


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
        if (record["family"], self.request.job_kind) in DETAIL_ENDPOINTS and (
            record["family"] != "TBMT" or raw.get("bidForm") != "CGTTRG"
        ):
            # RC3-12 public detail lanes: a CGTTRG *TBMT package* keeps the
            # legacy path below (its own endpoints and parsers); every other
            # form goes through the generic detail endpoints. The carve-out
            # is TBMT-only: other families inherit the package's bidForm on
            # their rows (a HOP_DONG awarded from a CGTTRG package carries
            # bidForm CGTTRG — canary #2, 17/11), and routing those to the
            # TBMT legacy path below fails them with LISTING_IDENTITY_MISMATCH
            # (they have no notifyNo). For non-TBMT families the public
            # DETAIL lane runs whatever the bidForm is.
            return self._fetch_public_detail(record, raw)
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

    def _fetch_public_detail(self, record, raw):
        """RC3-12: one public detail fetch for a non-CGTTRG record.

        Same pattern as _fetch_m1: POST a JSON body to the portal's
        contractor-selection service, no session, 3 s pacing. Endpoints that
        carry `?token=` take it empty (the portal checks its presence, not
        its value — canaried for M1 on 2026-11-13 and for these lanes on
        2026-11-17); YCBG and eShop take no token at all. The request id is
        the listing row's own id (verified per family on 2026-11-17: the
        detail payload's `id` equals the ES list-row id for all six detail
        families), except eShop, which is keyed by bidNo, and TBMT, which
        uses notifyId-or-id like the legacy path.
        """
        family, lane = record["family"], self.request.job_kind
        path, query = DETAIL_ENDPOINTS[(family, lane)]
        listing_field = _DETAIL_LISTING_FIELD[family]
        if raw.get(listing_field) != record["natural_key"]:
            raise UnsupportedAdapter("LISTING_IDENTITY_MISMATCH", "UNVERIFIED_LISTING")
        if lane == "HSMT":
            # TBMT HSMT only reaches here for non-CGTTRG forms; processApply
            # comes from the listing row and is required by the endpoint
            # (missing it returns HTTP 500). The gate mirrors the legacy
            # path: anything other than absent/LDT is unsupported, not
            # silently coerced.
            process_apply = raw.get("processApply")
            if process_apply not in (None, "LDT"):
                raise UnsupportedAdapter(
                    "UNSUPPORTED_PROCESS_APPLY:" + str(process_apply),
                    "UNSUPPORTED_PROCESS_APPLY",
                )
            notify_id = raw.get("notifyId") or raw.get("id")
            if not isinstance(notify_id, str) or not notify_id:
                raise DetailShapeError("NOTIFY_ID_MISSING")
            body = {"id": notify_id, "processApply": process_apply or "LDT"}
        elif family == "TBMT":
            # The TBMT detail endpoint is the LDT selection endpoint; a
            # package under another process (e.g. KHAC) has no verified
            # detail endpoint, so it is unsupported, not silently fetched.
            # The gate mirrors the legacy path and the HSMT branch above.
            process_apply = raw.get("processApply")
            if process_apply not in (None, "LDT"):
                raise UnsupportedAdapter(
                    "UNSUPPORTED_PROCESS_APPLY:" + str(process_apply),
                    "UNSUPPORTED_PROCESS_APPLY",
                )
            notify_id = raw.get("notifyId") or raw.get("id")
            if not isinstance(notify_id, str) or not notify_id:
                raise DetailShapeError("NOTIFY_ID_MISSING")
            body = {"id": notify_id}
        elif family == "ESHOP":
            body = {"bidNo": record["natural_key"]}
        else:
            record_id = raw.get("id")
            if not isinstance(record_id, str) or not record_id:
                raise DetailShapeError("RECORD_ID_MISSING")
            body = {"id": record_id}
        endpoint = PREFIX + path
        if endpoint in self.blocked_endpoints:
            raise DetailShapeError("ENDPOINT_BLOCKED_THIS_WORKER")
        expected_version = raw.get(_DETAIL_VERSION_FIELD[family])
        if isinstance(expected_version, list) and len(expected_version) == 1:
            # The portal returns some scalars as singleton arrays (the
            # ESHOP list row's orderNo is ['OR…']); unwrap it so the receipt
            # carries the same value the identity check compares.
            expected_version = expected_version[0]
        self.receipt = {
            "endpoint": endpoint,
            "request": body,
            "source_record_id": record["id"],
            "expected_notice": record["natural_key"],
            "expected_version": expected_version,
        }
        with FetchLane(
            url=endpoint, source_key="egp_public_cgttrg", budget=self.budget, retry=self.retry,
            connect_timeout_seconds=self.connect_timeout_seconds, query=query,
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
    if lane == "DETAIL":
        family = record["family"]
        if family == "TBMT" and isinstance(payload, dict) and isinstance(payload.get("bidoNotifyContractorM"), dict):
            # Generic-endpoint TBMT detail: the envelope carries the identity.
            return _detail_identity(
                payload["bidoNotifyContractorM"], record, expected_id,
                "notifyNo", "notifyVersion",
            )
        if family != "TBMT":
            # The other families' detail envelopes; a missing envelope is a
            # shape failure, not a silent pass.
            value = _detail_envelope(payload, family)
            if value is None:
                return "IDENTITY_SHAPE_INVALID"
            return _detail_identity(
                value, record, expected_id,
                _DETAIL_KEY_FIELD[family], _DETAIL_VERSION_FIELD[family],
            )
        # else: CGTTRG TBMT DETAIL (reoffer shape) — falls through to the
        # generic path below, which checks the top-level notifyNo.
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


def _detail_envelope(payload, family):
    """The dict carrying a new detail lane's identity fields, or None."""
    if not isinstance(payload, dict):
        return None
    if family == "YCBG":
        envelope = payload.get("bidoRequestQuote")
    elif family == "KHLCNT":
        envelope = payload.get("bidPoBidpPlanProjectDetailView")
    elif family == "HOP_DONG":
        envelope = payload.get("contract")
    else:  # DU_AN and ESHOP carry their identity at the top level.
        envelope = payload
    return envelope if isinstance(envelope, dict) else None


def _detail_identity(value, record, expected_id, key_field, version_field):
    """One envelope's identity: natural key, request id, version.

    Mirrors the M1 branch's tolerances: the id check passes when the
    envelope carries no id, and the version check is skipped when either
    side has none. The version comparison strips leading zeros, so "00"
    and "0" are the same publication.
    """
    if value.get(key_field) != record["natural_key"]:
        return "NOTICE_IDENTITY_MISMATCH"
    if expected_id and value.get("id") not in (None, expected_id):
        return "NOTICE_UUID_MISMATCH"
    actual, expected = value.get(version_field), record.get("version_raw")
    if (
        actual is not None
        and expected is not None
        and str(actual).lstrip("0") != str(expected).lstrip("0")
    ):
        return "NOTICE_VERSION_MISMATCH"
    return None
