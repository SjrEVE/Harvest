"""Domain value objects shared by extraction and storage.

`NormalizedRecord` describes what an adapter understood about one record — the input to
the content-version axis. It lives in `domain`, not in `storage`, because both the
extractor (which produces it) and the writer (which consumes it) need it. Putting it in
either layer creates an import cycle between collectors and storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VERSION_STATES = ("KNOWN", "MISSING", "UNPARSABLE")


class RecordContractError(ValueError):
    """A record violates the identity contract before it ever reaches the database."""


class IntegrityMismatchError(RuntimeError):
    """A record's bytes disagree with the hash the extractor recorded for them."""


@dataclass(frozen=True)
class NormalizedRecord:
    """One record as understood by an adapter.

    The version invariant is enforced here, not in SQL, so a bad mapping fails at the
    adapter boundary with a readable message instead of surfacing as an IntegrityError
    three layers later. `MISSING` carrying a raw value is exactly the coercion the source
    adapter performed (`or "00"`) and is rejected outright.
    """

    family: str
    natural_key: str | None
    version_state: str
    version_raw: str | None
    fields: dict[str, Any]

    def __post_init__(self) -> None:
        if self.version_state not in VERSION_STATES:
            raise RecordContractError(
                f"{self.family}: version_state must be one of {VERSION_STATES}, got {self.version_state!r}"
            )
        if self.version_state == "MISSING" and self.version_raw is not None:
            raise RecordContractError(
                f"{self.family}: MISSING must not carry version_raw (got {self.version_raw!r}). "
                "A present value is not MISSING — this is the `or \"00\"` coercion the contract forbids."
            )
        if self.version_state in ("KNOWN", "UNPARSABLE") and self.version_raw is None:
            raise RecordContractError(
                f"{self.family}: {self.version_state} requires version_raw; use MISSING when the source omitted it"
            )
        if not self.family:
            raise RecordContractError("family must be non-empty")
