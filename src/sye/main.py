"""FastAPI application.

The API is a thin shell over the same objects the pipeline produces: the Lovable
frontend integrates by reading JSON, never by understanding LangGraph.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sye import __version__
from sye.api.routes_campaigns import router as campaigns_router
from sye.api.routes_demo import router as demo_router
from sye.api.routes_runs import router as runs_router
from sye.config import get_settings
from sye.domain.models import Campaign, PipelineRunExport
from sye.observability.logging import setup_logging
from sye.persistence.db import init_db

DESCRIPTION = """
Demand-first group buying: user requests → structured intents → compatible demand
buckets → researched products → deterministic match evaluation → suppliers →
simulated negotiation → campaigns.

All commercial terms produced in demo mode are **simulated**. Product and supplier
research may be live web data; every web-derived object keeps its sources.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    init_db(settings.db_url)

    app = FastAPI(
        title="SYE demand aggregation API",
        version=__version__,
        description=DESCRIPTION,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,  # explicit origins, never "*"
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(demo_router)
    app.include_router(runs_router)
    app.include_router(campaigns_router)

    @app.get("/health", tags=["meta"], summary="Liveness and capability probe")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "mode": settings.mode,
            "offline_default": settings.offline,
            "llm_configured": settings.has_llm_key,
            "linkup_configured": settings.has_linkup_key,
            "cors_origins": settings.cors_origin_list,
        }

    @app.get(
        "/api/v1/schema/pipeline-run",
        tags=["schema"],
        summary="JSON Schema of the run export (generate typed frontend interfaces)",
    )
    async def pipeline_run_schema() -> dict[str, Any]:
        return PipelineRunExport.model_json_schema()

    @app.get("/api/v1/schema/campaign", tags=["schema"], summary="JSON Schema of a campaign")
    async def campaign_schema() -> dict[str, Any]:
        return Campaign.model_json_schema()

    return app


app = create_app()
