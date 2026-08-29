"""Graph nodes for approval, campaign creation and export finalisation.

Adapters over :class:`~sye.agents.CampaignAgent`, plus the run's own bookkeeping
(status, metrics, export) which belongs to the pipeline rather than to any agent.
"""

from __future__ import annotations

from typing import Any

from sye.agents import CampaignAgent
from sye.domain.enums import RunStatus
from sye.domain.state import PipelineState
from sye.graph.context import RunContext
from sye.services.exports import build_export, compute_metrics


async def approval_gate(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = CampaignAgent(ctx.agent_context())
    await agent.approval_gate(str(state.get("mode", "demo")))
    return {"audit_events": ctx.audit.drain()}


async def build_campaigns(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = CampaignAgent(ctx.agent_context())
    result = await agent.run(
        list(state.get("buckets", [])),
        list(state.get("matches", [])),
        list(state.get("products", [])),
        list(state.get("suppliers", [])),
        list(state.get("offers", [])),
        list(state.get("offer_evaluations", [])),
    )
    ctx.snapshots.write("campaigns", result.campaigns)
    return {
        "campaigns": result.campaigns,
        "bucket_outcomes": result.outcomes,
        "warnings": result.warnings,
        "audit_events": ctx.audit.drain(),
    }


def determine_status(state: PipelineState) -> RunStatus:
    """A run is ``completed`` only when every bucket reached a campaign."""
    buckets = list(state.get("buckets", []))
    campaigns = list(state.get("campaigns", []))
    if not buckets:
        return RunStatus.FAILED if not state.get("intents") else RunStatus.PARTIAL
    if not campaigns:
        return RunStatus.PARTIAL
    covered = {c.bucket_id for c in campaigns}
    if covered == {b.bucket_id for b in buckets}:
        return RunStatus.COMPLETED
    return RunStatus.PARTIAL


async def finalize_export(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    async with ctx.audit.step(node="finalize_export", emit_start=False) as step:
        status = determine_status(state)
        step.complete(
            message=(
                f"Run {ctx.run_id} finished with status {status.value}: "
                f"{len(state.get('campaigns', []))} campaign(s), "
                f"{len(state.get('warnings', []))} warning(s)"
            ),
            output_refs=[c.campaign_id for c in state.get("campaigns", [])],
            metadata={"status": status.value},
        )

    events = ctx.audit.drain()
    all_events = sorted(list(state.get("audit_events", [])) + events, key=lambda e: e.sequence)
    metrics = compute_metrics(
        {**state, "audit_events": all_events},
        duration_ms=int((ctx.audit.events[-1].timestamp - ctx.started_at).total_seconds() * 1000)
        if ctx.audit.events
        else 0,
        linkup_calls=ctx.research_calls,
        llm_calls=ctx.llm_calls,
        llm_failures=ctx.llm_failures,
        engine=ctx.engine,
        node_durations=ctx.node_durations,
    )
    return {"audit_events": events, "metrics": {**state.get("metrics", {}), **metrics}}


def finalize_run_export(state: PipelineState, ctx: RunContext):
    """Build the canonical export object from the finished state."""
    return build_export(
        dict(state),
        status=determine_status(state),
        started_at=ctx.started_at,
        completed_at=ctx.audit.events[-1].timestamp if ctx.audit.events else None,
        metrics=state.get("metrics", {}),
    )
