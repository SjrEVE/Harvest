"""Deep-lane batch dispatch (DETAIL/HSMT/REOFFER) with an explicit live gate.

The parsing, history and staleness rules live in `collectors.deep` / `storage.deep` and are
tested there. What this module adds is the *job* shape: pick the records the request targets,
fetch one payload per record, parse it with a spec, and capture it with the version-change rule
applied first.

Live endpoints are BLOCKED by construction (§10.4, D-3): with no injected fetcher/spec the run
returns BLOCKED with a reason, not a fabricated payload. Offline runs inject a fake fetcher and a
declared spec — the same code path, no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from santhau_studio.collectors.deep import (
    DeepLaneSpec,
    DeepResponse,
    DetailShapeError,
    detail_spec,
    hsmt_spec,
    parse_detail,
    reoffer_spec,
)
from santhau_studio.storage.deep import (
    DeepCaptureResult,
    mark_stale_on_version_change,
    record_deep_capture,
)
from santhau_studio.storage.warehouse import WarehouseWriter

DeepFetcher = Callable[[str, str], dict | DeepResponse]  # (family, natural_key) -> response
SpecProvider = Callable[[str, str], DeepLaneSpec | None]  # (family, lane) -> spec
Clock = Callable[[], datetime]

LANE_KINDS = {"DETAIL": "DETAIL", "HSMT": "HSMT", "REOFFER": "REOFFER"}
DEFAULT_BATCH_LIMIT = 200

LANE_READINESS = {
    "DETAIL": "TESTED_OFFLINE",
    "HSMT": "TESTED_OFFLINE",
    "REOFFER": "TESTED_OFFLINE",
    # Item winning prices need POST contractor-input-result/get. This tree only has
    # listing fixtures that carry inputResultId, not a recorded response for that
    # endpoint, so the lane stays closed. Do not invent the call.
    "KQLCNT_FULL": "BLOCKED",
    "BOQ": "BLOCKED",
}
LIVE_BLOCK_REASON = "LIVE_DEEP_ENDPOINT_UNVERIFIED"


@dataclass
class DeepBatchResult:
    status: str
    detail: dict[str, Any]


def _default_spec(family: str, lane: str) -> DeepLaneSpec | None:
    """The shipped specs carry `paths=None` until a canary proves the shape (D-3)."""
    spec = {"DETAIL": detail_spec, "HSMT": hsmt_spec, "REOFFER": reoffer_spec}[lane](family)
    declared = bool(spec.members_path or spec.lots_path or spec.goods_path or spec.hsmt_columns)
    return spec if declared else None


def _targets(writer: WarehouseWriter, family: str, request_scope: dict, limit: int) -> list[dict]:
    explicit = request_scope.get("record_ids") if isinstance(request_scope, dict) else None
    if explicit:
        placeholders = ",".join("?" for _ in explicit)
        rows = writer.conn.execute(
            f"SELECT * FROM source_records WHERE family=? AND id IN ({placeholders})"
            f" ORDER BY id LIMIT {int(limit)}",
            (family, *[int(value) for value in explicit]),
        ).fetchall()
    else:
        rows = writer.conn.execute(
            "SELECT * FROM source_records WHERE family=? AND id>? ORDER BY id LIMIT ?",
            (family, int(request_scope.get("after_record_id", 0)), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def run_deep_batch(
    writer: WarehouseWriter,
    *,
    run_id: str,
    family: str,
    lane: str,
    scope: dict,
    fetcher: DeepFetcher | None,
    spec_provider: SpecProvider | None = None,
    clock: Clock | None = None,
    limit: int = DEFAULT_BATCH_LIMIT,
    stop_requested=lambda: False,
) -> DeepBatchResult:
    lane = LANE_KINDS.get(lane, lane)
    if lane not in LANE_KINDS:
        return DeepBatchResult("BLOCKED", {"reason": f"LANE_{lane}_NOT_DISPATCHABLE"})
    if fetcher is None:
        return DeepBatchResult("BLOCKED", {"reason": LIVE_BLOCK_REASON, "lane": lane})
    provided = (spec_provider or _default_spec)(family, lane)
    if provided is None:
        return DeepBatchResult("BLOCKED", {"reason": "DEEP_SPEC_UNVERIFIED", "lane": lane})
    if provided.parser_kind:
        from santhau_studio.workers.public_deep_run import collect_public
        return collect_public(writer, run_id, lane, scope, fetcher, provided,
                              _targets(writer, family, scope, min(limit, int(scope.get("limit", limit)))),
                              clock or (lambda: datetime.now(UTC)), stop_requested)
    now = (clock or (lambda: datetime.now(UTC)))()
    captured: list[DeepCaptureResult] = []
    stale_marked = 0
    failures: list[dict[str, str]] = []
    for record in _targets(writer, family, scope, limit):
        try:
            fetched = fetcher(family, str(record["natural_key"]))
            payload = fetched.response_payload if isinstance(fetched, DeepResponse) else fetched
            detail = fetched.detail if isinstance(fetched, DeepResponse) else fetched
            parsed = parse_detail(provided, detail)
        except (DetailShapeError, KeyError, TypeError, ValueError) as exc:
            failures.append({"record_id": str(record["id"]), "error": f"{type(exc).__name__}: {exc}"[:160]})
            continue
        observed = parsed.source_version_json
        stale_marked += mark_stale_on_version_change(
                writer.conn, source_record_id=int(record["id"]), observed_version_json=observed
            )
        completeness = "FULL" if not parsed.problems else "PARTIAL"
        result = record_deep_capture(
            writer,
            run_id=run_id,
            source_record_id=int(record["id"]),
            spec=provided,
            completeness=completeness,
            response_payload=payload,
            detail=detail,
            now=now,
            refresh_after_days=provided.refresh_after_days,
        )
        captured.append(result)
    counts = {"captured": len(captured), "stale_marked": stale_marked, "failed": len(failures), "lane": lane}
    if failures:
        counts["failures"] = failures[:5]
    if not captured and failures:
        return DeepBatchResult("FAILED", counts)
    if not captured:
        return DeepBatchResult("COMPLETED", {**counts, "note": "NO_TARGETS"})
    return DeepBatchResult("COMPLETED", counts)


def fake_fetcher(payloads: dict[str, dict]) -> DeepFetcher:
    """A fetcher backed by a dict, for tests and offline demos (never used in production)."""

    def fetch(_family: str, natural_key: str) -> dict:
        if natural_key not in payloads:
            raise KeyError(f"no synthetic payload for {natural_key}")
        return json.loads(json.dumps(payloads[natural_key]))

    return fetch
