"""RFQ Builder: deterministic commercial data + optional LLM prose.

An RFQ is never sent by this system. In demo mode it is ``simulation_ready``;
in live mode it stops at ``ready_for_human_review``.
"""

from __future__ import annotations

from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.ids import stable_id, utcnow
from sye.domain.models import (
    RFQ,
    DemandBucket,
    ProductCandidate,
    RFQCopy,
    SupplierCandidate,
)
from sye.integrations.llm import LLMProvider, LLMUnavailable
from sye.services.bucketing import requirement_summary

SYSTEM_PROMPT = """You write the summary paragraph of a request for quote sent on behalf of
a group of individual buyers who have pooled their demand.

Be factual and businesslike. State the product, the number of units, the target unit price
and the delivery/warranty expectations exactly as given. Do not promise anything that is not
in the data, and do not invent a company name."""


async def build_rfq(
    bucket: DemandBucket,
    products: list[ProductCandidate],
    suppliers: list[SupplierCandidate],
    *,
    config: DemoConfig,
    run_id: str,
    llm: LLMProvider | None = None,
) -> tuple[RFQ, list[str]]:
    warnings: list[str] = []
    quantity = max(bucket.demand_quantity, 1)
    target = bucket.target_price or bucket.price_ceiling
    primary = products[0] if products else None

    requested_terms = {
        "market": config.market,
        "requested_quantity": quantity,
        "max_unit_price": float(bucket.price_ceiling) if bucket.price_ceiling else None,
        "delivery_days_max": 21,
        "warranty_months_min": 24,
        "returns_days_min": 14,
        "offer_valid_days": 14,
        "requirements": requirement_summary(bucket),
        "acceptable_equivalents": [p.canonical_name for p in products[1:4]],
    }

    summary = (
        f"Aggregated demand of {quantity} units for "
        f"{primary.canonical_name if primary else bucket.label} on behalf of "
        f"{len(bucket.member_user_ids)} individual buyers in market {config.market}. "
        f"Target unit price {target} {bucket.currency}"
        + (
            f", maximum {bucket.price_ceiling} {bucket.currency}. "
            if bucket.price_ceiling
            else ". "
        )
        + "Requested terms: delivery within 21 days, at least 24 months warranty, "
        "14-day returns, quote valid 14 days. Equivalent models meeting the same "
        "requirements are acceptable."
    )
    authored_by = "deterministic"

    if llm is not None:
        try:
            copy = await llm.structured(
                schema=RFQCopy,
                system=SYSTEM_PROMPT,
                user=(
                    f"Product: {primary.canonical_name if primary else bucket.label}\n"
                    f"Units: {quantity}\nBuyers: {len(bucket.member_user_ids)}\n"
                    f"Target unit price: {target} {bucket.currency}\n"
                    f"Maximum unit price: {bucket.price_ceiling} {bucket.currency}\n"
                    f"Requirements: {'; '.join(requirement_summary(bucket))}\n"
                    f"Requested terms: {requested_terms}"
                ),
                task="rfq_copy",
            )
            summary = copy.summary.strip() or summary
            authored_by = f"llm:{llm.name}"
        except LLMUnavailable as exc:
            warnings.append(f"RFQ copy stayed deterministic: {exc}")

    requested_terms["summary_authored_by"] = authored_by

    rfq = RFQ(
        rfq_id=stable_id("rfq", run_id, bucket.bucket_id),
        bucket_id=bucket.bucket_id,
        product_ids=[p.product_id for p in products],
        supplier_ids=[s.supplier_id for s in suppliers],
        quantity=quantity,
        requested_currency=bucket.currency,
        requested_target_unit_price=Decimal(target) if target is not None else None,
        requested_terms=requested_terms,
        summary=summary,
        status="simulation_ready" if config.mode == "demo" else "ready_for_human_review",
        created_at=utcnow(),
    )
    return rfq, warnings
