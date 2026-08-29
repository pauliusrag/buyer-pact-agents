"""Graph nodes for the commercial phase.

Adapters over :class:`~sye.agents.SourcingAgent` (RFQ) and
:class:`~sye.agents.NegotiationAgent` (offers, normalisation, counter rounds).
The renegotiation *cycle* is a real edge in the graph; each pass calls the agent once.
"""

from __future__ import annotations

from typing import Any

from sye.agents import NegotiationAgent, SourcingAgent
from sye.domain.enums import BucketStatus
from sye.domain.state import PipelineState
from sye.graph.context import RunContext


def _blocked_buckets(state: PipelineState) -> set[str]:
    return {o.bucket_id for o in state.get("bucket_outcomes", []) if o.status != BucketStatus.OPEN}


async def build_rfqs(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = SourcingAgent(ctx.agent_context())
    result = await agent.build_rfqs(
        list(state.get("buckets", [])),
        list(state.get("products", [])),
        list(state.get("matches", [])),
        list(state.get("suppliers", [])),
        skip_buckets=_blocked_buckets(state),
    )
    ctx.snapshots.write("rfqs", result.rfqs)
    return {"rfqs": result.rfqs, "warnings": result.warnings, "audit_events": ctx.audit.drain()}


async def obtain_supplier_offers(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = NegotiationAgent(ctx.agent_context())
    result = await agent.collect_initial_offers(
        list(state.get("rfqs", [])),
        list(state.get("suppliers", [])),
        list(state.get("products", [])),
    )
    ctx.snapshots.write("offers_round_1", result.offers)
    return {
        "offers": result.offers,
        "bucket_outcomes": result.outcomes,
        "warnings": result.warnings,
        "audit_events": ctx.audit.drain(),
    }


async def normalize_and_compare_offers(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = NegotiationAgent(ctx.agent_context())
    result = await agent.normalize(
        list(state.get("buckets", [])),
        list(state.get("rfqs", [])),
        list(state.get("offers", [])),
        list(state.get("suppliers", [])),
        already_evaluated={e.offer_id for e in state.get("offer_evaluations", [])},
    )
    return {"offer_evaluations": result.evaluations, "audit_events": ctx.audit.drain()}


async def negotiate_again(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = NegotiationAgent(ctx.agent_context())
    current_round = int(state.get("active_negotiation_round", 1))
    result = await agent.counter_round(
        list(state.get("buckets", [])),
        list(state.get("rfqs", [])),
        list(state.get("suppliers", [])),
        list(state.get("products", [])),
        list(state.get("offers", [])),
        list(state.get("offer_evaluations", [])),
        current_round=current_round,
    )
    ctx.snapshots.write("offers_final", list(state.get("offers", [])) + result.offers)
    return {
        "offers": result.offers,
        "negotiation_actions": result.actions,
        "active_negotiation_round": result.round,
        "warnings": result.warnings,
        "metrics": {**state.get("metrics", {}), "last_round_new_offers": len(result.offers)},
        "audit_events": ctx.audit.drain(),
    }
