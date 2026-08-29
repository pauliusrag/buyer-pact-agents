"""Run endpoints: export, events, live stream, report and Lovable payload."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from sye.api.schemas import RunSummary
from sye.api.service import RunManager, get_run_manager
from sye.domain.events import AuditEvent
from sye.domain.models import PipelineRunExport
from sye.services.exports import lovable_payload, to_json
from sye.services.report import render_report

router = APIRouter(prefix="/api/v1/demo/runs", tags=["runs"])


def _require(manager: RunManager, run_id: str) -> PipelineRunExport:
    export = manager.get_export(run_id)
    if export is None:
        status = manager.status(run_id)
        if status == "running":
            raise HTTPException(status_code=409, detail=f"run {run_id} is still running")
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return export


@router.get("", response_model=list[RunSummary], summary="List stored runs")
async def list_runs(
    limit: int = 50, manager: RunManager = Depends(get_run_manager)
) -> list[RunSummary]:
    rows = []
    for row in manager.list_runs(limit=limit):
        rows.append(
            RunSummary(
                run_id=row["run_id"],
                scenario_name=row["scenario_name"],
                mode=row["mode"],
                status=row["status"],
                started_at=row["started_at"].isoformat() if row["started_at"] else None,
                completed_at=row["completed_at"].isoformat() if row["completed_at"] else None,
                campaigns=row["campaigns"] or 0,
                warnings=row["warnings"] or 0,
            )
        )
    return rows


@router.get("/{run_id}", response_model=PipelineRunExport, summary="Get the whole run")
async def get_run(run_id: str, manager: RunManager = Depends(get_run_manager)) -> PipelineRunExport:
    return _require(manager, run_id)


@router.get("/{run_id}/events", response_model=list[AuditEvent], summary="Ordered audit events")
async def get_events(
    run_id: str, manager: RunManager = Depends(get_run_manager)
) -> list[AuditEvent]:
    events = manager.get_events(run_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"no events for run {run_id}")
    return sorted(events, key=lambda e: e.sequence)


@router.get("/{run_id}/export", summary="Canonical export (download friendly)")
async def get_export(run_id: str, manager: RunManager = Depends(get_run_manager)) -> JSONResponse:
    export = _require(manager, run_id)
    return JSONResponse(
        content=to_json(export),
        headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
    )


@router.get("/{run_id}/lovable", summary="Frontend-safe projection for the Lovable UI")
async def get_lovable(run_id: str, manager: RunManager = Depends(get_run_manager)) -> JSONResponse:
    return JSONResponse(content=lovable_payload(_require(manager, run_id)))


@router.get("/{run_id}/report", response_class=PlainTextResponse, summary="Markdown run report")
async def get_report(run_id: str, manager: RunManager = Depends(get_run_manager)) -> str:
    return render_report(_require(manager, run_id))


@router.get("/{run_id}/stream", summary="Server-sent stream of public audit events")
async def stream_events(run_id: str, manager: RunManager = Depends(get_run_manager)):
    async def generator():
        async for event in manager.stream(run_id):
            payload = json.loads(event.model_dump_json())
            yield f"event: audit\ndata: {json.dumps(payload)}\n\n"
        yield f"event: end\ndata: {json.dumps({'run_id': run_id})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
