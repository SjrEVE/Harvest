"""Time-boundary contract for a source filter (§4.2).

The schema speaks half-open UTC `[from, to)`. The portal speaks *closed* VN-local days: a
request for 2026-11-01 asks `publicDate` between `2026-11-01 00:00:00` and
`2026-11-01 23:59:59.999`. Getting the closing term wrong loses or duplicates the rows that
sit right on the boundary — the quietest and most damaging coverage defect there is, which is
why the rule is data (`filter_boundary_rules`), verified by canary, and carried in every proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from santhau_studio.domain.coverage import Interval

CLOSED_TERM_MINUS = {"MILLISECOND": timedelta(milliseconds=1), "SECOND": timedelta(seconds=1),
                     "MINUTE": timedelta(minutes=1), "HOUR": timedelta(hours=1), "DAY": timedelta(days=1)}
# A closed range is expressed with the *last instant the source can name* at its own precision,
# so the upper bound uses a different format from the lower one: at DAY precision the closing
# term of 2026-11-01 is "2026-11-01 23:59:59", not a second "2026-11-01 00:00:00".
_FORMATS = {
    "DAY": "%Y-%m-%d 00:00:00",
    "HOUR": "%Y-%m-%d %H:00:00",
    "MINUTE": "%Y-%m-%d %H:%M:00",
    "SECOND": "%Y-%m-%d %H:%M:%S",
    "MILLISECOND": "%Y-%m-%d %H:%M:%S.%f",
}
_END_FORMATS = {
    "DAY": "%Y-%m-%d 23:59:59",
    "HOUR": "%Y-%m-%d %H:59:59",
    "MINUTE": "%Y-%m-%d %H:%M:59",
    "SECOND": "%Y-%m-%d %H:%M:%S",
    "MILLISECOND": "%Y-%m-%d %H:%M:%S.%f",
}


class BoundaryError(ValueError):
    """The rule cannot describe this interval; refusing is better than guessing a filter."""


@dataclass(frozen=True)
class BoundaryRule:
    """A declared and canary-verified edge contract for one endpoint+family+adapter."""

    rule_id: int
    boundary_kind: str
    precision: str
    source_timezone: str
    offset_minutes: int
    canary_verified: bool

    @classmethod
    def from_row(cls, row) -> BoundaryRule:
        return cls(
            rule_id=int(row["id"]),
            boundary_kind=row["boundary_kind"],
            precision=row["precision"],
            source_timezone=row["source_timezone"],
            offset_minutes=int(row["offset_minutes"]),
            canary_verified=bool(row["canary_verified"]),
        )

    @property
    def zone(self) -> timezone | ZoneInfo:
        try:
            return ZoneInfo(self.source_timezone)
        except Exception:  # zoneinfo raises several types depending on platform data
            return timezone(timedelta(minutes=self.offset_minutes))

    def requires_canary(self) -> bool:
        return not self.canary_verified


def source_window_for(rule: BoundaryRule, interval: Interval) -> tuple[str, str]:
    """Convert a half-open UTC interval into the pair this source actually accepts.

    CLOSED_LOCAL and CLOSED_UTC sources treat the upper term as *inclusive*, so the closing
    value is the last instant they can express inside the interval: `to - 1ms` at millisecond
    precision, `to - 1s` at second precision, and so on. A HALF_OPEN_UTC source takes the pair
    as given and must not be adjusted — subtracting there would drop the last instant.
    """
    if rule.precision not in _FORMATS:
        raise BoundaryError(f"unsupported precision {rule.precision!r}")
    fmt = _FORMATS[rule.precision]
    if rule.boundary_kind == "OPEN_UTC":
        raise BoundaryError("an OPEN_UTC source cannot be proven terminal at a boundary")
    if rule.boundary_kind == "HALF_OPEN_UTC":
        # The source honours the same convention the schema uses: no adjustment either end.
        return (
            _format(interval.from_utc.astimezone(UTC), fmt, rule.precision),
            _format(interval.to_utc.astimezone(UTC), fmt, rule.precision),
        )
    zone = UTC if rule.boundary_kind == "CLOSED_UTC" else rule.zone
    from_local = interval.from_utc.astimezone(zone)
    closing = interval.to_utc.astimezone(zone) - CLOSED_TERM_MINUS[rule.precision]
    if closing < from_local:
        raise BoundaryError(f"interval shorter than the source's {rule.precision} precision")
    return (
        _format(from_local, fmt, rule.precision),
        _format(closing, _END_FORMATS[rule.precision], rule.precision),
    )


def _format(value: datetime, fmt: str, precision: str) -> str:
    """%f yields microseconds; a millisecond-precision source wants three digits."""
    text = value.strftime(fmt)
    if precision == "MILLISECOND" and "." in text:
        head, _, tail = text.partition(".")
        return f"{head}.{tail[:3]}"
    return text


def split_for_granularity(interval: Interval, granularity: str) -> tuple[Interval, ...]:
    """Cut one interval into the next finer spans (§4.3 adaptive split)."""
    step = {"DAY": timedelta(days=1), "HOUR": timedelta(hours=1), "MINUTE": timedelta(minutes=1),
            "SECOND": timedelta(seconds=1), "MILLISECOND": timedelta(milliseconds=1)}[granularity]
    if interval.duration <= step:
        return (interval,)
    parts: list[Interval] = []
    cursor = interval.from_utc
    while cursor < interval.to_utc:
        nxt = min(cursor + step, interval.to_utc)
        parts.append(Interval(cursor, nxt))
        cursor = nxt
    return tuple(parts)
