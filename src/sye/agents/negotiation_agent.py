"""Negotiation Agent.

Owns the commercial phase: collect offers against an RFQ, normalise them so they
can honestly be compared, decide whether the best one is good enough, and if not,
decide what to counter with and against whom.

Its decisions: what to ask for (a deterministic policy using pooled volume and the
best competing quote as leverage), whether to accept, counter or walk away, and when
to stop pushing. The prose of the message is the only thing an LLM writes, and in
demo mode that message is drafted and never delivered.

The renegotiation *loop* lives in the graph as a real cycle; this agent supplies one
round at a time plus the policy the graph's conditional edge consults.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from sye.agents.base import Agent, AgentResult
from sye.agents.tools.negotiation_policy import negotiate
from sye.config import DemoConfig
from sye.domain.enums import BucketStatus
from sye.domain.models import (
    RFQ,
    BucketOutcome,
    DemandBucket,
    NegotiationAction,
    OfferEvaluation,
    ProductCandidate,
    SupplierCandidate,
    SupplierOffer,
)
from sye.integrations.supplier_gateway import SupplierOutreachNotImplemented
from sye.services.offer_normalizer import best_evaluation, evaluate_offers

MAX_SUPPLIERS_TO_NEGOTIATE = 3


class OfferRoundOutcome(AgentResult):
    offers: list[SupplierOffer] = Field(default_factory=list)
    actions: list[NegotiationAction] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)
    round: int = 1


class EvaluationOutcome(AgentResult):
    evaluations: list[OfferEvaluation] = Field(default_factory=list)


class NegotiationResult(AgentResult):
    offers: list[SupplierOffer] = Field(default_factory=list)
    evaluations: list[OfferEvaluation] = Field(default_factory=list)
    actions: list[NegotiationAction] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)
    rounds_run: int = 1


class NegotiationAgent(Agent):
    name = "negotiation_agent"

    # -- policy the graph's conditional edge consults ----------------------- #
    @staticmethod
    def should_continue(
        *,
        evaluations: list[OfferEvaluation],
        current_round: int,
        config: DemoConfig,
        last_round_new_offers: int | None,
    ) -> bool:
        """Counter again while rounds remain and the last round moved a price.

        The group always tests the market at least once: an opening quote is never
        treated as the supplier's final word.
        """
        max_rounds = getattr(config, "max_negotiation_rounds", 2)
        if current_round - 1 >= max_rounds:
            return False
        if not evaluations:
            return False
        # After the first counter, only continue if the last round actually moved a price.
        return not (current_round > 1 and not last_round_new_offers)

    # -- one round of offers ------------------------------------------------ #
    async def collect_initial_offers(
        self,
        rfqs: list[RFQ],
        suppliers: list[SupplierCandidate],
        products: list[ProductCandidate],
    ) -> OfferRoundOutcome:
        gateway = self.ctx.require_gateway({p.product_id: p for p in products})
        by_id = {s.supplier_id: s for s in suppliers}

        async with self.audit.step(node="obtain_supplier_offers") as step:
            offers: list[SupplierOffer] = []
            warnings: list[str] = []
            outcomes: list[BucketOutcome] = []

            for rfq in rfqs:
                rfq_offers: list[SupplierOffer] = []
                for supplier_id in rfq.supplier_ids[:MAX_SUPPLIERS_TO_NEGOTIATE]:
                    supplier = by_id.get(supplier_id)
                    if supplier is None:
                        continue
                    try:
                        rfq_offers.append(await gateway.request_offer(rfq, supplier))
                    except SupplierOutreachNotImplemented as exc:
                        warnings.append(str(exc))
                        self.audit.warning(
                            node="obtain_supplier_offers",
                            message=str(exc),
                            input_refs=[rfq.rfq_id, supplier_id],
                        )
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"offer generation failed for {supplier.name}: {exc}")

                offers.extend(rfq_offers)
                if not rfq_offers:
                    outcomes.append(
                        BucketOutcome(
                            bucket_id=rfq.bucket_id,
                            status=BucketStatus.NO_QUALIFYING_OFFER,
                            reason="no supplier produced an offer",
                        )
                    )
                    continue

                self.audit.event(
                    node="obtain_supplier_offers",
                    event_type="offers_received",
                    message=(
                        f"Simulated {len(rfq_offers)} initial offer(s) for {rfq.bucket_id}: "
                        + ", ".join(
                            f"{by_id[o.supplier_id].name} {o.unit_price} {o.currency}"
                            for o in rfq_offers
                        )
                    ),
                    input_refs=[rfq.rfq_id],
                    output_refs=[o.offer_id for o in rfq_offers],
                    metadata={"gateway": gateway.name, "data_origin": "simulated"},
                )

            step.complete(
                message=f"Collected {len(offers)} simulated offer(s)",
                output_refs=[o.offer_id for o in offers],
                metadata={"gateway": gateway.name},
            )

        return OfferRoundOutcome(
            agent=self.name, offers=offers, outcomes=outcomes, warnings=warnings, round=1
        )

    # -- normalisation ------------------------------------------------------ #
    async def normalize(
        self,
        buckets: list[DemandBucket],
        rfqs: list[RFQ],
        offers: list[SupplierOffer],
        suppliers: list[SupplierCandidate],
        *,
        already_evaluated: set[str] | None = None,
    ) -> EvaluationOutcome:
        """Score every new offer on landed cost and terms, then report the leader."""
        seen = already_evaluated or set()
        rfq_by_id = {r.rfq_id: r for r in rfqs}
        supplier_by_id = {s.supplier_id: s for s in suppliers}

        async with self.audit.step(node="normalize_and_compare_offers") as step:
            fresh: list[OfferEvaluation] = []
            for bucket in buckets:
                bucket_offers = [
                    o
                    for o in offers
                    if rfq_by_id.get(o.rfq_id) is not None
                    and rfq_by_id[o.rfq_id].bucket_id == bucket.bucket_id
                ]
                if not bucket_offers:
                    continue

                evaluations = evaluate_offers(bucket_offers, bucket, self.config)
                new = [e for e in evaluations if e.offer_id not in seen]
                fresh.extend(new)
                if not new:
                    continue

                best = best_evaluation(evaluations, require_qualifying=False)
                if best is None:
                    continue
                offer = next(o for o in bucket_offers if o.offer_id == best.offer_id)
                supplier = supplier_by_id.get(offer.supplier_id)
                self.audit.event(
                    node="normalize_and_compare_offers",
                    event_type="offers_compared",
                    message=(
                        f"Best normalized offer for {bucket.bucket_id}: "
                        f"{best.landed_unit_cost} {offer.currency} landed from "
                        f"{supplier.name if supplier else offer.supplier_id} "
                        f"(round {offer.negotiation_round})"
                    ),
                    input_refs=[o.offer_id for o in bucket_offers],
                    output_refs=[best.offer_id],
                    confidence=best.overall_score,
                    metadata={
                        "qualifies": best.qualifies,
                        "disqualification_reasons": best.disqualification_reasons,
                        "weights": self.config.offer_weights,
                    },
                )

            step.complete(
                message=f"Normalized and compared {len(fresh)} new offer(s)",
                output_refs=[e.offer_id for e in fresh],
            )

        return EvaluationOutcome(agent=self.name, evaluations=fresh)

    # -- one counter round --------------------------------------------------- #
    async def counter_round(
        self,
        buckets: list[DemandBucket],
        rfqs: list[RFQ],
        suppliers: list[SupplierCandidate],
        products: list[ProductCandidate],
        offers: list[SupplierOffer],
        evaluations: list[OfferEvaluation],
        *,
        current_round: int,
    ) -> OfferRoundOutcome:
        """Draft one counter per supplier and record the simulated response."""
        gateway = self.ctx.require_gateway({p.product_id: p for p in products})
        bucket_by_id = {b.bucket_id: b for b in buckets}
        supplier_by_id = {s.supplier_id: s for s in suppliers}
        evaluation_by_offer = {e.offer_id: e for e in evaluations}
        next_round = current_round + 1

        async with self.audit.step(node="negotiate_again") as step:
            new_offers: list[SupplierOffer] = []
            actions: list[NegotiationAction] = []
            warnings: list[str] = []

            for rfq in rfqs:
                bucket = bucket_by_id.get(rfq.bucket_id)
                if bucket is None:
                    continue
                latest = [
                    o
                    for o in offers
                    if o.rfq_id == rfq.rfq_id and o.negotiation_round == current_round
                ]
                if not latest:
                    continue

                costs = [
                    Decimal(evaluation_by_offer[o.offer_id].landed_unit_cost)
                    for o in latest
                    if o.offer_id in evaluation_by_offer
                ]

                def supplier_name(offer: SupplierOffer) -> str:
                    supplier = supplier_by_id.get(offer.supplier_id)
                    return supplier.name if supplier else offer.supplier_id

                for offer in sorted(latest, key=lambda o: (supplier_name(o), o.offer_id)):
                    evaluation = evaluation_by_offer.get(offer.offer_id)
                    if evaluation is None:
                        continue
                    competing = [c for c in costs if c != Decimal(evaluation.landed_unit_cost)]
                    leverage = min(competing) if competing else None

                    action, action_warnings = await negotiate(
                        rfq=rfq,
                        bucket=bucket,
                        offer=offer,
                        evaluation=evaluation,
                        best_competing_cost=leverage,
                        negotiation_round=next_round,
                        config=self.config,
                        llm=self.llm,
                    )
                    warnings.extend(action_warnings)
                    actions.append(action)

                    supplier = supplier_by_id.get(offer.supplier_id)
                    if action.action != "counter" or supplier is None:
                        self.audit.event(
                            node="negotiate_again",
                            event_type="negotiation_action",
                            message=(
                                f"Round {next_round}: {action.action} for "
                                f"{supplier.name if supplier else offer.supplier_id} — "
                                f"{action.rationale_summary}"
                            ),
                            input_refs=[offer.offer_id],
                            metadata={"delivered": False, "authored_by": action.authored_by},
                        )
                        continue

                    try:
                        counter_offer = await gateway.submit_counter(rfq, supplier, offer, action)
                    except SupplierOutreachNotImplemented as exc:
                        warnings.append(str(exc))
                        self.audit.warning(
                            node="negotiate_again", message=str(exc), input_refs=[offer.offer_id]
                        )
                        continue
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"counter simulation failed for {supplier.name}: {exc}")
                        continue

                    delta = Decimal(offer.unit_price) - Decimal(counter_offer.unit_price)
                    if delta <= 0:
                        self.audit.event(
                            node="negotiate_again",
                            event_type="counter_offer",
                            message=(
                                f"Round {next_round}: {supplier.name} held firm at "
                                f"{offer.unit_price} {offer.currency}"
                            ),
                            input_refs=[offer.offer_id],
                            decision=action.rationale_summary,
                            metadata={"delivered": False, "outcome": "held"},
                        )
                        continue

                    new_offers.append(counter_offer)
                    self.audit.event(
                        node="negotiate_again",
                        event_type="counter_offer",
                        message=(
                            f"Round {next_round}: countered {supplier.name} at "
                            f"{action.proposed_unit_price} {offer.currency}; simulated "
                            f"response {counter_offer.unit_price} {counter_offer.currency} "
                            f"(-{delta})"
                        ),
                        input_refs=[offer.offer_id],
                        output_refs=[counter_offer.offer_id],
                        decision=action.rationale_summary,
                        metadata={
                            "delivered": False,
                            "message_drafted": bool(action.supplier_message),
                            "authored_by": action.authored_by,
                            "data_origin": "simulated",
                        },
                    )

            step.complete(
                message=(
                    f"Negotiation round {next_round}: {len(actions)} action(s), "
                    f"{len(new_offers)} improved offer(s)"
                ),
                output_refs=[o.offer_id for o in new_offers],
                metadata={"round": next_round, "delivered": False},
            )

        return OfferRoundOutcome(
            agent=self.name,
            offers=new_offers,
            actions=actions,
            warnings=warnings,
            round=next_round,
        )

    # -- whole phase --------------------------------------------------------- #
    async def run(
        self,
        buckets: list[DemandBucket],
        rfqs: list[RFQ],
        suppliers: list[SupplierCandidate],
        products: list[ProductCandidate],
    ) -> NegotiationResult:
        """The full loop, for standalone use. In the pipeline the graph owns the cycle."""
        initial = await self.collect_initial_offers(rfqs, suppliers, products)
        offers = list(initial.offers)
        actions = list(initial.actions)
        warnings = list(initial.warnings)

        evaluated = await self.normalize(buckets, rfqs, offers, suppliers)
        evaluations = list(evaluated.evaluations)

        current_round = 1
        last_new = None
        while self.should_continue(
            evaluations=evaluations,
            current_round=current_round,
            config=self.config,
            last_round_new_offers=last_new,
        ):
            counter = await self.counter_round(
                buckets,
                rfqs,
                suppliers,
                products,
                offers,
                evaluations,
                current_round=current_round,
            )
            offers.extend(counter.offers)
            actions.extend(counter.actions)
            warnings.extend(counter.warnings)
            last_new = len(counter.offers)
            current_round = counter.round

            evaluated = await self.normalize(
                buckets,
                rfqs,
                offers,
                suppliers,
                already_evaluated={e.offer_id for e in evaluations},
            )
            evaluations.extend(evaluated.evaluations)

        return NegotiationResult(
            agent=self.name,
            offers=offers,
            evaluations=evaluations,
            actions=actions,
            outcomes=initial.outcomes,
            rounds_run=current_round,
            warnings=warnings,
            metrics={"offers": len(offers), "rounds": current_round},
        )
