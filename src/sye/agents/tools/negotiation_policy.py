"""Negotiation policy tool.

The *policy* (accept / counter / reject and the counter price) is deterministic
Python using the aggregated demand and the best competing offer as leverage. The
LLM only writes the message and the rationale — and in demo mode that message is
never delivered anywhere.
"""

from __future__ import annotations

from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.models import (
    RFQ,
    DemandBucket,
    NegotiationAction,
    NegotiationCopy,
    OfferEvaluation,
    SupplierOffer,
)
from sye.integrations.llm import LLMProvider, LLMUnavailable
from sye.services.simulation import money

SYSTEM_PROMPT = """You write a short, professional negotiation message to a supplier on
behalf of a group of individual buyers who pooled their demand into one order.

Use only the facts given: number of units, current quoted price, the price we are asking
for, and the competing quote if one is provided. Be direct and polite, three sentences at
most. Never threaten, never invent a competing offer, never promise volumes that were not
given."""


def decide_action(
    *,
    offer: SupplierOffer,
    evaluation: OfferEvaluation,
    bucket: DemandBucket,
    best_competing_cost: Decimal | None,
    negotiation_round: int,
    config: DemoConfig,
) -> tuple[str, Decimal | None, str]:
    """Deterministic negotiation policy → ``(action, proposed_unit_price, reason)``."""
    quantity = max(bucket.demand_quantity, 1)
    shipping_per_unit = Decimal(offer.shipping_cost_total or 0) / Decimal(quantity)
    landed = Decimal(evaluation.landed_unit_cost)
    target = Decimal(bucket.target_price) if bucket.target_price is not None else None
    ceiling = Decimal(bucket.price_ceiling) if bucket.price_ceiling is not None else None

    if negotiation_round > config.max_negotiation_rounds + 1:
        if evaluation.qualifies:
            return "accept", None, "negotiation round cap reached; offer still qualifies"
        return "reject", None, "negotiation round cap reached without a qualifying offer"

    # A buying group always tests the market once; the ask is the most aggressive of
    # a fixed step down, the group target and the best competing quote.
    desired_candidates = [landed * Decimal("0.92")]
    if target is not None and target < landed:
        desired_candidates.append(target)
    if ceiling is not None and ceiling * Decimal("0.95") < landed:
        desired_candidates.append(ceiling * Decimal("0.95"))
    if best_competing_cost is not None and best_competing_cost < landed:
        desired_candidates.append(best_competing_cost * Decimal("0.97"))

    desired_landed = min(desired_candidates)
    proposed_unit = money(max(Decimal("1"), desired_landed - shipping_per_unit))
    if proposed_unit >= Decimal(offer.unit_price):
        return "accept", None, "no realistic improvement left to ask for"

    reason = (
        f"{quantity} pooled units justify a better unit price; asking {proposed_unit} "
        f"(landed {money(desired_landed)}) against the current landed cost {landed}"
    )
    if best_competing_cost is not None and best_competing_cost < landed:
        reason += f", with a competing landed quote at {best_competing_cost}"
    return "counter", proposed_unit, reason


async def negotiate(
    *,
    rfq: RFQ,
    bucket: DemandBucket,
    offer: SupplierOffer,
    evaluation: OfferEvaluation,
    best_competing_cost: Decimal | None,
    negotiation_round: int,
    config: DemoConfig,
    llm: LLMProvider | None = None,
) -> tuple[NegotiationAction, list[str]]:
    warnings: list[str] = []
    action, price, reason = decide_action(
        offer=offer,
        evaluation=evaluation,
        bucket=bucket,
        best_competing_cost=best_competing_cost,
        negotiation_round=negotiation_round,
        config=config,
    )

    message = ""
    authored_by = "deterministic"
    if action == "counter":
        message = (
            f"We represent {len(bucket.member_user_ids)} buyers who have pooled demand for "
            f"{rfq.quantity} units. Your current quote is {offer.unit_price} "
            f"{offer.currency} per unit; we can commit to the full volume at "
            f"{price} {offer.currency} per unit."
        )
        if best_competing_cost is not None:
            message += (
                f" A competing supplier is currently at {best_competing_cost} "
                f"{offer.currency} landed."
            )
        if llm is not None:
            try:
                copy = await llm.structured(
                    schema=NegotiationCopy,
                    system=SYSTEM_PROMPT,
                    user=(
                        f"Units: {rfq.quantity}\nBuyers: {len(bucket.member_user_ids)}\n"
                        f"Current quote: {offer.unit_price} {offer.currency}\n"
                        f"Shipping total: {offer.shipping_cost_total}\n"
                        f"Our ask: {price} {offer.currency}\n"
                        f"Best competing landed cost: {best_competing_cost}\n"
                        f"Round: {negotiation_round} of {config.max_negotiation_rounds}"
                    ),
                    task="negotiation_copy",
                )
                message = copy.supplier_message.strip() or message
                reason = copy.rationale_summary.strip() or reason
                authored_by = f"llm:{llm.name}"
            except LLMUnavailable as exc:
                warnings.append(f"negotiation copy stayed deterministic: {exc}")

    return (
        NegotiationAction(
            offer_id=offer.offer_id,
            supplier_id=offer.supplier_id,
            round=negotiation_round,
            action=action,  # type: ignore[arg-type]
            proposed_unit_price=price,
            requested_term_changes=(
                {"unit_price": float(price), "quantity": rfq.quantity} if price else {}
            ),
            supplier_message=message,
            rationale_summary=reason,
            delivered=False,  # demo mode never sends anything
            authored_by=authored_by,
        ),
        warnings,
    )
