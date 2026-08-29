"""Campaign Agent.

Owns the last step: turn a winning offer into something a storefront can render.

Its decisions: which offer actually wins (deterministic selection over normalised
evaluations — an LLM never picks the winner), whether a bucket has a deal worth
publishing at all, and how to state the requirement match honestly. It writes the
campaign copy, and every number in it is computed, not generated.
"""

from __future__ import annotations

from pydantic import Field

from sye.agents.base import Agent, AgentResult
from sye.agents.tools.campaign_builder import build_campaign
from sye.domain.enums import BucketStatus
from sye.domain.models import (
    BucketOutcome,
    Campaign,
    DemandBucket,
    OfferEvaluation,
    ProductCandidate,
    ProductMatch,
    SupplierCandidate,
    SupplierOffer,
)
from sye.services.matching import viable_matches
from sye.services.offer_normalizer import best_evaluation


class CampaignResult(AgentResult):
    campaigns: list[Campaign] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)


class CampaignAgent(Agent):
    name = "campaign_agent"

    async def approval_gate(self, mode: str) -> AgentResult:
        """Human-in-the-loop seam.

        Demo mode passes straight through — nothing leaves the process. In live mode
        this is where a LangGraph ``interrupt`` pauses the run for human approval
        before any real-world side effect.
        """
        async with self.audit.step(node="approval_gate", emit_start=False) as step:
            if mode == "demo":
                step.complete(
                    message="Approval gate bypassed in demo mode (no real-world side effects)",
                    metadata={"mode": mode, "interrupt": False},
                )
            else:
                step.warn(
                    "Live mode reached the approval gate: campaign publication requires "
                    "human review before any supplier is contacted",
                    metadata={"mode": mode, "interrupt": True},
                )
        return AgentResult(agent=self.name)

    async def run(
        self,
        buckets: list[DemandBucket],
        matches: list[ProductMatch],
        products: list[ProductCandidate],
        suppliers: list[SupplierCandidate],
        offers: list[SupplierOffer],
        evaluations: list[OfferEvaluation],
    ) -> CampaignResult:
        offer_by_id = {o.offer_id: o for o in offers}
        product_by_id = {p.product_id: p for p in products}
        supplier_by_id = {s.supplier_id: s for s in suppliers}

        async with self.audit.step(node="build_campaigns") as step:
            campaigns: list[Campaign] = []
            outcomes: list[BucketOutcome] = []
            warnings: list[str] = []

            for bucket in buckets:
                bucket_evaluations = [e for e in evaluations if e.bucket_id == bucket.bucket_id]
                if not bucket_evaluations:
                    continue

                winner = best_evaluation(bucket_evaluations, require_qualifying=True)
                if winner is None:
                    fallback = best_evaluation(bucket_evaluations, require_qualifying=False)
                    reason = (
                        "; ".join(fallback.disqualification_reasons)
                        if fallback
                        else "no offer qualified"
                    )
                    outcomes.append(
                        BucketOutcome(
                            bucket_id=bucket.bucket_id,
                            status=BucketStatus.NO_QUALIFYING_OFFER,
                            reason=reason,
                        )
                    )
                    self.audit.warning(
                        node="build_campaigns",
                        message=f"No qualifying offer for {bucket.bucket_id}: {reason}",
                        input_refs=[bucket.bucket_id],
                    )
                    continue

                offer = offer_by_id.get(winner.offer_id)
                if offer is None:
                    continue
                product = product_by_id.get(offer.product_id)
                supplier = supplier_by_id.get(offer.supplier_id)
                if product is None or supplier is None:
                    warnings.append(f"winning offer {offer.offer_id} lost its product or supplier")
                    continue

                ranked = viable_matches([m for m in matches if m.bucket_id == bucket.bucket_id])
                match = next((m for m in ranked if m.product_id == product.product_id), None)

                campaign, campaign_warnings = await build_campaign(
                    bucket=bucket,
                    product=product,
                    supplier=supplier,
                    offer=offer,
                    evaluation=winner,
                    match=match,
                    config=self.config,
                    run_id=self.run_id,
                    llm=self.llm,
                )
                warnings.extend(campaign_warnings)
                campaigns.append(campaign)
                outcomes.append(
                    BucketOutcome(
                        bucket_id=bucket.bucket_id,
                        status=BucketStatus.CAMPAIGN_CREATED,
                        reason="winning simulated offer selected",
                        campaign_id=campaign.campaign_id,
                    )
                )
                self.audit.event(
                    node="build_campaigns",
                    event_type="campaign_created",
                    message=(
                        f"Campaign {campaign.campaign_id} created for "
                        f"{len(campaign.member_user_ids)} buyer(s): {product.canonical_name} "
                        f"at {campaign.group_price} {campaign.currency} "
                        f"({campaign.discount_percent}% below the reference market price)"
                    ),
                    input_refs=[bucket.bucket_id, offer.offer_id],
                    output_refs=[campaign.campaign_id],
                    decision=campaign.why_this_product,
                    sources=campaign.sources[:3],
                    metadata={
                        "data_origin": campaign.data_origin.value,
                        "status": campaign.status,
                        "simulated": True,
                    },
                )

            step.complete(
                message=f"Created {len(campaigns)} campaign(s)",
                output_refs=[c.campaign_id for c in campaigns],
            )

        return CampaignResult(
            agent=self.name,
            campaigns=campaigns,
            outcomes=outcomes,
            warnings=warnings,
            metrics={"campaigns": len(campaigns)},
        )
