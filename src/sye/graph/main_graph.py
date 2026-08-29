"""The LangGraph pipeline.

    START → load_requests → parse_user_intents → validate_intents
          → build_demand_buckets → research_products → evaluate_matches
          → research_suppliers → build_rfqs → obtain_supplier_offers
          → normalize_and_compare_offers ⇄ negotiate_again
          → approval_gate → build_campaigns → finalize_export → END

Every arrow that can fail routes to ``finalize_export`` so a partial run still
produces a valid export.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from langgraph.graph import END, START, StateGraph

from sye.config import DemoConfig, Settings, configure_langsmith, get_settings
from sye.domain.ids import new_run_id
from sye.domain.models import PipelineRunExport, UserRequest
from sye.domain.state import PipelineState, initial_state
from sye.graph import campaign_subgraph as campaign_nodes
from sye.graph import negotiation_subgraph as negotiation_nodes
from sye.graph import research_subgraph as research_nodes
from sye.graph import routing
from sye.graph import user_intent_subgraph as intent_nodes
from sye.graph.context import RunContext, build_context
from sye.persistence.checkpointer import build_checkpointer
from sye.persistence.repositories import RunRepository
from sye.services.exports import lovable_payload
from sye.services.report import render_report

NodeFn = Callable[[PipelineState, RunContext], Awaitable[dict[str, Any]]]

NODES: dict[str, NodeFn] = {
    "load_requests": intent_nodes.load_requests,
    "parse_user_intents": intent_nodes.parse_user_intents,
    "validate_intents": intent_nodes.validate_intents,
    "build_demand_buckets": research_nodes.build_demand_buckets,
    "research_products": research_nodes.research_products,
    "evaluate_matches": research_nodes.evaluate_matches,
    "research_suppliers": research_nodes.research_suppliers,
    "build_rfqs": negotiation_nodes.build_rfqs,
    "obtain_supplier_offers": negotiation_nodes.obtain_supplier_offers,
    "normalize_and_compare_offers": negotiation_nodes.normalize_and_compare_offers,
    "negotiate_again": negotiation_nodes.negotiate_again,
    "approval_gate": campaign_nodes.approval_gate,
    "build_campaigns": campaign_nodes.build_campaigns,
    "finalize_export": campaign_nodes.finalize_export,
}


def _bind(name: str, fn: NodeFn, ctx: RunContext) -> Callable[[PipelineState], Any]:
    async def node(state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return await fn(state, ctx)
        finally:
            ctx.record_duration(name, int((time.perf_counter() - started) * 1000))

    node.__name__ = name
    return node


def build_graph(ctx: RunContext, *, checkpointer: Any | None = None):
    """Compile the real StateGraph. The workflow lives in edges, not in one node."""
    graph = StateGraph(PipelineState)

    for name, fn in NODES.items():
        graph.add_node(name, _bind(name, fn, ctx))

    graph.add_edge(START, "load_requests")
    graph.add_edge("load_requests", "parse_user_intents")
    graph.add_edge("parse_user_intents", "validate_intents")
    graph.add_edge("validate_intents", "build_demand_buckets")

    graph.add_conditional_edges(
        "build_demand_buckets",
        routing.after_bucketing,
        {"research_products": "research_products", "finalize_export": "finalize_export"},
    )
    graph.add_conditional_edges(
        "research_products",
        routing.after_product_research,
        {"evaluate_matches": "evaluate_matches", "finalize_export": "finalize_export"},
    )
    graph.add_conditional_edges(
        "evaluate_matches",
        routing.after_matching,
        {"research_suppliers": "research_suppliers", "finalize_export": "finalize_export"},
    )
    graph.add_conditional_edges(
        "research_suppliers",
        routing.after_supplier_research,
        {"build_rfqs": "build_rfqs", "finalize_export": "finalize_export"},
    )
    graph.add_conditional_edges(
        "build_rfqs",
        routing.after_rfqs,
        {
            "obtain_supplier_offers": "obtain_supplier_offers",
            "finalize_export": "finalize_export",
        },
    )
    graph.add_edge("obtain_supplier_offers", "normalize_and_compare_offers")
    graph.add_conditional_edges(
        "normalize_and_compare_offers",
        routing.should_renegotiate,
        {"negotiate_again": "negotiate_again", "approval_gate": "approval_gate"},
    )
    graph.add_edge("negotiate_again", "normalize_and_compare_offers")
    graph.add_edge("approval_gate", "build_campaigns")
    graph.add_edge("build_campaigns", "finalize_export")
    graph.add_edge("finalize_export", END)

    return graph.compile(checkpointer=checkpointer)


async def run_pipeline(
    user_requests: list[UserRequest],
    *,
    config: DemoConfig,
    settings: Settings | None = None,
    run_id: str | None = None,
    scenario_name: str = "demo",
    ctx: RunContext | None = None,
    persist: bool = True,
    write_artifacts: bool = True,
) -> tuple[PipelineRunExport, RunContext]:
    """Run the whole pipeline once and return the canonical export."""
    settings = settings or get_settings()
    configure_langsmith(settings)
    run_id = run_id or new_run_id()

    repository = RunRepository(settings.db_url) if persist else None

    if ctx is None:
        ctx = build_context(run_id=run_id, config=config, settings=settings)
    if repository is not None:
        ctx.audit.add_sink(repository.append_event)

    state = initial_state(
        run_id=run_id, config=config, scenario_name=scenario_name, user_requests=user_requests
    )

    async with AsyncExitStack() as stack:
        checkpointer, kind = await build_checkpointer(
            stack, settings.data_dir / "checkpoints.sqlite"
        )
        app = build_graph(ctx, checkpointer=checkpointer)
        ctx.audit.event(
            node="run_pipeline",
            event_type="run_started",
            message=(
                f"Run {run_id} started in {config.mode} mode "
                f"({'offline fixtures' if config.offline else 'live research'}, "
                f"engine {ctx.engine}, checkpointer {kind}, seed {config.seed})"
            ),
            metadata={
                "offline": config.offline,
                "seed": config.seed,
                "checkpointer": kind,
                "research_provider": ctx.research.name,
            },
        )
        state["audit_events"] = ctx.audit.drain()

        final_state = await app.ainvoke(
            state, config={"configurable": {"thread_id": run_id}, "recursion_limit": 60}
        )

    export = campaign_nodes.finalize_run_export(final_state, ctx)

    if write_artifacts:
        ctx.snapshots.write("final", export)
        ctx.snapshots.write("audit", export.audit_events)
        ctx.snapshots.write("lovable", lovable_payload(export))
        ctx.snapshots.write("report", render_report(export))

    if repository is not None:
        repository.save_run(export)

    return export, ctx
