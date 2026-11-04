"""Bounded source census; counts are source-reported, never collection proof."""
from datetime import datetime, timedelta

from santhau_studio.domain.coverage import Interval


def new_report(span, families):
    return {"schema": 1, "evidence_class": "SOURCE_COUNT_ONLY", "from": span.from_utc.isoformat(),
        "to": span.to_utc.isoformat(), "windows": [
            {"family": family, "from": span.from_utc.isoformat(), "to": span.to_utc.isoformat(),
             "state": "PENDING", "children": []} for family in families]}


def count_windows(report, fetch, *, max_windows, minimum=timedelta(milliseconds=1), checkpoint=None):
    """Breadth first across all families; restart visits pending nodes only."""
    attempted = 0
    for node in report["windows"]:
        if node["state"] != "PENDING":
            continue
        if attempted >= max_windows:
            break
        span = Interval(datetime.fromisoformat(node["from"]), datetime.fromisoformat(node["to"]))
        attempted += 1
        try:
            observation = fetch(node["family"], span)
        except Exception:
            # Caller records the scrubbed reason; do not discard the pending node on
            # a budget stop or CAPTCHA, so explicit later approval can resume it.
            summarize(report)
            if checkpoint:
                checkpoint(report)
            raise
        node.update(observation)
        total = node.get("total")
        if type(total) is int and 0 <= total < 10000 and node.get("trusted"):
            node["state"] = "MEASURED"
        elif type(total) is int and total >= 10000:
            units = span.duration // minimum
            if units < 2:
                node["state"] = "BLOCKED_PRECISION"
            else:
                middle = span.from_utc + minimum * (units // 2)
                node["state"] = "SPLIT"
                for start, end in ((span.from_utc, middle), (middle, span.to_utc)):
                    node["children"].append(len(report["windows"]))
                    report["windows"].append({"family": node["family"], "from": start.isoformat(),
                        "to": end.isoformat(), "state": "PENDING", "children": []})
        else:
            node["state"] = "UNVERIFIED_TOTAL"
        summarize(report)
        if checkpoint:
            checkpoint(report)
    summarize(report)
    return report


def summarize(report):
    nodes = report["windows"]
    values = {}
    for index in range(len(nodes) - 1, -1, -1):
        node = nodes[index]
        children = node["children"]
        if node["state"] == "MEASURED":
            values[index] = (node["total"], node["total"])
        elif children:
            counts = [values[c] for c in children]
            known = sum(n for n, _ in counts) if all(n is not None for n, _ in counts) else None
            values[index] = (known, known if known is not None else max(node.get("total") or 0, sum(n for _, n in counts)))
        else:
            values[index] = (None, node.get("total") if type(node.get("total")) is int and node["total"] >= 10000 else 0)
    children = {child for node in nodes for child in node["children"]}
    report["families"] = [{"family": n["family"], "total": values[i][0], "lower_bound": values[i][1],
        "status": "SOURCE_REPORTED" if values[i][0] is not None else "PARTIAL"}
        for i, n in enumerate(nodes) if i not in children]
    report["pending_windows"] = sum(n["state"] == "PENDING" for n in nodes)
