"""Product research tool.

Turns a bucket's binding constraints into one precise research query, asks the
research client (Linkup live, fixtures offline) for candidates, then verifies the
finalists' specs from a second source when a critical spec is missing.

The *decision* to search again, and with what, belongs to
:class:`~sye.agents.MarketResearchAgent`; this module performs one search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.models import DemandBucket, EvidenceSource, ProductCandidate
from sye.domain.vocabulary import attribute_hint, category_noun
from sye.integrations.linkup_client import ResearchClient, ResearchError
from sye.services.constraints import describe


@dataclass
class ProductResearchResult:
    bucket_id: str
    products: list[ProductCandidate] = field(default_factory=list)
    sources: list[EvidenceSource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    query: str = ""
    verified_count: int = 0
    broadened: bool = False


def build_query(
    bucket: DemandBucket, config: DemoConfig, *, max_results: int, broadened: bool = False
) -> str:
    """A precise, self-contained research prompt for the bucket.

    ``broadened`` is the agent's second attempt: it keeps every technical hard
    requirement (those are what make the group a group) but drops the soft wishes and
    allows the negotiation headroom on price, because a first pass that returns
    nothing usually means the price line was drawn too tightly for retail.
    """
    hard = [describe(c) for c in bucket.shared_hard_constraints if c.key != "price.unit_price"]
    soft = [describe(c) for c in bucket.compatible_soft_constraints]

    if bucket.price_ceiling is None:
        price = "any price"
    elif broadened:
        limit = Decimal(bucket.price_ceiling) * Decimal(str(1 + config.price_negotiable_headroom))
        price = f"up to {limit.quantize(Decimal('1'))} {bucket.currency}"
    else:
        price = f"under {bucket.price_ceiling} {bucket.currency}"

    preferred = "none" if broadened or not soft else "; ".join(soft)
    return (
        f"Find {max_results} {category_noun(bucket.category)}s currently sold in market "
        f"{config.market} that satisfy ALL of: {'; '.join(hard) if hard else 'no hard limits'}. "
        f"Target price {price}, aggregated order of {bucket.demand_quantity} units. "
        f"Preferred but not required: {preferred}. "
        f"For each product return brand, model, canonical_name, normal retail price with "
        f"currency, merchant or listing name, listing url, availability and attributes. "
        f"Always report normal_market_price as a number in {bucket.currency} and set "
        f"currency to '{bucket.currency}'; convert if the listing is in another currency, "
        f"and omit the product rather than guessing if no price is available. "
        f"{attribute_hint(bucket.category)}."
    )


def needs_verification(product: ProductCandidate, bucket: DemandBucket) -> bool:
    """True when a *hard* spec is missing — unknown specs must not silently pass.

    A missing or foreign-currency price counts: web listings frequently omit the
    price or quote it in another market's currency, and an unpriced candidate is
    rejected just as firmly as one that fails a technical requirement.
    """
    if product.normal_market_price is None:
        return True
    if product.currency and bucket.currency and product.currency != bucket.currency:
        return True
    for constraint in bucket.shared_hard_constraints:
        if constraint.key == "price.unit_price":
            continue
        if product.attributes.get(constraint.key) is None:
            return True
    return False


async def research_products(
    bucket: DemandBucket,
    *,
    client: ResearchClient,
    config: DemoConfig,
    run_id: str,
    verify_finalists: int | None = None,
    broadened: bool = False,
) -> ProductResearchResult:
    result = ProductResearchResult(bucket_id=bucket.bucket_id, broadened=broadened)
    result.query = build_query(
        bucket, config, max_results=config.max_products_per_bucket, broadened=broadened
    )

    try:
        products = await client.search_products(
            query=result.query,
            category=bucket.category,
            market=config.market,
            max_results=config.max_products_per_bucket,
            run_id=run_id,
            bucket_id=bucket.bucket_id,
            constraints=bucket.shared_hard_constraints,
        )
    except ResearchError as exc:
        result.warnings.append(f"product research failed for {bucket.bucket_id}: {exc}")
        return result
    except Exception as exc:  # noqa: BLE001 - one bucket must not kill the run
        result.warnings.append(
            f"product research error for {bucket.bucket_id}: {type(exc).__name__}: {exc}"
        )
        return result

    verified: list[ProductCandidate] = []
    budget = (
        verify_finalists if verify_finalists is not None else config.max_verifications_per_bucket
    )
    for product in products:
        # Spend the verification budget on the candidates that actually need it,
        # in ranked order — not simply on the first few returned.
        if budget > 0 and needs_verification(product, bucket):
            try:
                product = await client.verify_product(product, run_id=run_id)
                result.verified_count += 1
                budget -= 1
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(
                    f"spec verification failed for {product.canonical_name}: {exc}"
                )
        verified.append(product)

    result.products = verified
    seen: set[str] = set()
    for product in verified:
        for source in product.sources:
            if source.url not in seen:
                seen.add(source.url)
                result.sources.append(source)
    return result
