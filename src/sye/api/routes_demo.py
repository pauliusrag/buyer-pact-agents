"""Demo endpoints: start a run, list and launch built-in scenarios."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from sye.api.schemas import RunAccepted, RunRequest, ScenarioInfo
from sye.api.service import RunManager, get_run_manager
from sye.domain.models import PipelineRunExport
from sye.services.exports import lovable_payload
from sye.services.scenarios import (
    ScenarioError,
    list_builtin,
    load_scenario_file,
    resolve_builtin,
)

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


def _respond(export: PipelineRunExport, fmt: str):
    """``?format=lovable`` returns the frontend projection in the same call.

    Saves a UI a second round trip: one button press, one response it can render.
    """
    if fmt == "lovable":
        return JSONResponse(content=lovable_payload(export))
    return export


def _overrides(request: RunRequest) -> dict[str, object]:
    return {
        k: v for k, v in {"seed": request.seed, "offline": request.offline}.items() if v is not None
    }


@router.post(
    "/runs",
    response_model=None,
    summary="Run a scenario end to end",
    description=(
        "Synchronous by default: the response is the complete PipelineRunExport. "
        "Set `background: true` to get a run_id immediately and follow /stream."
    ),
)
async def start_run(
    request: RunRequest,
    format: Literal["export", "lovable"] = "export",
    manager: RunManager = Depends(get_run_manager),
) -> PipelineRunExport | RunAccepted:
    if not request.users:
        raise HTTPException(status_code=422, detail="scenario contains no users")
    try:
        handle = await manager.start(
            request.to_scenario(), background=request.background, overrides=_overrides(request)
        )
    except ScenarioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if request.background:
        return RunAccepted(
            run_id=handle.run_id,
            status=handle.status,
            stream_url=f"/api/v1/demo/runs/{handle.run_id}/stream",
            export_url=f"/api/v1/demo/runs/{handle.run_id}/export",
        )
    if handle.export is None:
        raise HTTPException(status_code=500, detail=handle.error or "run produced no export")
    return _respond(handle.export, format)


@router.get("/scenarios", response_model=list[ScenarioInfo], summary="Built-in scenarios")
async def scenarios() -> list[ScenarioInfo]:
    return [ScenarioInfo(**row) for row in list_builtin()]


@router.post(
    "/scenarios/{key}/run",
    response_model=None,
    summary="Run a built-in scenario (one-click demo)",
)
async def run_builtin(
    key: str,
    background: bool = False,
    seed: int | None = None,
    offline: bool | None = None,
    format: Literal["export", "lovable"] = "export",
    manager: RunManager = Depends(get_run_manager),
) -> PipelineRunExport | RunAccepted:
    try:
        payload = load_scenario_file(resolve_builtin(key))
    except ScenarioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    overrides = {k: v for k, v in {"seed": seed, "offline": offline}.items() if v is not None}
    handle = await manager.start(payload, background=background, overrides=overrides)
    if background:
        return RunAccepted(
            run_id=handle.run_id,
            status=handle.status,
            stream_url=f"/api/v1/demo/runs/{handle.run_id}/stream",
            export_url=f"/api/v1/demo/runs/{handle.run_id}/export",
        )
    if handle.export is None:
        raise HTTPException(status_code=500, detail=handle.error or "run produced no export")
    return _respond(handle.export, format)
