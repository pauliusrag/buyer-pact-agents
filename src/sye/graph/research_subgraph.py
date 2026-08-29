"""Graph nodes for grouping, market research and supplier discovery.

Adapters over :class:`~sye.agents.MarketResearchAgent` (bucketing, research,
matching) and :class:`~sye.agents.SourcingAgent` (suppliers).
"""

from __future__ import annotations

from typing import Any

from sye.agents import MarketResearchAgent, SourcingAgent
from sye.domain.enums import BucketStatus
from sye.domain.state import PipelineState
from sye.graph.context import RunContext


def _blocked_buckets(state: PipelineState) -> set[str]:
    return {o.bucket_id for o in state.get("bucket_outcomes", []) if o.status != BucketStatus.OPEN}


async def build_demand_buckets(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = MarketResearchAgent(ctx.agent_context())
    result = await agent.build_buckets(list(state.get("intents", [])))
    ctx.snapshots.write("buckets", result.buckets)
    return {
        "buckets": result.buckets,
        "bucket_memberships": result.memberships,
        "warnings": result.warnings,
        "audit_events": ctx.audit.drain(),
    }


async def research_products(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = MarketResearchAgent(ctx.agent_context())
    result = await agent.research_products(list(state.get("buckets", [])))
    ctx.snapshots.write("products", result.products)
    return {
        "products": result.products,
        "bucket_outcomes": result.outcomes,
        "warnings": result.warnings,
        "audit_events": ctx.audit.drain(),
    }


async def evaluate_matches(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = MarketResearchAgent(ctx.agent_context())
    result = await agent.evaluate_matches(
        list(state.get("buckets", [])), list(state.get("products", []))
    )
    ctx.snapshots.write("matches", result.matches)
    return {
        "matches": result.matches,
        "bucket_outcomes": result.outcomes,
        "warnings": result.warnings,
        "audit_events": ctx.audit.drain(),
    }


async def research_suppliers(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = SourcingAgent(ctx.agent_context())
    result = await agent.research_suppliers(
        list(state.get("buckets", [])),
        list(state.get("products", [])),
        list(state.get("matches", [])),
        skip_buckets=_blocked_buckets(state),
    )
    ctx.snapshots.write("suppliers", result.suppliers)
    return {
        "suppliers": result.suppliers,
        "bucket_outcomes": result.outcomes,
        "warnings": result.warnings,
        "audit_events": ctx.audit.drain(),
    }
