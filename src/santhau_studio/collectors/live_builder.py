"""Shared live page source, without any desktop or OS-specific process imports."""

import json

from santhau_studio.collectors.budget import RequestBudget
from santhau_studio.collectors.lane_source import egp_page_source
from santhau_studio.collectors.source_registry import SOURCES


def live_builder(writer, request):
    from santhau_studio.collectors.http_client import FetchLane

    evidence = writer.conn.execute(
        "SELECT value_json FROM settings WHERE key='evidence_class'"
    ).fetchone()
    if evidence and json.loads(evidence[0]).get("class") == "RC3_SYNTHETIC_OFFLINE":
        return None
    source = request.sources[0] if request.sources else None
    if not isinstance(source, dict) or "family" not in source:
        return None
    limits = request.request_limits
    budget = RequestBudget(limits.get("max_requests", 30000), limits.get("max_seconds", 3600))
    key = source["source_key"]
    return egp_page_source(
        family=str(source["family"]),
        pacing_seconds=limits.get("pacing_seconds", 3),
        budget=budget,
        lane_factory=lambda: FetchLane(
            url=f"https://{SOURCES[key].host}{SOURCES[key].endpoint}", source_key=key, budget=budget
        ),
    )
