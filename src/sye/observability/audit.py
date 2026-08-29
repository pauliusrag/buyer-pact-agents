"""Audit logging.

Every node wraps its work in ``async with audit.step(...) as step:``. The context
manager guarantees an event is written whether the node succeeds, warns or fails,
and it measures duration. Events are appended to an in-memory ordered list, pushed
to live subscribers (SSE) and persisted incrementally through an optional sink.

Only concise decision summaries are recorded — never model chain-of-thought.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any

from sye.domain.enums import AuditStatus
from sye.domain.events import AuditEvent
from sye.domain.ids import stable_id, utcnow
from sye.domain.primitives import EvidenceSource
from sye.observability.logging import get_logger

logger = get_logger("sye.audit")

EventSink = Callable[[AuditEvent], None]


class AuditStep:
    """Handle passed to node bodies to describe what happened."""

    def __init__(self, logger_: AuditLogger, node: str, event_type: str, input_refs: list[str]):
        self._audit = logger_
        self.node = node
        self.event_type = event_type
        self.input_refs = list(input_refs)
        self._finished = False
        self._payload: dict[str, Any] = {}
        self._status = AuditStatus.COMPLETED

    def complete(
        self,
        *,
        message: str,
        output_refs: Iterable[str] = (),
        decision: str | None = None,
        confidence: float | None = None,
        sources: Iterable[EvidenceSource] = (),
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._status = AuditStatus.COMPLETED
        self._payload = {
            "message": message,
            "output_refs": list(output_refs),
            "decision": decision,
            "confidence": confidence,
            "sources": list(sources),
            "metadata": metadata or {},
        }
        self._finished = True

    def warn(self, message: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Record a warning without failing the node."""
        self._status = AuditStatus.WARNING
        self._payload = {
            "message": message,
            "output_refs": self._payload.get("output_refs", []),
            "decision": self._payload.get("decision"),
            "confidence": self._payload.get("confidence"),
            "sources": self._payload.get("sources", []),
            "metadata": {**self._payload.get("metadata", {}), **(metadata or {})},
        }
        self._finished = True

    @property
    def status(self) -> AuditStatus:
        return self._status

    def _finalize(self, duration_ms: int) -> AuditEvent:
        payload = self._payload or {"message": f"{self.node} finished"}
        return self._audit._emit(
            node=self.node,
            event_type=self.event_type,
            status=self._status,
            input_refs=self.input_refs,
            duration_ms=duration_ms,
            **payload,
        )


class AuditLogger:
    """Ordered, incrementally persisted audit trail for a single run."""

    def __init__(self, run_id: str, sinks: Iterable[EventSink] = ()) -> None:
        self.run_id = run_id
        self.events: list[AuditEvent] = []
        self._sequence = 0
        self._drained = 0
        self._sinks: list[EventSink] = list(sinks)

    # -- sinks ------------------------------------------------------------- #
    def add_sink(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def remove_sink(self, sink: EventSink) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    # -- emitting ---------------------------------------------------------- #
    def _emit(
        self,
        *,
        node: str,
        event_type: str,
        status: AuditStatus,
        message: str,
        input_refs: Iterable[str] = (),
        output_refs: Iterable[str] = (),
        decision: str | None = None,
        confidence: float | None = None,
        sources: Iterable[EvidenceSource] = (),
        duration_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        self._sequence += 1
        event = AuditEvent(
            event_id=stable_id("evt", self.run_id, self._sequence),
            run_id=self.run_id,
            sequence=self._sequence,
            timestamp=utcnow(),
            node=node,
            event_type=event_type,
            status=status,
            input_refs=list(input_refs),
            output_refs=list(output_refs),
            message=message,
            decision=decision,
            confidence=confidence,
            sources=list(sources),
            duration_ms=duration_ms,
            metadata=metadata or {},
        )
        self.events.append(event)
        logger.debug("%s", event.render())
        for sink in list(self._sinks):
            try:
                sink(event)
            except Exception:  # a broken sink must never break the pipeline
                logger.warning("audit sink failed", exc_info=True)
        return event

    def event(
        self,
        *,
        node: str,
        message: str,
        event_type: str = "note",
        status: AuditStatus = AuditStatus.COMPLETED,
        **kwargs: Any,
    ) -> AuditEvent:
        """Record a standalone event (no timing)."""
        return self._emit(
            node=node, event_type=event_type, status=status, message=message, **kwargs
        )

    def warning(self, *, node: str, message: str, **kwargs: Any) -> AuditEvent:
        return self._emit(
            node=node,
            event_type=kwargs.pop("event_type", "warning"),
            status=AuditStatus.WARNING,
            message=message,
            **kwargs,
        )

    @asynccontextmanager
    async def step(
        self,
        *,
        node: str,
        event_type: str = "node",
        input_refs: Iterable[str] = (),
        emit_start: bool = True,
        start_message: str | None = None,
    ):
        """Wrap a unit of work so that exactly one terminal event is recorded."""
        refs = list(input_refs)
        if emit_start:
            self._emit(
                node=node,
                event_type=event_type,
                status=AuditStatus.STARTED,
                message=start_message or f"{node} started",
                input_refs=refs,
            )
        started = time.perf_counter()
        step = AuditStep(self, node, event_type, refs)
        try:
            yield step
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            self._emit(
                node=node,
                event_type=event_type,
                status=AuditStatus.FAILED,
                message=f"{node} failed: {type(exc).__name__}: {exc}",
                input_refs=refs,
                duration_ms=duration,
            )
            raise
        duration = int((time.perf_counter() - started) * 1000)
        step._finalize(duration)

    def drain(self) -> list[AuditEvent]:
        """Return events recorded since the last drain (for the graph state)."""
        drained = self.events[self._drained :]
        self._drained = len(self.events)
        return list(drained)

    # -- views ------------------------------------------------------------- #
    def ordered(self) -> list[AuditEvent]:
        return sorted(self.events, key=lambda e: e.sequence)

    def timeline(self, *, include_started: bool = False) -> list[str]:
        return [
            e.render() for e in self.ordered() if include_started or e.status != AuditStatus.STARTED
        ]
