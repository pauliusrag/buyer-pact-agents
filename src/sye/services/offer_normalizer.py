"""Offer normalisation and comparison.

Suppliers quote different shapes: unit price, shipping, delivery time, warranty,
conditions. We normalise to a landed unit cost first and only then compare, so a
cheap headline price with expensive shipping cannot win.

Weights come from :class:`DemoConfig` (default 60/20/10/10).
"""

from __future__ import annotations

from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.models import DemandBucket, OfferEvaluation, SupplierOffer
from sye.services import scoring
from sye.services.simulation import money


def landed_unit_cost(offer: SupplierOffer, quantity: int) -> Decimal:
    shipping = Decimal(offer.shipping_cost_total or 0)
    per_unit = shipping / Decimal(max(quantity, 1))
    return money(Decimal(offer.unit_price) + per_unit)


def price_score(cost: Decimal, ceiling: Decimal | None, reference: Decimal | None) -> float:
    """Absolute price score in [0, 1]: 0 at the price ceiling, 1 at half of it.

    Deliberately *not* relative to the other offers in the batch: offers arrive in
    successive negotiation rounds, and a score that depends on what else happened to
    be in the batch would let a stale, more expensive quote outrank a later, cheaper
    one from the same supplier.
    """
    anchor = ceiling or reference
    if anchor is None or anchor <= 0:
        return 0.5
    top = Decimal(anchor)
    bottom = top * Decimal("0.5")
    if cost >= top:
        return 0.0
    if cost <= bottom:
        return 1.0
    return round(float((top - cost) / (top - bottom)), 4)


def evaluate_offers(
    offers: list[SupplierOffer], bucket: DemandBucket, config: DemoConfig
) -> list[OfferEvaluation]:
    """Score offers against the bucket's ceiling and the requested terms."""
    if not offers:
        return []

    quantity = max(bucket.demand_quantity, 1)
    costs = {offer.offer_id: landed_unit_cost(offer, quantity) for offer in offers}
    reference = max(costs.values())
    weights = config.offer_weights

    evaluations: list[OfferEvaluation] = []
    for offer in sorted(offers, key=lambda o: o.offer_id):
        cost = costs[offer.offer_id]
        price = price_score(cost, bucket.price_ceiling, reference)
        fulfillment = round(
            0.6 * scoring.delivery_score(offer.estimated_delivery_days)
            + 0.4 * scoring.coverage_score(offer.max_quantity, quantity),
            4,
        )
        warranty = scoring.warranty_score(offer.warranty_months)
        terms = round(
            (0.6 if offer.returns_policy_summary else 0.0) + (0.4 if offer.expires_at else 0.0),
            4,
        )
        overall = round(
            weights["landed_cost"] * price
            + weights["fulfillment"] * fulfillment
            + weights["warranty"] * warranty
            + weights["terms"] * terms,
            4,
        )

        disqualifications: list[str] = []
        if bucket.price_ceiling is not None and cost > Decimal(bucket.price_ceiling):
            disqualifications.append(
                f"landed unit cost {cost} {bucket.currency} exceeds the group ceiling "
                f"{bucket.price_ceiling} {bucket.currency}"
            )
        if offer.max_quantity is not None and offer.max_quantity < quantity:
            disqualifications.append(
                f"supplier can only cover {offer.max_quantity} of {quantity} units"
            )

        evaluations.append(
            OfferEvaluation(
                offer_id=offer.offer_id,
                bucket_id=bucket.bucket_id,
                landed_unit_cost=cost,
                price_score=price,
                fulfillment_score=fulfillment,
                warranty_score=warranty,
                terms_score=terms,
                overall_score=overall,
                qualifies=not disqualifications,
                disqualification_reasons=disqualifications,
                negotiation_round=offer.negotiation_round,
            )
        )
    return evaluations


def best_evaluation(
    evaluations: list[OfferEvaluation], *, require_qualifying: bool = True
) -> OfferEvaluation | None:
    """Deterministic winner selection: qualifying offers first, then score, then cost."""
    pool = [e for e in evaluations if e.qualifies] if require_qualifying else list(evaluations)
    if not pool and require_qualifying:
        return None
    if not pool:
        return None
    return sorted(pool, key=lambda e: (-e.overall_score, e.landed_unit_cost, e.offer_id))[0]


def best_landed_cost(evaluations: list[OfferEvaluation]) -> Decimal | None:
    if not evaluations:
        return None
    return min(Decimal(e.landed_unit_cost) for e in evaluations)
