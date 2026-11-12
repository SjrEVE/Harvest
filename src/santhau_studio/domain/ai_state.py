"""Structured state the AI control lane exposes (read-only contract, v1)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class AIState:
    schema_version: int = 1
    seq: int = 0
    worker_pid: int | None = None
    phase: str = "STARTING"
    current_job_id: str | None = None
    progress: dict = field(default_factory=dict)
    serving: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    last_error: str | None = None
    updated_at_utc: str | None = None

    @classmethod
    def initial(cls, worker_pid: int | None = None) -> AIState:
        return cls(worker_pid=worker_pid)

    def to_dict(self) -> dict:
        return asdict(self)
