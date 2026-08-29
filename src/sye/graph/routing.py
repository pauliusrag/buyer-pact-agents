"""Conditional edges.

A failed bucket never kills the run: routing checks whether *any* bucket can
still make progress, and the rest of the run completes as ``partial``.
"""

from __future__ import annotations

from sye.agents import NegotiationAgent
from sye.domain.enums import BucketStatus, MatchClassification
from sye.domain.state import PipelineState


def after_bucketing(state: PipelineState) -> str:
    return "research_products" if state.get("buckets") else "finalize_export"


def after_product_research(state: PipelineState) -> str:
    return "evaluate_matches" if state.get("products") else "finalize_export"


def after_matching(state: PipelineState) -> str:
    viable = [
        m for m in state.get("matches", []) if m.classification != MatchClassification.REJECTED
    ]
    return "research_suppliers" if viable else "finalize_export"


def after_supplier_research(state: PipelineState) -> str:
    open_buckets = {b.bucket_id for b in state.get("buckets", [])} - {
        o.bucket_id for o in state.get("bucket_outcomes", []) if o.status != BucketStatus.OPEN
    }
    has_suppliers = any(s.bucket_id in open_buckets for s in state.get("suppliers", []))
    return "build_rfqs" if has_suppliers else "finalize_export"


def after_rfqs(state: PipelineState) -> str:
    return "obtain_supplier_offers" if state.get("rfqs") else "finalize_export"


def should_renegotiate(state: PipelineState) -> str:
    """The renegotiation cycle. The *policy* belongs to the negotiation agent."""
    keep_going = NegotiationAgent.should_continue(
        evaluations=list(state.get("offer_evaluations", [])),
        current_round=int(state.get("active_negotiation_round", 1)),
        config=state.get("config"),
        last_round_new_offers=state.get("metrics", {}).get("last_round_new_offers"),
    )
    return "negotiate_again" if keep_going else "approval_gate"
