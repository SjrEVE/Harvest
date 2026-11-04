"""Historical coverage, using the same proof resolver as collection."""
from contextlib import closing
from datetime import datetime, timedelta

from santhau_studio.collectors.source_registry import FAMILY_ORDER
from santhau_studio.desktop.collection_dates import VN, collection_scope
from santhau_studio.desktop.collection_totals import load_counts, load_totals, summarize
from santhau_studio.domain.coverage import Interval, resolve_coverage
from santhau_studio.storage.coverage import load_rows


def coverage_range(backend, payload):
    try:
        scope = collection_scope(payload, "BACKFILL")
        first, last = (datetime.fromisoformat(scope[k]).astimezone(VN) for k in ("from","to"))
        daily = payload.get("resolution") == "DAY"
        weekly = payload.get("resolution") == "WEEK"
        if last-first > timedelta(days=124 if daily else 366*10):
            raise ValueError("Chọn tối đa 4 tháng để xem từng ngày, hoặc tối đa 10 năm để xem theo tháng.")
        periods = []
        start = first
        while start < last:
            end = start+timedelta(days=1 if daily else 7) if daily or weekly else (
                start.replace(year=start.year+1,month=1,day=1) if start.month==12 else start.replace(month=start.month+1,day=1))
            end = min(end,last)
            periods.append({"label":start.strftime("%d/%m/%Y" if daily or weekly else "%m/%Y"),
                "partial_period": (end-start).days < 7 if weekly else not daily and (start.day != 1 or end.day != 1),
                "date_from":start.strftime("%d/%m/%Y"),"date_to":(end-timedelta(days=1)).strftime("%d/%m/%Y"),
                "families":[], "_window":Interval(start,end)})
            start = end
        conn = backend._serving()
        if conn is None:
            return {"ok":False,"periods":[],"error":"Kho chưa có bản dữ liệu hiển thị."}
        with closing(conn):
            counts = load_counts(conn, first, last)
            meta = conn.execute("SELECT snapshot_built_at_utc FROM serving_meta ORDER BY id DESC LIMIT 1").fetchone()
            for family in FAMILY_ORDER:
                rows = load_rows(conn,"egp_home",family,Interval(first,last))
                totals = load_totals(conn, family, rows)
                for period in periods:
                    resolved = resolve_coverage(source_key="egp_home",family=family,requested=period["_window"],rows=rows)
                    period["families"].append({"family":family,"status":resolved.status,
                        "proof_ids":list(resolved.proof_ids),"reason":"; ".join(resolved.reasons),
                        **summarize(family,period["_window"],rows,counts,totals)})
        for period in periods:
            period.pop("_window")
        return {"ok":True,"synthetic":backend.workspace_identity()["synthetic"],"periods":list(reversed(periods)),
                "snapshot_at":meta[0] if meta else None,"counts_available":counts is not None}
    except (ValueError,KeyError) as exc:
        return {"ok":False,"periods":[],"error":str(exc)}
