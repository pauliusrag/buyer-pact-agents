"""Graph nodes for the ingestion phase.

Thin adapters: they hand state to :class:`~sye.agents.IntentAgent` and merge its
typed result back. The reasoning lives in the agent.
"""

from __future__ import annotations

from typing import Any

from sye.agents import IntentAgent
from sye.domain.state import PipelineState
from sye.graph.context import RunContext


async def load_requests(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    requests = list(state.get("user_requests", []))
    async with ctx.audit.step(node="load_requests", input_refs=[ctx.run_id]) as step:
        step.complete(
            message=f"Loaded {len(requests)} user requests",
            output_refs=[r.request_id for r in requests],
            metadata={"scenario": state.get("scenario_name"), "mode": state.get("mode")},
        )
    ctx.snapshots.write("input", requests)
    return {"audit_events": ctx.audit.drain()}


async def parse_user_intents(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = IntentAgent(ctx.agent_context())
    result = await agent.ingest(list(state.get("user_requests", [])))
    ctx.snapshots.write("intents", result.intents)
    return {
        "intents": result.intents,
        "warnings": result.warnings,
        "audit_events": ctx.audit.drain(),
    }


async def validate_intents(state: PipelineState, ctx: RunContext) -> dict[str, Any]:
    agent = IntentAgent(ctx.agent_context())
    result = await agent.validate(list(state.get("intents", [])))
    return {"warnings": result.warnings, "audit_events": ctx.audit.drain()}
