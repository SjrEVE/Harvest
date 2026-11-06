"""Coverage resolver — the U-1 contract.

§4.5 names one resolver and forbids consumers from joining coverage_intervals themselves,
but it states the *precedence rules* without pinning the return shape or the union
arithmetic. This module fixes both, because "a VN day is COMPLETE" is only testable once
the union of half-open intervals is defined precisely.

Invariants this implementation is built around:

* intervals are half-open ``[from, to)`` in UTC, so adjacency merges and never overlaps;
* only ``COMPLETE`` intervals with a usable proof contribute to ``proven``; a newer
  ``PARTIAL`` on the same span cannot mask a verified ``COMPLETE`` (§4.5);
* a ``COMPLETE`` row without ``proof_version`` is ``LEGACY_COMPLETE_UNVERIFIED`` — it is
  surfaced, never used to skip (BUG-15);
* a VN calendar day is COMPLETE only when proven intervals cover the whole day after
  converting to UTC. Covering 3 of 24 hours is not a complete day (COV-13).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC = UTC

Granularity = Literal["DAY", "HOUR", "MINUTE", "SECOND", "MILLISECOND"]
RowStatus = Literal["COMPLETE", "PARTIAL", "FAILED", "BLOCKED", "INTERRUPTED"]
ResolutionStatus = Literal[
    "COMPLETE", "PARTIAL", "NOT_ACCESSED", "NEEDS_RECONCILE", "BLOCKED", "UNSUPPORTED"
]

# Coarser granularity is stronger evidence: one proven DAY subsumes any split beneath it.
_GRANULARITY_RANK: dict[str, int] = {
    "DAY": 5,
    "HOUR": 4,
    "MINUTE": 3,
    "SECOND": 2,
    "MILLISECOND": 1,
}


@dataclass(frozen=True)
class Interval:
    """Half-open UTC interval [from_utc, to_utc)."""

    from_utc: datetime
    to_utc: datetime

    def __post_init__(self) -> None:
        if self.from_utc.tzinfo is None or self.to_utc.tzinfo is None:
            raise ValueError("coverage intervals must be tz-aware UTC")
        if _as_utc(self.from_utc) >= _as_utc(self.to_utc):
            raise ValueError(f"empty interval is not evidence: {self.from_utc} >= {self.to_utc}")

    @property
    def duration(self) -> timedelta:
        return _as_utc(self.to_utc) - _as_utc(self.from_utc)

    def intersects(self, other: Interval) -> bool:
        return _as_utc(self.from_utc) < _as_utc(other.to_utc) and _as_utc(other.from_utc) < _as_utc(
            self.to_utc
        )

    def clipped_to(self, other: Interval) -> Interval | None:
        lo = max(_as_utc(self.from_utc), _as_utc(other.from_utc))
        hi = min(_as_utc(self.to_utc), _as_utc(other.to_utc))
        return Interval(lo, hi) if lo < hi else None

    def contains(self, other: Interval) -> bool:
        return _as_utc(self.from_utc) <= _as_utc(other.from_utc) and _as_utc(
            self.to_utc
        ) >= _as_utc(other.to_utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class CoverageRow:
    """One coverage_intervals record, as the resolver sees it."""

    id: int
    source_key: str
    family: str
    interval: Interval
    status: RowStatus
    granularity: Granularity
    proof_id: int | None = None
    proof_version: int | None = None
    data_revised_after_proof: bool = False
    compatible_context: bool = True

    @property
    def is_verified_complete(self) -> bool:
        """COMPLETE counts only when a proof with a proof_version stands behind it."""
        return (
            self.status == "COMPLETE"
            and self.proof_id is not None
            and self.proof_version is not None
            and self.compatible_context
        )

    @property
    def is_legacy_unverified(self) -> bool:
        return self.status == "COMPLETE" and not self.is_verified_complete

    @property
    def needs_reconcile(self) -> bool:
        return self.status == "COMPLETE" and self.data_revised_after_proof


@dataclass(frozen=True)
class CoverageResolution:
    """The exact return contract of resolve_coverage (U-1).

    ``proven`` and ``gaps`` are disjoint, sorted, maximal unions inside ``requested``.
    A caller decides "can I skip this range?" from ``status == COMPLETE`` alone; it must
    not re-derive coverage, which is the whole point of having one resolver (§4.5).
    """

    source_key: str
    family: str
    requested: Interval
    status: ResolutionStatus
    proven: tuple[Interval, ...] = ()
    gaps: tuple[Interval, ...] = ()
    legacy_unverified: tuple[Interval, ...] = ()
    stale: tuple[Interval, ...] = ()
    blocked: tuple[Interval, ...] = ()
    granularity: Granularity | None = None
    proof_ids: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def can_skip(self) -> bool:
        """Skip is granted only for a fully proven, reconciled, non-legacy span."""
        return self.status == "COMPLETE"

    @property
    def proven_fraction(self) -> float:
        covered = sum((interval.duration for interval in self.proven), timedelta(0))
        return covered.total_seconds() / self.requested.duration.total_seconds()


@dataclass
class _Accumulator:
    rows: list[Interval] = field(default_factory=list)


def union(intervals: Iterable[Interval]) -> tuple[Interval, ...]:
    """Merge to a disjoint, sorted, maximal set. Adjacent halves merge: [a,b)+[b,c) = [a,c)."""
    items = sorted(
        (Interval(_as_utc(i.from_utc), _as_utc(i.to_utc)) for i in intervals),
        key=lambda i: (_as_utc(i.from_utc), _as_utc(i.to_utc)),
    )
    if not items:
        return ()
    merged: list[Interval] = [items[0]]
    for current in items[1:]:
        last = merged[-1]
        if _as_utc(current.from_utc) <= _as_utc(last.to_utc):  # touching or overlapping
            merged[-1] = Interval(last.from_utc, max(_as_utc(last.to_utc), _as_utc(current.to_utc)))
        else:
            merged.append(current)
    return tuple(merged)


def subtract(base: Iterable[Interval], removals: Iterable[Interval]) -> tuple[Interval, ...]:
    """Set difference on half-open intervals; the result stays disjoint and sorted."""
    remaining = list(union(base))
    for cut in union(removals):
        nxt: list[Interval] = []
        for piece in remaining:
            lo, hi = _as_utc(piece.from_utc), _as_utc(piece.to_utc)
            clo, chi = _as_utc(cut.from_utc), _as_utc(cut.to_utc)
            if chi <= lo or clo >= hi:
                nxt.append(piece)
                continue
            if lo < clo:
                nxt.append(Interval(lo, clo))
            if chi < hi:
                nxt.append(Interval(chi, hi))
        remaining = nxt
    return tuple(sorted(remaining, key=lambda i: _as_utc(i.from_utc)))


def resolve_coverage(
    source_key: str,
    family: str,
    requested: Interval,
    rows: Iterable[CoverageRow],
    *,
    historical_access_supported: bool = True,
) -> CoverageResolution:
    """Single resolver for every consumer (§4.5): inventory, serving, operations, skip, UI."""
    if not historical_access_supported:
        return CoverageResolution(
            source_key=source_key,
            family=family,
            requested=requested,
            status="UNSUPPORTED",
            reasons=("HISTORICAL_ACCESS_UNSUPPORTED",),
        )

    scoped = [row for row in rows if row.interval.intersects(requested)]
    if not scoped:
        return CoverageResolution(
            source_key=source_key,
            family=family,
            requested=requested,
            status="NOT_ACCESSED",
            gaps=(requested,),
            reasons=("no coverage rows in range",),
        )

    complete_verified = [r for r in scoped if r.is_verified_complete]
    legacy = [r for r in scoped if r.is_legacy_unverified]
    stale = [r for r in scoped if r.needs_reconcile]
    blocked = [r for r in scoped if r.status == "BLOCKED"]

    # A verified COMPLETE beats any PARTIAL over the same span (§4.5), so PARTIAL rows
    # never enter `proven` and never subtract from it either.
    proven_in_range = union(
        [
            r.interval.clipped_to(requested)
            for r in complete_verified
            if r.interval.clipped_to(requested)
        ]
    )
    gaps = subtract([requested], proven_in_range)

    legacy_intervals = union(
        [r.interval.clipped_to(requested) for r in legacy if r.interval.clipped_to(requested)]
    )
    stale_intervals = union(
        [r.interval.clipped_to(requested) for r in stale if r.interval.clipped_to(requested)]
    )
    blocked_intervals = union(
        [r.interval.clipped_to(requested) for r in blocked if r.interval.clipped_to(requested)]
    )

    reasons: list[str] = []
    if any(row.proof_version is not None and not row.compatible_context for row in scoped):
        reasons.append("PROOF_CONTEXT_INCOMPATIBLE")
    if legacy_intervals:
        reasons.append(
            "LEGACY_COMPLETE_UNVERIFIED: COMPLETE rows lack proof_version and are not skippable"
        )
    if stale_intervals:
        reasons.append("STALE_NEEDS_RECONCILE: data revised after proof")

    if stale_intervals:
        status: ResolutionStatus = "NEEDS_RECONCILE"
    elif not gaps and not legacy_intervals:
        status = "COMPLETE"
    elif proven_in_range:
        status = "PARTIAL"
    elif blocked_intervals and not legacy_intervals:
        status = "BLOCKED"
    elif any(r.status in ("PARTIAL", "FAILED", "INTERRUPTED") for r in scoped):
        status = "PARTIAL"
        reasons.append("ACCESSED_WITHOUT_COMPLETE_PROOF")
    else:
        status = "PARTIAL" if (gaps and (proven_in_range or legacy_intervals)) else "NOT_ACCESSED"

    granularity = (
        _coarsest_covering(complete_verified, requested, proven_in_range)
        if proven_in_range
        else None
    )
    proof_ids = tuple(sorted({r.proof_id for r in complete_verified if r.proof_id is not None}))

    return CoverageResolution(
        source_key=source_key,
        family=family,
        requested=requested,
        status=status,
        proven=proven_in_range,
        gaps=gaps,
        legacy_unverified=legacy_intervals,
        stale=stale_intervals,
        blocked=blocked_intervals,
        granularity=granularity,
        proof_ids=proof_ids,
        reasons=tuple(reasons),
    )


def _coarsest_covering(
    rows: list[CoverageRow], requested: Interval, proven: tuple[Interval, ...]
) -> Granularity | None:
    """Granularity actually proven for the whole range, or None.

    Only rows whose union really covers `requested` may justify reporting their
    granularity; otherwise a single DAY row plus nine HOUR rows would claim DAY.
    """
    if union([r.interval for r in rows if r.interval.contains(requested)]):
        return max(
            (r.granularity for r in rows if r.interval.contains(requested)),
            key=lambda g: _GRANULARITY_RANK[g],
        )
    if len(proven) == 1 and proven[0].contains(requested):
        return max((r.granularity for r in rows), key=lambda g: _GRANULARITY_RANK[g])
    return None


def vn_calendar_day(day: date) -> Interval:
    """The UTC span of one Vietnamese calendar day — half-open, DST-free at UTC+7.

    Built from real datetime arithmetic so leap days and month ends are correct by
    construction (COV-11), not by a hand-rolled 24*60*60 assumption.
    """
    start_local = datetime(day.year, day.month, day.day, tzinfo=VN_TZ)
    end_local = start_local + timedelta(days=1)
    return Interval(start_local.astimezone(UTC), end_local.astimezone(UTC))


def day_is_complete(
    day: date, rows: Iterable[CoverageRow], source_key: str, family: str
) -> CoverageResolution:
    """§4.3: covered_days is *derived* — a VN day is COMPLETE only when proven intervals
    cover the whole day after UTC conversion. Three of twenty-four hours is not enough."""
    return resolve_coverage(source_key, family, vn_calendar_day(day), rows)
