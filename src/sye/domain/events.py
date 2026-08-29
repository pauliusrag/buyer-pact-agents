"""Audit events — the traceability backbone of the demo.

Only concise decision summaries are stored. Model chain-of-thought is never
persisted or streamed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from sye.domain.enums import AuditStatus
from sye.domain.primitives import EvidenceSource, SyeModel


class AuditEvent(SyeModel):
    event_id: str
    run_id: str
    sequence: int
    timestamp: datetime

    node: str
    event_type: str
    status: AuditStatus

    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)

    message: str
    decision: str | None = None
    confidence: float | None = None

    sources: list[EvidenceSource] = Field(default_factory=list)
    duration_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def render(self) -> str:
        """Human-readable one-liner used by the CLI timeline."""
        icon = {
            AuditStatus.STARTED: "·",
            AuditStatus.COMPLETED: "✓",
            AuditStatus.WARNING: "!",
            AuditStatus.FAILED: "✗",
        }[self.status]
        return f"[{self.sequence:02d}] {icon} {self.node}: {self.message}"
