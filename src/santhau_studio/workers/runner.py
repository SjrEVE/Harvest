"""Run modes BACKFILL / INCREMENTAL / RECONCILE with adaptive split and proof gating (§4.3, §4.4).

Two rules dominate this module:

* **skip only on the resolver.** Whether a range may be skipped comes from
  `storage.coverage.resolve`, never from a JOIN written here (§4.5).
* **absence of evidence is not evidence of completion.** A run that hits a cap, sees the source's
  total move, or cannot produce terminal evidence records PARTIAL. The frozen DDL forbids a
  COMPLETE interval without a proof row, so nothing here attempts to write one.

Fetching is injected (`page_source`), so the same code path is exercised offline against a
recorded portal shape and, later, against the live lane. Every attempt the lane made is written
as a `fetch_attempts` row: a retry must be visible, and `unrecovered_request_count` is computed
from those rows rather than asserted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from santhau_studio.collectors.boundary import (
    BoundaryRule,
    source_window_for,
    split_for_granularity,
)
from santhau_studio.collectors.egp_home import ES_MAX_RESULT_WINDOW, Page
from santhau_studio.collectors.extraction import extract_page, verify_extraction
from santhau_studio.collectors.http_client import AccessBlocked, Attempt, FetchFailed
from santhau_studio.collectors.source_registry import binding_for
from santhau_studio.domain.coverage import Interval, vn_calendar_day
from santhau_studio.storage import coverage
from santhau_studio.storage.coverage import ProofRecord
from santhau_studio.storage.warehouse import WarehouseWriter

ORDER_NEWEST_FIRST = "NEWEST_FIRST"
ORDER_OLDEST_FIRST = "OLDEST_FIRST"
INCREMENTAL_OVERLAP_DAYS = 2  # §4.4: the current day plus two days of overlap
ES_TRUNCATION_TOTAL = ES_MAX_RESULT_WINDOW
VN_TZ_NAME = "Asia/Ho_Chi_Minh"

PageSource = Callable[[Interval, str, "SearchWindow"], Iterator[Page]]
StopCheck = Callable[[], bool]


@dataclass
class IntervalOutcome:
    """What one interval attempt produced, and therefore what it can prove."""

    interval: Interval
    granularity: str
    status: str
    pages: int = 0
    requests: int = 0
    attempts: int = 0
    attempt_errors: int = 0
    unrecovered_requests: int = 0
    rows: int = 0
    records_new: int = 0
    records_updated: int = 0
    records_raw_only: int = 0
    records_unresolved: int = 0
    malformed: int = 0
    identity_keys: set[str] = field(default_factory=set)
    total_first: int | None = None
    total_last: int | None = None
    total_trusted: bool = False
    max_pages_hit: int = 0
    max_records_hit: int = 0
    terminal_evidence: dict[str, Any] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    children: tuple[IntervalOutcome, ...] = ()
    interval_id: int | None = None

    @property
    def distinct_ids(self) -> int:
        return len(self.identity_keys)

    @property
    def ids_union_hash(self) -> str:
        return hashlib.sha256(",".join(sorted(self.identity_keys)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RunLimits:
    max_pages: int
    max_records: int
    page_size: int

    @classmethod
    def for_family(cls, family: str) -> RunLimits:
        binding = binding_for(family)
        return cls(max_pages=binding.max_pages, max_records=binding.max_records, page_size=binding.page_size)


@dataclass(frozen=True)
class SearchWindow:
    """The source-side date filter, sent only when the boundary rule is verified (§4.1 item 1)."""

    from_value: str
    to_value: str


class Runner:
    """Fetches intervals and records coverage for one source+family."""

    def __init__(
        self,
        *,
        writer: WarehouseWriter,
        source_key: str,
        family: str,
        endpoint: str,
        adapter_sha: str,
        page_source: PageSource,
        rule_for: Callable[[str], BoundaryRule | None] | None = None,
        limits: RunLimits | None = None,
        clock: Callable[[], datetime] | None = None,
        stop_requested: StopCheck | None = None,
        max_split_granularity: str = "MINUTE",
        progress=None,
    ) -> None:
        self.writer = writer
        self.conn = writer.conn
        self.source_key = source_key
        self.family = family
        self.endpoint = endpoint
        self.adapter_sha = adapter_sha
        self.page_source = page_source
        self.limits = limits or RunLimits.for_family(family)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.stop_requested = stop_requested or (lambda: False)
        self.max_split_granularity = max_split_granularity
        self._rule_for = rule_for or self._rule_from_db
        self._rule_cache: dict[str, BoundaryRule | None] = {}
        self.reconcile_outcomes: list[IntervalOutcome] = []
        self.progress = progress

    # ------------------------------------------------------------------ boundary rules

    def _rule_from_db(self, granularity: str) -> BoundaryRule | None:
        row = coverage.boundary_rule(
            self.conn, source_key=self.source_key, family=self.family,
            endpoint=self.endpoint, adapter_sha=self.adapter_sha,
        )
        if row is None:
            return None
        rule = BoundaryRule.from_row(row)
        if coverage.granularity_rank(rule.precision) < coverage.granularity_rank(granularity):
            # A rule verified only to DAY precision cannot certify an HOUR window: the source may
            # ignore anything finer. Claiming otherwise is the silent edge bug §4.2 warns about.
            return None
        return rule

    def boundary_rule(self, granularity: str) -> BoundaryRule | None:
        if granularity not in self._rule_cache:
            self._rule_cache[granularity] = self._rule_for(granularity)
        return self._rule_cache[granularity]

    def window_for(self, interval: Interval, granularity: str) -> SearchWindow | None:
        rule = self.boundary_rule(granularity)
        if rule is None:
            return None
        from_value, to_value = source_window_for(rule, interval)
        return SearchWindow(from_value=from_value, to_value=to_value)

    # ------------------------------------------------------------------ one interval

    def fetch_interval(self, interval: Interval, granularity: str, run_id: str) -> IntervalOutcome:
        """Walk every page of one interval, writing each attempt and committing each page."""
        outcome = IntervalOutcome(interval=interval, granularity=granularity, status="FETCHED")
        binding = binding_for(self.family)
        window = self.window_for(interval, granularity)
        if window is None:
            # §4.2 fail-closed: without a verified rule we cannot even name the filter, so
            # fetching would mean an unbounded query. Refuse before the first request.
            outcome.status = "PARTIAL"
            outcome.blockers = ("BOUNDARY_RULE_UNVERIFIED",)
            return outcome
        try:
            pages = self.page_source(interval, granularity, window)
        except (AccessBlocked, FetchFailed) as exc:
            return self._refusal(outcome, exc, run_id, 0)
        seen_records = 0
        page_no = 0
        while True:
            if self.stop_requested():
                outcome.status = "INTERRUPTED"
                outcome.blockers = ("STOP_REQUESTED",)
                return outcome
            if page_no >= self.limits.max_pages:
                outcome.max_pages_hit = page_no
                outcome.status = "PARTIAL"
                outcome.blockers = (f"MAX_PAGES_HIT={page_no}",)
                return outcome
            try:
                page = next(pages)
            except StopIteration:
                # The source ran out of pages without a terminal signal: that is not proof.
                outcome.status = "PARTIAL"
                outcome.blockers = ("NO_TERMINAL_EVIDENCE",)
                return outcome
            except (AccessBlocked, FetchFailed) as exc:
                return self._refusal(outcome, exc, run_id, page_no)

            counted = bool(page.attempt_log) or page.payload is not None
            outcome.requests += 1 if counted else 0
            outcome.attempts += max(page.attempts, 1) if counted else 0
            outcome.attempt_errors += page.failed_attempts
            report = extract_page(page.payload, binding.spec) if page.payload is not None else None
            response_ref = self.writer.store_response(page.payload) if page.payload is not None else None
            attempt_id = (
                self._write_attempts(page, interval, run_id, response_ref)
                if counted and response_ref is not None
                else None
            )
            if page.total_hint is not None:
                if outcome.total_first is None:
                    outcome.total_first = page.total_hint
                outcome.total_last = page.total_hint
                if page.total_hint_trusted:
                    outcome.total_trusted = True
            if page.payload is None or report is None:
                outcome.status = "PARTIAL"
                outcome.blockers = (f"MAX_PAGES_HIT={page.page_no}",)
                outcome.max_pages_hit = page.page_no
                return outcome

            verify_extraction(page.payload, report)
            committed = self.writer.commit_page(
                run_id=run_id, attempt_id=attempt_id, response_ref=response_ref, records=report.records
            )
            outcome.pages += 1
            outcome.rows += len(report.records)
            outcome.records_new += committed.records_new
            outcome.records_updated += committed.records_updated
            outcome.records_raw_only += committed.records_raw_only
            outcome.records_unresolved += committed.records_unresolved
            outcome.malformed += report.malformed_members
            outcome.identity_keys.update(_identity_keys(self.family, report.records))
            seen_records += len(report.records)
            if self.progress:
                self.progress({"family":self.family,"from":_iso(interval.from_utc),"to":_iso(interval.to_utc),
                    "granularity":granularity,"pages":outcome.pages,"rows":outcome.rows,"requests":outcome.requests,
                    "total_hint":outcome.total_last,"state":"COLLECTING"})
            if seen_records >= self.limits.max_records:
                outcome.max_records_hit = seen_records
                outcome.status = "PARTIAL"
                outcome.blockers = (f"MAX_RECORDS_HIT={seen_records}",)
                return outcome
            if not page.has_more:
                outcome.terminal_evidence = {
                    "kind": "EMPTY_PAGE" if page.rows == 0 else "SHORT_PAGE",
                    "page_no": page.page_no,
                    "rows": page.rows,
                    "page_size": self.limits.page_size,
                }
                break
            page_no += 1

        if outcome.total_first is not None and outcome.total_last is not None and outcome.total_first != outcome.total_last:
            outcome.status = "PARTIAL"
            outcome.blockers = (f"TOTAL_CHANGED {outcome.total_first}->{outcome.total_last}",)
        return outcome

    def _refusal(self, outcome: IntervalOutcome, exc: Exception, run_id: str, page_no: int) -> IntervalOutcome:
        """The lane gave up: an access refusal blocks the interval, a dead request stays partial."""
        outcome.requests += 1
        outcome.unrecovered_requests += 1
        attempts = getattr(exc, "attempt_log", ())
        outcome.attempts += len(attempts)
        outcome.attempt_errors += len(attempts)
        for attempt in attempts:
            self.writer.ensure_attempt(run_id=run_id, family=self.family, endpoint=self.endpoint,
                interval_from=_iso(outcome.interval.from_utc), interval_to=_iso(outcome.interval.to_utc),
                page_no=page_no, attempt_no=attempt.attempt_no, outcome=_ddl_outcome(attempt),
                http_status=attempt.http_status, error_kind=attempt.error_kind, response=None)

        if isinstance(exc, AccessBlocked):
            outcome.status = "BLOCKED"
            outcome.blockers = (f"ACCESS_BLOCKED:{type(exc).__name__}",)
        else:
            outcome.status = "PARTIAL"
            outcome.blockers = (f"REQUEST_UNRECOVERED:{_reason(exc)}",)
        return outcome

    def _write_attempts(self, page: Page, interval: Interval, run_id: str, response_ref) -> int:
        """Record every try the lane made, and return the id of the try that carried the body.

        A retry is a new attempt on the same logical request (§3.3), so the failed tries are
        written first with no response object. The attempt that actually returned bytes is the
        one the page commit hangs from, and it is the only one that gets the sha.
        """
        common = {
            "run_id": run_id, "family": self.family, "endpoint": self.endpoint,
            "interval_from": _iso(interval.from_utc), "interval_to": _iso(interval.to_utc),
            "page_no": page.page_no,
        }
        attempt_id = None
        for attempt in page.attempt_log:
            succeeded = attempt.outcome == "OK"
            recorded = self.writer.ensure_attempt(
                **common,
                attempt_no=attempt.attempt_no,
                outcome=_ddl_outcome(attempt),
                response=response_ref if succeeded else None,
                http_status=attempt.http_status,
                error_kind=attempt.error_kind,
            )
            if succeeded:
                attempt_id = recorded
        if attempt_id is None:
            attempt_id = self.writer.ensure_attempt(
                **common, attempt_no=max(page.attempts, 1), outcome="OK",
                response=response_ref, http_status=page.http_status,
            )
        return int(attempt_id)

    # ------------------------------------------------------------------ split + record

    def run_interval(self, interval: Interval, granularity: str, run_id: str) -> IntervalOutcome:
        """Fetch, split when the source truncated or a cap was hit, then record leaf rows."""
        outcome = self.fetch_interval(interval, granularity, run_id)
        truncated = outcome.total_first is not None and outcome.total_first >= ES_TRUNCATION_TOTAL
        capped = bool(outcome.max_pages_hit or outcome.max_records_hit)
        finer = coverage.finer_granularity(granularity)
        splittable = (
            (truncated or capped)
            and finer is not None
            and coverage.granularity_rank(finer) <= coverage.granularity_rank(self.max_split_granularity)
            and interval.duration > _granularity_step(finer)
        )
        if splittable:
            outcome.children = tuple(
                self.run_interval(child, finer, run_id) for child in split_for_granularity(interval, finer)
            )
            for name in ("requests", "attempts", "attempt_errors", "unrecovered_requests", "rows",
                         "records_new", "records_updated", "records_raw_only", "records_unresolved", "malformed"):
                setattr(outcome, name, sum(getattr(child, name) for child in outcome.children))
            outcome.identity_keys = set().union(*(child.identity_keys for child in outcome.children))
            all_complete = bool(outcome.children) and all(child.status == "COMPLETE" for child in outcome.children)
            outcome.status = "COMPLETE" if all_complete else "PARTIAL"
            outcome.blockers = () if all_complete else (outcome.blockers or ("SPLIT_INCOMPLETE",))
            return outcome
        self._record(outcome, run_id)
        return outcome

    def _record(self, outcome: IntervalOutcome, run_id: str) -> None:
        rule = self.boundary_rule(outcome.granularity)
        blockers: list[str] = list(outcome.blockers)
        if rule is None:
            blockers.append("BOUNDARY_RULE_UNVERIFIED")
        if not outcome.total_trusted:
            blockers.append("TOTAL_UNTRUSTED")
        proof: ProofRecord | None = None
        if not blockers:
            proof = ProofRecord(
                proof_version=1,
                source_key=self.source_key,
                family=self.family,
                interval=outcome.interval,
                granularity=outcome.granularity,
                boundary_rule_id=rule.rule_id,
                source_query_fingerprint=_query_fingerprint(self.source_key, self.family, self.endpoint, outcome.interval),
                adapter_sha=self.adapter_sha,
                request_count=outcome.requests,
                attempt_count=outcome.attempts,
                attempt_error_count=outcome.attempt_errors,
                unrecovered_request_count=outcome.unrecovered_requests,
                malformed_count=outcome.malformed,
                distinct_ids=outcome.distinct_ids,
                ids_union_hash=outcome.ids_union_hash,
                total_first=outcome.total_first,
                total_last=outcome.total_last,
                total_trusted=1,
                terminal_evidence=outcome.terminal_evidence,
                page_size=self.limits.page_size,
                max_pages_hit=outcome.max_pages_hit,
                max_records_hit=outcome.max_records_hit,
                verified_at_utc=_iso(self.clock()),
            )
            blockers = list(proof.complete_blockers())
        status = "COMPLETE" if not blockers else ("BLOCKED" if outcome.status == "BLOCKED" else "PARTIAL")
        if status != "COMPLETE":
            proof = None
        outcome.interval_id = coverage.record_interval(
            self.conn, run_id=run_id, status=status, proof=proof,
            source_key=self.source_key, family=self.family,
            interval=outcome.interval, granularity=outcome.granularity,
        )
        self.conn.execute("INSERT INTO settings(key,value_json,updated_at_utc) VALUES(?,?,?)",
            (f"coverage:evidence:{outcome.interval_id}",
             _json({"interval_id":outcome.interval_id,"family":self.family,"from":_iso(outcome.interval.from_utc),
                    "to":_iso(outcome.interval.to_utc),"granularity":outcome.granularity,"blockers":blockers,
                    "pages":outcome.pages,"rows":outcome.rows,"requests":outcome.requests}), _iso(self.clock())))
        self.conn.commit()
        outcome.blockers = tuple(blockers)
        if outcome.status != "INTERRUPTED":
            outcome.status = status

    # ------------------------------------------------------------------ modes

    def backfill(
        self, run_id: str, span: Interval, *, order: str = ORDER_NEWEST_FIRST, skip_proven: bool = True,
    ) -> list[IntervalOutcome]:
        """§4.4: split into VN calendar days, skip only proven time, fetch the rest."""
        days = list(_vn_days(span))
        if order == ORDER_NEWEST_FIRST:
            days.reverse()
        outcomes: list[IntervalOutcome] = []
        for day in days:
            parts = self._unproven_parts(day) if skip_proven else [day]
            for interval in parts:
                outcomes.append(self.run_interval(interval, interval_granularity(interval), run_id))
            if self.stop_requested():
                break
        return outcomes

    def incremental(self, run_id: str, *, now: datetime) -> list[IntervalOutcome]:
        """Watermark minus the overlap, up to now. The watermark only moves over COMPLETE days.

        The overlap window is re-fetched on purpose (`skip_proven=False`): late edits inside
        it are exactly what the overlap exists to catch, and a re-scan that comes back
        PARTIAL simply holds the watermark where it is.
        """
        watermark = coverage.watermark_for(self.conn, self.source_key, self.family)
        from zoneinfo import ZoneInfo

        # A first run at 08:15 must include yesterday's whole VN day. A rolling 24h
        # window consists only of two partial days and cannot use a DAY-verified rule.
        start = (watermark - timedelta(days=INCREMENTAL_OVERLAP_DAYS) if watermark else
                 vn_calendar_day(now.astimezone(ZoneInfo(VN_TZ_NAME)).date() - timedelta(days=1)).from_utc)
        if start >= now:
            start = now - timedelta(hours=1)
        outcomes = self.backfill(run_id, Interval(start, now), order=ORDER_OLDEST_FIRST, skip_proven=False)
        for outcome in outcomes:
            if outcome.status == "COMPLETE" and outcome.interval_id is not None:
                coverage.advance_watermark(
                    self.conn, source_key=self.source_key, family=self.family,
                    to_utc=outcome.interval.to_utc, complete_interval_id=outcome.interval_id,
                )
        return outcomes

    def reconcile(self, run_id: str, span: Interval) -> list[dict[str, Any]]:
        """§4.4: re-scan proven intervals and write findings. A record is never deleted here."""
        gaps: list[dict[str, Any]] = []
        self.reconcile_outcomes = []
        resolution = coverage.resolve(self.conn, self.source_key, self.family, span.from_utc, span.to_utc)
        for proven in resolution.proven:
            before = _stored_identities(self.conn, self.source_key, self.family, proven)
            outcome = self.fetch_interval(proven, interval_granularity(proven), run_id)
            self._record(outcome, run_id)
            self.reconcile_outcomes.append(outcome)
            if outcome.status != "COMPLETE":
                continue  # a failed re-scan proves nothing about absence
            for key in sorted(before - outcome.identity_keys):
                gap = {"kind": "MISSING_FROM_SOURCE_IN_VERIFIED_WINDOW", "identity_hash": key}
                self._record_gap(run_id, proven, gap)
                gaps.append(gap)
            extra = sorted(outcome.identity_keys - before)
            if extra:
                gap = {"kind": "COUNT_DRIFT", "identity_hashes": extra[:20], "extra_count": len(extra)}
                self._record_gap(run_id, proven, gap)
                gaps.append(gap)
        return gaps

    # ------------------------------------------------------------------ helpers

    def _unproven_parts(self, day: Interval) -> list[Interval]:
        resolution = coverage.resolve(self.conn, self.source_key, self.family, day.from_utc, day.to_utc)
        if resolution.can_skip:
            return []
        return list(resolution.gaps) or [day]

    def _record_gap(self, run_id: str, interval: Interval, gap: dict[str, Any]) -> None:
        payload = {k: v for k, v in gap.items() if k != "interval"}
        self.conn.execute(
            " INSERT INTO reconcile_gaps (run_id, source_key, family, interval_from_utc, interval_to_utc, kind,"
            " evidence_json, detected_at_utc) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, self.source_key, self.family, _iso(interval.from_utc), _iso(interval.to_utc),
             gap["kind"], _json(payload), _iso(self.clock())),
        )


# ------------------------------------------------------------------ module helpers

_LANE_OUTCOME_TO_DDL = {"OK": "OK", "BLOCKED": "BLOCKED", "MALFORMED": "MALFORMED"}


def _ddl_outcome(attempt: Attempt) -> str:
    """Lane attempt vocabulary → fetch_attempts.outcome (a frozen DDL CHECK).

    A RETRY try is a retryable HTTP outcome; a transport ERROR is a timeout when the
    exception type says so and an HTTP error otherwise.
    """
    mapped = _LANE_OUTCOME_TO_DDL.get(attempt.outcome)
    if mapped is not None:
        return mapped
    if "timeout" in (attempt.error_kind or "").lower():
        return "TIMEOUT"
    return "HTTP_ERROR"


def interval_granularity(interval: Interval) -> str:
    for name, step in (("DAY", timedelta(days=1)), ("HOUR", timedelta(hours=1)),
                       ("MINUTE", timedelta(minutes=1)), ("SECOND", timedelta(seconds=1))):
        if interval.duration >= step:
            return name
    return "MILLISECOND"


def _granularity_step(granularity: str) -> timedelta:
    return {"DAY": timedelta(days=1), "HOUR": timedelta(hours=1), "MINUTE": timedelta(minutes=1),
            "SECOND": timedelta(seconds=1), "MILLISECOND": timedelta(milliseconds=1)}[granularity]


def _vn_days(span: Interval) -> list[Interval]:
    """The VN calendar days covering `span`, clipped to it, oldest first (§4.3)."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(VN_TZ_NAME)
    first = span.from_utc.astimezone(zone).date()
    last = span.to_utc.astimezone(zone).date()
    days: list[Interval] = []
    day = first
    while day <= last:
        interval = vn_calendar_day(day)
        clipped = interval.clipped_to(span)
        if clipped is not None and clipped.duration > timedelta(0):
            days.append(clipped)
        day += timedelta(days=1)
    return days


