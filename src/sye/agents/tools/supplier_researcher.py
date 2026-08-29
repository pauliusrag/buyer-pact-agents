"""Supplier Research Agent.

Finds plausible suppliers for the products that survived matching. Authorisation
is never claimed: ``authorization_claimed`` stays false unless evidence says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sye.config import DemoConfig
from sye.domain.models import EvidenceSource, ProductCandidate, SupplierCandidate
from sye.integrations.linkup_client import ResearchClient, ResearchError


@dataclass
class SupplierResearchResult:
    bucket_id: str
    suppliers: list[SupplierCandidate] = field(default_factory=list)
    sources: list[EvidenceSource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def research_suppliers(
    bucket_id: str,
    products: list[ProductCandidate],
    *,
    client: ResearchClient,
    config: DemoConfig,
    run_id: str,
) -> SupplierResearchResult:
    result = SupplierResearchResult(bucket_id=bucket_id)
    by_id: dict[str, SupplierCandidate] = {}

    for product in products:
        try:
            found = await client.search_suppliers(
                product=product,
                market=config.market,
                max_results=config.max_suppliers_per_product,
                run_id=run_id,
                bucket_id=bucket_id,
            )
        except ResearchError as exc:
            result.warnings.append(f"supplier research failed for {product.canonical_name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(
                f"supplier research error for {product.canonical_name}: {type(exc).__name__}: {exc}"
            )
            continue

        for supplier in found:
            existing = by_id.get(supplier.supplier_id)
            if existing is None:
                by_id[supplier.supplier_id] = supplier
            else:
                merged = sorted(set(existing.product_ids) | set(supplier.product_ids))
                existing.product_ids = merged

    result.suppliers = sorted(by_id.values(), key=lambda s: s.name)
    seen: set[str] = set()
    for supplier in result.suppliers:
        for source in supplier.evidence:
            if source.url not in seen:
                seen.add(source.url)
                result.sources.append(source)
    return result
