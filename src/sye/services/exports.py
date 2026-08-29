"""Export builders.

:class:`PipelineRunExport` is the single frontend contract — every demo view is
derivable from it. ``lovable_payload`` is the same data plus a few denormalised
convenience views, guaranteed to be plain JSON: no Decimals, datetimes, enums or
Python objects.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sye.domain.enums import BucketStatus, MatchClassification, RunMode, RunStatus
from sye.domain.models import PipelineRunExport


def build_export(
    state: dict[str, Any],
    *,
    status: RunStatus,
    started_at,
    completed_at=None,
    metrics: dict[str, Any] | None = None,
) -> PipelineRunExport:
    config = state.get("config")
    return PipelineRunExport(
        schema_version="1.0",
        run_id=state["run_id"],
        mode=RunMode(state.get("mode", "demo")),
        status=status,
        scenario_name=state.get("scenario_name"),
        market=getattr(config, "market", "SE"),
        currency=getattr(config, "currency", "EUR"),
        started_at=started_at,
        completed_at=completed_at,
        user_requests=list(state.get("user_requests", [])),
        intents=list(state.get("intents", [])),
        buckets=list(state.get("buckets", [])),
        bucket_memberships=list(state.get("bucket_memberships", [])),
        bucket_outcomes=list(state.get("bucket_outcomes", [])),
        products=list(state.get("products", [])),
        matches=list(state.get("matches", [])),
        suppliers=list(state.get("suppliers", [])),
        rfqs=list(state.get("rfqs", [])),
        offers=list(state.get("offers", [])),
        offer_evaluations=list(state.get("offer_evaluations", [])),
        negotiation_actions=list(state.get("negotiation_actions", [])),
        campaigns=list(state.get("campaigns", [])),
        audit_events=sorted(state.get("audit_events", []), key=lambda e: e.sequence),
        metrics=metrics or state.get("metrics", {}),
        warnings=list(dict.fromkeys(state.get("warnings", []))),
    )


def compute_metrics(
    state: dict[str, Any],
    *,
    duration_ms: int,
    linkup_calls: int,
    llm_calls: int,
    llm_failures: int,
    engine: str,
    node_durations: dict[str, int] | None = None,
) -> dict[str, Any]:
    matches = list(state.get("matches", []))
    evaluations = list(state.get("offer_evaluations", []))
    rejected = [m for m in matches if m.classification == MatchClassification.REJECTED]
    qualified = [m for m in matches if m.classification == MatchClassification.QUALIFIED]
    negotiable = [m for m in matches if m.classification == MatchClassification.NEGOTIABLE_GAP]

    round_one = [e for e in evaluations if e.negotiation_round == 1]
    final_round = max((e.negotiation_round for e in evaluations), default=1)
    last = [e for e in evaluations if e.negotiation_round == final_round]

    initial_best = min((Decimal(e.landed_unit_cost) for e in round_one), default=None)
    final_best = min((Decimal(e.landed_unit_cost) for e in evaluations), default=None)
    improvement = (
        round(float((initial_best - final_best) / initial_best * 100), 2)
        if initial_best and final_best and initial_best > 0
        else 0.0
    )

    campaigns = list(state.get("campaigns", []))
    return {
        "users": len(state.get("user_requests", [])),
        "intents": len(state.get("intents", [])),
        "demand_buckets": len(state.get("buckets", [])),
        "products_researched": len(state.get("products", [])),
        "products_qualified": len(qualified),
        "products_negotiable": len(negotiable),
        "products_rejected": len(rejected),
        "suppliers_researched": len(state.get("suppliers", [])),
        "rfqs": len(state.get("rfqs", [])),
        "simulated_offers": len(state.get("offers", [])),
        "negotiation_actions": len(state.get("negotiation_actions", [])),
        "negotiation_rounds": final_round,
        "offers_in_final_round": len(last),
        "campaigns_created": len(campaigns),
        "linkup_calls": linkup_calls,
        "llm_calls": llm_calls,
        "llm_failures": llm_failures,
        "reasoning_engine": engine,
        "initial_best_offer": float(initial_best) if initial_best is not None else None,
        "final_best_offer": float(final_best) if final_best is not None else None,
        "simulated_negotiation_improvement_percent": improvement,
        "total_simulated_group_value": float(
            sum(Decimal(c.group_price) * c.committed_demand for c in campaigns)
        )
        if campaigns
        else 0.0,
        "total_duration_ms": duration_ms,
        "node_durations_ms": node_durations or {},
    }


def to_json(export: PipelineRunExport) -> dict[str, Any]:
    """Plain-JSON dict: floats for money, ISO strings for datetimes, enum values."""
    return json.loads(export.model_dump_json())


def lovable_payload(export: PipelineRunExport) -> dict[str, Any]:
    """Frontend-safe projection: the full export plus denormalised views."""
    payload = to_json(export)

    products = {p.product_id: p for p in export.products}
    suppliers = {s.supplier_id: s for s in export.suppliers}
    offers = {o.offer_id: o for o in export.offers}
    buckets = {b.bucket_id: b for b in export.buckets}
    intents = {i.intent_id: i for i in export.intents}

    campaign_cards = []
    for campaign in export.campaigns:
        product = products.get(campaign.product_id)
        supplier = suppliers.get(campaign.supplier_id)
        offer = offers.get(campaign.winning_offer_id)
        bucket = buckets.get(campaign.bucket_id)
        campaign_cards.append(
            {
                "campaign_id": campaign.campaign_id,
                "title": campaign.title,
                "short_description": campaign.short_description,
                "why_this_product": campaign.why_this_product,
                "status": campaign.status,
                "data_origin": campaign.data_origin.value,
                "product": {
                    "product_id": campaign.product_id,
                    "name": product.canonical_name if product else None,
                    "brand": product.brand if product else None,
                    "attributes": product.attributes if product else {},
                    "listing_url": product.listing_url if product else None,
                    "data_origin": product.data_origin.value if product else None,
                },
                "supplier": {
                    "supplier_id": campaign.supplier_id,
                    "name": supplier.name if supplier else None,
                    "type": supplier.supplier_type if supplier else None,
                    "website": supplier.website if supplier else None,
                },
                "pricing": {
                    "currency": campaign.currency,
                    "group_price": float(campaign.group_price),
                    "normal_market_price": float(campaign.normal_market_price)
                    if campaign.normal_market_price
                    else None,
                    "discount_amount": float(campaign.discount_amount)
                    if campaign.discount_amount
                    else None,
                    "discount_percent": campaign.discount_percent,
                    "simulated": True,
                },
                "demand": {
                    "committed": campaign.committed_demand,
                    "min_buyers": campaign.min_buyers,
                    "max_buyers": campaign.max_buyers,
                    "member_user_ids": campaign.member_user_ids,
                },
                "delivery": {
                    "estimated_days": offer.estimated_delivery_days if offer else None,
                    "warranty_months": offer.warranty_months if offer else None,
                    "returns": offer.returns_policy_summary if offer else None,
                },
                "requirements": campaign.requirement_match_summary,
                "terms": campaign.terms_summary,
                "bucket_label": bucket.label if bucket else None,
                "sources": [s.url for s in campaign.sources],
                "starts_at": campaign.starts_at.isoformat(),
                "ends_at": campaign.ends_at.isoformat(),
            }
        )

    journeys = []
    for request in export.user_requests:
        intent = next((i for i in export.intents if i.user_id == request.user_id), None)
        membership = next(
            (m for m in export.bucket_memberships if m.user_id == request.user_id and m.joined),
            None,
        )
        bucket = buckets.get(membership.bucket_id) if membership else None
        campaign = next(
            (c for c in export.campaigns if bucket and c.bucket_id == bucket.bucket_id), None
        )
        journeys.append(
            {
                "user_id": request.user_id,
                "prompt": request.prompt,
                "intent_summary": intent.extraction_summary if intent else None,
                "hard_requirements": [c.key for c in (intent.hard_constraints() if intent else [])],
                "max_budget": float(intent.max_budget)
                if intent and intent.max_budget is not None
                else None,
                "bucket_id": bucket.bucket_id if bucket else None,
                "bucket_label": bucket.label if bucket else None,
                "bucket_explanation": membership.explanation if membership else None,
                "campaign_id": campaign.campaign_id if campaign else None,
                "outcome": "campaign" if campaign else "no_campaign",
            }
        )

    payload["views"] = {
        "campaign_cards": campaign_cards,
        "user_journeys": journeys,
        "timeline": [
            {
                "sequence": e.sequence,
                "node": e.node,
                "status": e.status.value,
                "message": e.message,
                "timestamp": e.timestamp.isoformat(),
                "duration_ms": e.duration_ms,
            }
            for e in export.audit_events
        ],
        "bucket_summaries": [
            {
                "bucket_id": b.bucket_id,
                "label": b.label,
                "members": b.member_user_ids,
                "demand_quantity": b.demand_quantity,
                "price_ceiling": float(b.price_ceiling) if b.price_ceiling else None,
                "status": _bucket_status(export, b.bucket_id),
                "explanation": b.compatibility_explanation,
                "requirements": [c.key for c in b.shared_hard_constraints],
                "intent_ids": [i for i in b.member_intent_ids if i in intents],
            }
            for b in export.buckets
        ],
    }
    return payload


def _bucket_status(export: PipelineRunExport, bucket_id: str) -> str:
    outcome = next((o for o in export.bucket_outcomes if o.bucket_id == bucket_id), None)
    if outcome:
        return outcome.status.value
    return BucketStatus.OPEN.value
