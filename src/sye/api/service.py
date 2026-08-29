"""In-process run manager.

Runs can be executed synchronously (simplest for a demo client) or in the
background with a live SSE feed of public audit events. Finished runs are read
back from SQLite, so a restart never loses a run.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sye.config import Settings, get_settings
from sye.domain.enums import RunStatus
from sye.domain.events import AuditEvent
from sye.domain.ids import new_run_id
from sye.domain.models import PipelineRunExport
from sye.graph.context import build_context
from sye.graph.main_graph import run_pipeline
from sye.observability.audit import AuditLogger
from sye.observability.logging import get_logger
from sye.persistence.repositories import RunRepository
from sye.services.scenarios import parse_scenario

logger = get_logger("sye.api")


@dataclass
class RunHandle:
    run_id: str
    status: str = "running"
    export: PipelineRunExport | None = None
    error: str | None = None
    events: list[AuditEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None


class RunManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.repository = RunRepository(self.settings.db_url)
        self.handles: dict[str, RunHandle] = {}

    # -- execution --------------------------------------------------------- #
    async def start(
        self, scenario: dict[str, Any], *, background: bool, overrides: dict[str, Any]
    ) -> RunHandle:
        run_id = new_run_id()
        handle = RunHandle(run_id=run_id)
        self.handles[run_id] = handle

        base_config = self.settings.demo_config(**overrides)
        scenario_name, requests, config = parse_scenario(
            scenario, base_config=base_config, run_id=run_id
        )

        audit = AuditLogger(run_id)
        audit.add_sink(lambda event: self._publish(handle, event))
        ctx = build_context(run_id=run_id, config=config, settings=self.settings, audit=audit)

        async def execute() -> None:
            try:
                export, _ = await run_pipeline(
                    requests,
                    config=config,
                    settings=self.settings,
                    run_id=run_id,
                    scenario_name=scenario_name,
                    ctx=ctx,
                )
                handle.export = export
                handle.status = export.status.value
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                logger.exception("run %s failed", run_id)
                handle.status = RunStatus.FAILED.value
                handle.error = f"{type(exc).__name__}: {exc}"
            finally:
                self._close(handle)

        if background:
            handle.task = asyncio.create_task(execute())
        else:
            await execute()
        return handle

    # -- events ------------------------------------------------------------ #
    def _publish(self, handle: RunHandle, event: AuditEvent) -> None:
        handle.events.append(event)
        for queue in list(handle.subscribers):
            queue.put_nowait(event)

    def _close(self, handle: RunHandle) -> None:
        for queue in list(handle.subscribers):
            queue.put_nowait(None)

    async def stream(self, run_id: str) -> AsyncIterator[AuditEvent]:
        """Live events for a running run, or a replay for a finished one."""
        handle = self.handles.get(run_id)
        if handle is None:
            for event in self.repository.get_events(run_id):
                yield event
            return

        for event in list(handle.events):
            yield event
        if handle.status != "running":
            return

        queue: asyncio.Queue = asyncio.Queue()
        handle.subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            if queue in handle.subscribers:
                handle.subscribers.remove(queue)

    # -- reads ------------------------------------------------------------- #
    def get_export(self, run_id: str) -> PipelineRunExport | None:
        handle = self.handles.get(run_id)
        if handle is not None and handle.export is not None:
            return handle.export
        return self.repository.get_run(run_id)

    def get_events(self, run_id: str) -> list[AuditEvent]:
        export = self.get_export(run_id)
        if export is not None:
            return export.audit_events
        handle = self.handles.get(run_id)
        if handle is not None:
            return handle.events
        return self.repository.get_events(run_id)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.list_runs(limit=limit)

    def status(self, run_id: str) -> str | None:
        handle = self.handles.get(run_id)
        if handle is not None:
            return handle.status
        export = self.repository.get_run(run_id)
        return export.status.value if export else None


_MANAGER: RunManager | None = None


def get_run_manager() -> RunManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = RunManager()
    return _MANAGER


def reset_run_manager() -> None:
    global _MANAGER
    _MANAGER = None
