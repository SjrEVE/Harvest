"""Record extraction — production path, not test scaffolding.

Closes verification gap V-2: a derivation row must be produced by an extraction that
actually happened. The payload an ExtractedRecord carries is the object read out of the
response at `locator`, so the recorded hash is by construction the hash of what that
locator resolves to. A writer cannot fabricate a derivation through this module without
fabricating the payload too, and the hash check in `verify_extraction` catches that.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from santhau_studio.domain.identity import canonical_bytes, record_object_sha
from santhau_studio.domain.records import NormalizedRecord


class LocatorError(ValueError):
    """The locator does not resolve against the response it claims to come from."""


def resolve_locator(payload: Any, locator: str) -> Any:
    """RFC 6901 JSON pointer, plus the `row:<n>` form for bare-array responses (§3.2)."""
    if locator.startswith("row:"):
        try:
            ordinal = int(locator.removeprefix("row:"))
        except ValueError as exc:
            raise LocatorError(f"bad row locator {locator!r}") from exc
        if not isinstance(payload, list):
            raise LocatorError(f"{locator} needs an array payload, got {type(payload).__name__}")
        if not 0 <= ordinal < len(payload):
            raise LocatorError(f"{locator} out of range (len {len(payload)})")
        return payload[ordinal]
    if not locator.startswith("/"):
        raise LocatorError(f"unrecognised locator form: {locator!r}")
    node = payload
    for raw_token in locator.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not re.fullmatch(r"0|[1-9]\d*", token):
                raise LocatorError(f"{locator}: {token!r} is not an array index")
            index = int(token)
            if index >= len(node):
                raise LocatorError(f"{locator}: index {index} out of range (len {len(node)})")
            node = node[index]
        elif isinstance(node, dict):
            if token not in node:
                raise LocatorError(f"{locator}: no member {token!r}")
            node = node[token]
        else:
            raise LocatorError(f"{locator}: cannot descend into {type(node).__name__}")
    return node


@dataclass(frozen=True)
class FamilySpec:
    """One family's endpoint shape and identity mapping.

    `version_field` feeds version_state/version_raw and must NOT be folded into
    natural_key (O-3 method A). `missing_is_zero` is the bug this contract forbids:
    the source adapter did `doc.get("notifyVersion") or "00"` on a path shared by all
    six families, which silently turns MISSING into a real-looking version.
    """

    family: str
    content_path: str
    natural_key_field: str
    version_field: str
    normalize: Callable[[dict[str, Any]], dict[str, Any]]
    unparseable_version: tuple[str, ...] = ("",)

    def natural_key_of(self, record: dict[str, Any]) -> str | None:
        value = record.get(self.natural_key_field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def version_of(self, record: dict[str, Any]) -> tuple[str, str | None]:
        """Map the source version field to (version_state, version_raw). Never coerces."""
        raw = record.get(self.version_field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return "MISSING", None
        if not isinstance(raw, str):
            raw = str(raw)
        if raw in self.unparseable_version or not re.fullmatch(r"[0-9]+", raw):
            return "UNPARSABLE", raw
        return "KNOWN", raw


@dataclass(frozen=True)
class ExtractedRecord:
    locator: str
    payload: dict[str, Any]
    normalized: NormalizedRecord
    record_sha: str
    extractable: bool = True
    reason_unresolved: str | None = None


@dataclass
class ExtractionReport:
    records: list[ExtractedRecord] = field(default_factory=list)
    unresolved: list[ExtractedRecord] = field(default_factory=list)
    malformed_members: int = 0

    @property
    def ingestible(self) -> list[ExtractedRecord]:
        return [r for r in self.records if r.extractable]


def extract_page(response: Any, spec: FamilySpec) -> ExtractionReport:
    """Walk the response and produce one ExtractedRecord per member, with a real locator."""
    report = ExtractionReport()
    for locator, member in _members(response, spec.content_path):
        if not isinstance(member, dict):
            # A member we cannot even treat as an object is page-level damage, not a record.
            report.malformed_members += 1
            continue
        payload = _as_json_object(member)
        sha = record_object_sha(payload)
        natural = spec.natural_key_of(member)
        state, raw = spec.version_of(member)
        normalized = NormalizedRecord(
            family=spec.family,
            natural_key=natural,
            version_state=state,
            version_raw=raw,
            fields=spec.normalize(member),
        )
        record = ExtractedRecord(
            locator=locator, payload=payload, normalized=normalized, record_sha=sha,
            extractable=natural is not None,
            reason_unresolved=None if natural is not None else "MISSING_NATURAL_KEY",
        )
        report.records.append(record)
        if natural is None:
            report.unresolved.append(record)
    return report


def verify_extraction(response: Any, report: ExtractionReport) -> None:
    """Assert every recorded locator resolves to a payload whose hash matches.

    This is the production-side guard that makes a fabricated derivation row detectable
    rather than merely discouraged.
    """
    for record in report.records:
        resolved = resolve_locator(response, record.locator)
        canonical = canonical_bytes(_as_json_object(resolved))
        if canonical_bytes(_as_json_object(record.payload)) != canonical:
            raise LocatorError(f"locator {record.locator} does not resolve to the recorded payload")
        if sha256(canonical).hexdigest() != record.record_sha:
            raise LocatorError(f"record hash mismatch at {record.locator}")


def _members(response: Any, content_path: str) -> list[tuple[str, Any]]:
    """Return (locator, member) pairs. A bare-array response yields `row:<n>` locators."""
    if isinstance(response, list):
        return [(f"row:{i}", item) for i, item in enumerate(response)]
    container = _descend(response, content_path)
    if container is None or not isinstance(container, list):
        return []
    return [(f"{content_path}/{i}", item) for i, item in enumerate(container)]


def _descend(node: Any, pointer: str) -> Any:
    for token in pointer.split("/")[1:]:
        if isinstance(node, dict):
            node = node.get(token.replace("~1", "/").replace("~0", "~"))
        else:
            return None
    return node


def _as_json_object(member: Any) -> Any:
    """Round-trip through JSON so canonical bytes are stable across dict orderings."""
    return json.loads(json.dumps(member, ensure_ascii=False, default=str))