def _identity_keys(family: str, records) -> set[str]:
    keys: set[str] = set()
    for record in records:
        if not record.extractable:
            continue
        normalized = record.normalized
        keys.add(f"{family}|{normalized.natural_key}|{normalized.version_state}|{normalized.version_raw or ''}")
    return keys


def _query_fingerprint(source_key: str, family: str, endpoint: str, interval: Interval) -> str:
    canonical = f"idq1:{source_key}|{family}|{endpoint}|{_iso(interval.from_utc)}|{_iso(interval.to_utc)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stored_identities(conn, source_key: str, family: str, interval: Interval) -> set[str]:
    """Identity keys already observed for this window, judged by the attempt's requested interval.

    The wall clock says when *we* ran; the requested interval says what the source was asked
    for — and reconcile compares like with like, so the join is on fetch_requests, not time.
    """
    rows = conn.execute(
        " SELECT DISTINCT sr.family || '|' || sr.natural_key || '|' || sr.version_state || '|' ||"
        "        COALESCE(sr.version_raw, '')"
        "   FROM observations o"
        "   JOIN fetch_attempts fa ON fa.id = o.attempt_id"
        "   JOIN fetch_requests fr ON fr.id = fa.request_id"
        "   JOIN source_records sr ON sr.identity_key = substr(o.observation_key, 4)"
        "  WHERE sr.source_key = ? AND sr.family = ?"
        "    AND fr.interval_from_utc < ? AND fr.interval_to_utc > ?",
        (source_key, family, _iso(interval.to_utc), _iso(interval.from_utc)),
    )
    return {row[0] for row in rows}


def _reason(exc: Exception) -> str:
    text = str(exc).split(":", 1)[-1].strip().replace(" ", "_")
    return text[:80]


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=list)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
