"""Canonical identity serialization — the O-3 contract primitive.

Why this exists instead of ``f"{family}|{natural_key}|{state}|{version_raw}"``:

* a delimiter join is ambiguous whenever a source value contains the delimiter, so
  ``notifyNo="A|B"`` with ``state="C"`` and ``notifyNo="A"`` with ``state="B|C"``
  collide;
* ``None`` rendered into a string becomes ``"None"``, which is a *different* identity
  from an empty version and is exactly the BUG-08 class the spec forbids;
* Unicode that differs only in normalisation form (``é`` as U+00E9 versus ``e`` + U+0301)
  fragments one real notice into two source records.

The encoding is therefore versioned, length-tagged and hashed. The version prefix is
load-bearing: ``identity_key`` sits in a UNIQUE constraint over a customer's warehouse,
so any future change must be a *new* version, never a silent edit of version 1.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

IDENTITY_SCHEMA_VERSION = 1
IDENTITY_PREFIX = f"id{IDENTITY_SCHEMA_VERSION}:"
CONTENT_HASH_VERSION = 1
CONTENT_HASH_PREFIX = f"ch{CONTENT_HASH_VERSION}:"

VERSION_STATES = frozenset({"KNOWN", "MISSING", "UNPARSABLE"})


class IdentityError(ValueError):
    """Raised when a component cannot form a valid identity. Never coerced."""


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def canonical_bytes(value: Any) -> bytes:
    """Deterministic serialisation: NFC-normalised strings, sorted keys, no optional whitespace.

    Used for identity components, content hashes and record-object payloads, so the same
    logical object always produces the same bytes from two different code paths.
    """
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        # 1.0 and 1 must not become two different identities for the same source value.
        return value if value != int(value) else int(value)
    return value


def identity_components(
    family: str,
    natural_key: str,
    version_state: str,
    version_raw: str | None,
) -> dict[str, Any]:
    """Validate and assemble the four components of a source-record identity.

    ``version_state`` and ``version_raw`` must agree: MISSING carries no raw value, and a
    present raw value implies KNOWN or UNPARSABLE. Rejecting the mismatch here is what
    stops "missing version silently becomes 00" (BUG-08) from ever reaching the database.
    """
    family = _nfc(family).strip()
    natural_key = _nfc(natural_key)
    if not family:
        raise IdentityError("family must be non-empty")
    if not natural_key:
        raise IdentityError(
            "natural_key must be non-empty; an unidentified payload is UNRESOLVED, not a record"
        )
    if version_state not in VERSION_STATES:
        raise IdentityError(
            f"version_state must be one of {sorted(VERSION_STATES)}, got {version_state!r}"
        )
    if version_state == "MISSING":
        if version_raw is not None:
            raise IdentityError(
                "MISSING must not carry version_raw; got a value, so this is not MISSING"
            )
    elif version_raw is None:
        raise IdentityError(
            f"{version_state} requires version_raw; use MISSING for an absent source version"
        )

    return {
        "family": family,
        "natural_key": natural_key,
        "version_state": version_state,
        "version_raw": version_raw,
    }


def identity_key(
    family: str,
    natural_key: str,
    version_state: str,
    version_raw: str | None,
) -> str:
    """Stable identity for one *publication* of one source record.

    TBMT ``notifyVersion`` 00 and 01 differ only in ``version_raw``, so they yield two
    identities and therefore two ``source_record`` rows, joined afterwards by
    :func:`notice_group_key` (O-3 / DEDUP-16).
    """
    components = identity_components(family, natural_key, version_state, version_raw)
    # Length-tagged: the JSON array keeps each component unambiguously bounded.
    payload = json.dumps(
        [
            components["family"],
            components["natural_key"],
            components["version_state"],
            components["version_raw"],
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return IDENTITY_PREFIX + hashlib.sha256(IDENTITY_PREFIX.encode("utf-8") + payload).hexdigest()


def notice_group_key(family: str, natural_key: str) -> str:
    """Identity of the *underlying notice*, ignoring which publication version it is.

    Two source records share a group only when family and natural key match exactly.
    Grouping is a display relation: it must never merge the records themselves.

    Same length-tagged encoding as identity_key — joining two free-text fields with a
    separator would collide exactly like the identity_key case this module rejects.
    """
    payload = json.dumps(
        [_nfc(family).strip(), _nfc(natural_key)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ng" + hashlib.sha256(f"ng{IDENTITY_SCHEMA_VERSION}:".encode() + payload).hexdigest()


def content_hash(normalized_record: dict[str, Any]) -> str:
    """Hash of the *normalized* record — the content-version axis (§3.5).

    Two records that differ only in raw formatting but normalize alike must produce the
    same hash, otherwise every re-fetch invents a content version.
    """
    return CONTENT_HASH_PREFIX + hashlib.sha256(canonical_bytes(normalized_record)).hexdigest()


def record_object_sha(record_payload: Any) -> str:
    """Hash of one record's canonical bytes — the raw-revision axis (§3.2).

    Deliberately separate from :func:`content_hash`: one is about what the source sent,
    the other about what we understood. A page re-fetch that changes only formatting must
    move the raw revision and leave the content version alone (DEDUP-09, DEDUP-15).
    """
    return hashlib.sha256(canonical_bytes(record_payload)).hexdigest()


def response_object_sha(page_payload: Any) -> str:
    """Hash of a whole page after redaction — terminal evidence, never a record identity."""
    return hashlib.sha256(canonical_bytes(page_payload)).hexdigest()
