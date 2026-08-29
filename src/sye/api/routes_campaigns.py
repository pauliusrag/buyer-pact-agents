"""Campaign endpoints — what a storefront renders."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from sye.api.service import RunManager, get_run_manager

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


@router.get("", summary="List campaigns from every stored run")
async def list_campaigns(
    limit: int = 100, manager: RunManager = Depends(get_run_manager)
) -> list[dict[str, Any]]:
    return manager.repository.list_campaigns(limit=limit)


@router.get("/{campaign_id}", summary="Get one campaign")
async def get_campaign(
    campaign_id: str, manager: RunManager = Depends(get_run_manager)
) -> dict[str, Any]:
    campaign = manager.repository.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id} not found")
    return campaign
