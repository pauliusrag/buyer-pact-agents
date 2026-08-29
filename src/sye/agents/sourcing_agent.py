"""Supplier Sourcing Agent.

Owns the step between "we know what to buy" and "we can ask for a price": find
companies that could plausibly fulfil this aggregated order, and turn the group's
requirements into a structured request for quote.

Its judgement calls: which of the viable products are worth sourcing for, which
companies are plausible suppliers for this market and volume (and never claiming an
authorisation the evidence does not support), and what terms to ask for. It drafts
the RFQ; it never sends it.
"""

from __future__ import annotations

from pydantic import Field

from sye.agents.base import Agent, AgentResult
from sye.agents.tools.rfq_builder import build_rfq
from sye.agents.tools.supplier_researcher import research_suppliers as research_for_products
from sye.domain.enums import BucketStatus
from sye.domain.models import (
    RFQ,
    BucketOutcome,
    DemandBucket,
    ProductCandidate,
    ProductMatch,
    SupplierCandidate,
)
from sye.services.matching import viable_matches

MAX_PRODUCTS_FOR_SUPPLIER_SEARCH = 2


class SupplierOutcome(AgentResult):
    suppliers: list[SupplierCandidate] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)


class RFQOutcome(AgentResult):
    rfqs: list[RFQ] = Field(default_factory=list)


class SourcingResult(AgentResult):
    suppliers: list[SupplierCandidate] = Field(default_factory=list)
    rfqs: list[RFQ] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)


class SourcingAgent(Agent):
    name = "sourcing_agent"

    async def research_suppliers(
        self,
        buckets: list[DemandBucket],
        products: list[ProductCandidate],
        matches: list[ProductMatch],
        *,
        skip_buckets: set[str] | None = None,
    ) -> SupplierOutcome:
        client = self.ctx.require_research()
        by_id = {p.product_id: p for p in products}
        skip = skip_buckets or set()

        async with self.audit.step(node="research_suppliers") as step:
            suppliers: list[SupplierCandidate] = []
            warnings: list[str] = []
            outcomes: list[BucketOutcome] = []

            for bucket in buckets:
                if bucket.bucket_id in skip:
                    continue
                ranked = viable_matches([m for m in matches if m.bucket_id == bucket.bucket_id])
                top_products = [
                    by_id[m.product_id]
                    for m in ranked[:MAX_PRODUCTS_FOR_SUPPLIER_SEARCH]
                    if m.product_id in by_id
                ]
                if not top_products:
                    continue

                result = await research_for_products(
                    bucket.bucket_id,
                    top_products,
                    client=client,
                    config=self.config,
                    run_id=self.run_id,
                )
                warnings.extend(result.warnings)
                suppliers.extend(result.suppliers)

                if not result.suppliers:
                    outcomes.append(
                        BucketOutcome(
                            bucket_id=bucket.bucket_id,
                            status=BucketStatus.NO_SUPPLIER,
                            reason="no plausible supplier found; product research preserved",
                        )
                    )
                    self.audit.warning(
                        node="research_suppliers",
                        message=f"No suppliers found for {bucket.bucket_id}",
                        input_refs=[bucket.bucket_id],
                    )
                    continue

                self.audit.event(
                    node="research_suppliers",
                    event_type="suppliers_found",
                    message=(
                        f"Found {len(result.suppliers)} plausible supplier(s) for "
                        f"{bucket.bucket_id}: "
                        f"{', '.join(s.name for s in result.suppliers)}"
                    ),
                    input_refs=[p.product_id for p in top_products],
                    output_refs=[s.supplier_id for s in result.suppliers],
                    sources=result.sources[:5],
                    metadata={"provider": client.name},
                )

            step.complete(
                message=f"Researched {len(suppliers)} supplier(s)",
                output_refs=[s.supplier_id for s in suppliers],
            )

        return SupplierOutcome(
            agent=self.name, suppliers=suppliers, outcomes=outcomes, warnings=warnings
        )

    async def build_rfqs(
        self,
        buckets: list[DemandBucket],
        products: list[ProductCandidate],
        matches: list[ProductMatch],
        suppliers: list[SupplierCandidate],
        *,
        skip_buckets: set[str] | None = None,
    ) -> RFQOutcome:
        by_id = {p.product_id: p for p in products}
        skip = skip_buckets or set()

        async with self.audit.step(node="build_rfqs") as step:
            rfqs: list[RFQ] = []
            warnings: list[str] = []

            for bucket in buckets:
                if bucket.bucket_id in skip:
                    continue
                ranked = viable_matches([m for m in matches if m.bucket_id == bucket.bucket_id])
                bucket_products = [by_id[m.product_id] for m in ranked if m.product_id in by_id]
                bucket_suppliers = [s for s in suppliers if s.bucket_id == bucket.bucket_id]
                if not bucket_products or not bucket_suppliers:
                    continue

                rfq, rfq_warnings = await build_rfq(
                    bucket,
                    bucket_products,
                    bucket_suppliers,
                    config=self.config,
                    run_id=self.run_id,
                    llm=self.llm,
                )
                warnings.extend(rfq_warnings)
                rfqs.append(rfq)
                self.audit.event(
                    node="build_rfqs",
                    event_type="rfq_built",
                    message=(
                        f"RFQ {rfq.rfq_id} for {rfq.quantity} units to "
                        f"{len(bucket_suppliers)} supplier(s) [{rfq.status}] — not sent"
                    ),
                    input_refs=[bucket.bucket_id],
                    output_refs=[rfq.rfq_id],
                    decision=rfq.summary,
                    metadata={
                        "target_unit_price": str(rfq.requested_target_unit_price),
                        "status": rfq.status,
                        "delivered": False,
                    },
                )

            step.complete(
                message=f"Built {len(rfqs)} RFQ(s); nothing was sent to any supplier",
                output_refs=[r.rfq_id for r in rfqs],
            )

        return RFQOutcome(agent=self.name, rfqs=rfqs, warnings=warnings)

    async def run(
        self,
        buckets: list[DemandBucket],
        products: list[ProductCandidate],
        matches: list[ProductMatch],
        *,
        skip_buckets: set[str] | None = None,
    ) -> SourcingResult:
        supplier_outcome = await self.research_suppliers(
            buckets, products, matches, skip_buckets=skip_buckets
        )
        rfq_outcome = await self.build_rfqs(
            buckets, products, matches, supplier_outcome.suppliers, skip_buckets=skip_buckets
        )
        return SourcingResult(
            agent=self.name,
            suppliers=supplier_outcome.suppliers,
            rfqs=rfq_outcome.rfqs,
            outcomes=supplier_outcome.outcomes,
            warnings=[*supplier_outcome.warnings, *rfq_outcome.warnings],
            metrics={
                "suppliers": len(supplier_outcome.suppliers),
                "rfqs": len(rfq_outcome.rfqs),
            },
        )
