"""Campaign construction tool.

Every number is computed in Python from the winning offer; the LLM only writes
the title and the two descriptive sentences the storefront shows.
"""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.enums import DataOrigin
from sye.domain.ids import stable_id, utcnow
from sye.domain.models import (
    Campaign,
    CampaignCopy,
    DemandBucket,
    OfferEvaluation,
    ProductCandidate,
    ProductMatch,
    SupplierCandidate,
    SupplierOffer,
)
from sye.integrations.llm import LLMProvider, LLMUnavailable
from sye.services.bucketing import requirement_summary
from sye.services.simulation import money

SYSTEM_PROMPT = """You write the storefront copy for a group-buying campaign created from
pooled demand.

Return a short title (max 70 characters), a one-sentence description, and two sentences
explaining why this specific product was selected for this group. Use only the facts
provided. Do not state a price that was not given, do not invent specifications, and do not
imply the deal is confirmed with the supplier."""


async def build_campaign(
    *,
    bucket: DemandBucket,
    product: ProductCandidate,
    supplier: SupplierCandidate,
    offer: SupplierOffer,
    evaluation: OfferEvaluation,
    match: ProductMatch | None,
    config: DemoConfig,
    run_id: str,
    llm: LLMProvider | None = None,
) -> tuple[Campaign, list[str]]:
    warnings: list[str] = []

    group_price = money(evaluation.landed_unit_cost)
    reference = Decimal(product.normal_market_price) if product.normal_market_price else None
    discount_amount = money(reference - group_price) if reference else None
    discount_percent = (
        round(float(discount_amount / reference * 100), 2)
        if reference and discount_amount and reference > 0
        else None
    )

    starts = utcnow()
    ends = starts + timedelta(hours=config.campaign_duration_hours)
    committed = max(bucket.demand_quantity, 1)
    min_buyers = max(2, math.ceil(committed * config.min_buyers_ratio))

    terms = [
        f"Simulated unit price {offer.unit_price} {offer.currency} from {supplier.name}",
        f"Landed price per buyer {group_price} {offer.currency} (shipping included)",
        f"Estimated delivery {offer.estimated_delivery_days} days",
        f"Warranty {offer.warranty_months} months",
        offer.returns_policy_summary or "Returns policy not specified",
        f"Campaign closes {ends.date().isoformat()} or when {min_buyers} buyers commit",
        "Commercial terms are simulated for this demo and are not a supplier commitment",
    ]

    title = f"{product.canonical_name} group buy for {len(bucket.member_user_ids)} buyers"
    description = (
        f"{len(bucket.member_user_ids)} buyers pooled demand for {committed} units; "
        f"simulated group price {group_price} {offer.currency}"
        + (f" versus a {reference} {offer.currency} market price." if reference else ".")
    )
    why = (
        match.explanation
        if match is not None
        else f"{product.canonical_name} satisfies every binding requirement of this group."
    )

    if llm is not None:
        try:
            copy = await llm.structured(
                schema=CampaignCopy,
                system=SYSTEM_PROMPT,
                user=(
                    f"Product: {product.canonical_name}\n"
                    f"Buyers: {len(bucket.member_user_ids)}; units: {committed}\n"
                    f"Group price: {group_price} {offer.currency}\n"
                    f"Reference market price: {reference} {offer.currency}\n"
                    f"Discount: {discount_percent}%\n"
                    f"Requirements satisfied: {'; '.join(requirement_summary(bucket))}\n"
                    f"Match explanation: {why}\n"
                    f"Supplier: {supplier.name} ({supplier.supplier_type})"
                ),
                task="campaign_copy",
            )
            title = copy.title.strip() or title
            description = copy.short_description.strip() or description
            why = copy.why_this_product.strip() or why
        except LLMUnavailable as exc:
            warnings.append(f"campaign copy stayed deterministic: {exc}")

    campaign = Campaign(
        campaign_id=stable_id("cmp", run_id, bucket.bucket_id),
        bucket_id=bucket.bucket_id,
        winning_offer_id=offer.offer_id,
        product_id=product.product_id,
        supplier_id=supplier.supplier_id,
        title=title[:120],
        short_description=description,
        why_this_product=why,
        currency=offer.currency,
        normal_market_price=reference,
        group_price=group_price,
        discount_amount=discount_amount,
        discount_percent=discount_percent,
        committed_demand=committed,
        min_buyers=min_buyers,
        max_buyers=offer.max_quantity,
        starts_at=starts,
        ends_at=ends,
        terms_summary=terms,
        requirement_match_summary=requirement_summary(bucket),
        member_user_ids=list(bucket.member_user_ids),
        sources=list(product.sources),
        status="simulation_ready" if config.mode == "demo" else "ready_for_review",
        data_origin=DataOrigin.SIMULATED,
        run_id=run_id,
    )
    return campaign, warnings
