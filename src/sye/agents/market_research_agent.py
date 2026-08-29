"""Market Research & Group Bucketing Agent.

This agent owns everything between "we have individual requirements" and "we know
which products can serve this group":

1. **Group bucketing** — decide who can be served by a single product. Deterministic
   constraint algebra decides feasibility; the agent consults an LLM judge only in the
   narrow band where the deterministic answer is genuinely ambiguous.
2. **Market research** — turn each bucket's binding requirements into a web search,
   judge whether the returned evidence is good enough, verify thin specs from a second
   source, and *decide to search again with a broadened query* when nothing fits.
3. **Match evaluation** — check every candidate against every binding requirement and
   classify it: qualified, negotiable gap, or rejected (a missing spec never passes).

Its decisions — who was grouped with whom and why, what was searched for, why a
product was accepted or rejected — are the ones the demo has to be able to explain.
It knows nothing about suppliers, offers or negotiation; that is another agent's job.
"""

from __future__ import annotations

import asyncio

from pydantic import Field

from sye.agents.base import Agent, AgentResult
from sye.agents.tools.compatibility_judge import CompatibilityJudge
from sye.agents.tools.match_explainer import explain_match
from sye.agents.tools.product_researcher import research_products as research_for_bucket
from sye.domain.enums import BucketStatus, MatchClassification
from sye.domain.models import (
    BucketMembershipExplanation,
    BucketOutcome,
    DemandBucket,
    ProductCandidate,
    ProductMatch,
    UserIntent,
)
from sye.services.bucketing import build_buckets
from sye.services.matching import evaluate_bucket, viable_matches

MAX_EXPLANATIONS_PER_BUCKET = 2


class BucketingOutcome(AgentResult):
    buckets: list[DemandBucket] = Field(default_factory=list)
    memberships: list[BucketMembershipExplanation] = Field(default_factory=list)
    judge_calls: int = 0


class ProductResearchOutcome(AgentResult):
    products: list[ProductCandidate] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)
    queries: dict[str, list[str]] = Field(default_factory=dict)
    attempts: dict[str, int] = Field(default_factory=dict)


class MatchOutcome(AgentResult):
    matches: list[ProductMatch] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)


class MarketResearchResult(AgentResult):
    """Everything this agent knows once it has finished.

    This is the hand-off to the sourcing agent: buckets that have at least one
    viable product, and the ranked candidates for each.
    """

    buckets: list[DemandBucket] = Field(default_factory=list)
    memberships: list[BucketMembershipExplanation] = Field(default_factory=list)
    products: list[ProductCandidate] = Field(default_factory=list)
    matches: list[ProductMatch] = Field(default_factory=list)
    outcomes: list[BucketOutcome] = Field(default_factory=list)
    queries: dict[str, list[str]] = Field(default_factory=dict)
    """What the agent actually asked the web, per bucket — one entry per attempt."""

    def matches_for(self, bucket_id: str) -> list[ProductMatch]:
        """Viable candidates for one bucket, best first."""
        return viable_matches([m for m in self.matches if m.bucket_id == bucket_id])

    def best_match(self, bucket_id: str) -> ProductMatch | None:
        ranked = self.matches_for(bucket_id)
        return ranked[0] if ranked else None

    def campaign_ready_buckets(self) -> list[DemandBucket]:
        """Buckets a campaign could actually be built for."""
        return [b for b in self.buckets if self.matches_for(b.bucket_id)]

    def products_for(self, bucket_id: str) -> list[ProductCandidate]:
        by_id = {p.product_id: p for p in self.products}
        return [by_id[m.product_id] for m in self.matches_for(bucket_id) if m.product_id in by_id]


class MarketResearchAgent(Agent):
    """The group bucketing and market research agent."""

    name = "market_research_agent"

    # -- 1. bucketing ------------------------------------------------------ #
    async def build_buckets(self, intents: list[UserIntent]) -> BucketingOutcome:
        judge = CompatibilityJudge(self.llm)

        async with self.audit.step(
            node="build_demand_buckets", input_refs=[i.intent_id for i in intents]
        ) as step:
            result = await build_buckets(
                intents,
                config=self.config,
                run_id=self.run_id,
                judge=judge if self.llm else None,
            )
            for bucket in result.buckets:
                self.audit.event(
                    node="build_demand_buckets",
                    event_type="bucket_created",
                    message=(
                        f"Created bucket {bucket.bucket_id} '{bucket.label}' with "
                        f"{len(bucket.member_user_ids)} user(s): "
                        f"{', '.join(bucket.member_user_ids)}"
                    ),
                    input_refs=bucket.member_intent_ids,
                    output_refs=[bucket.bucket_id],
                    decision=bucket.compatibility_explanation,
                    confidence=bucket.compatibility_score,
                    metadata={
                        "requirements": [c.key for c in bucket.shared_hard_constraints],
                        "price_ceiling": str(bucket.price_ceiling),
                        "demand_quantity": bucket.demand_quantity,
                    },
                )
            step.complete(
                message=(
                    f"Grouped {len(intents)} intents into {len(result.buckets)} demand bucket(s)"
                ),
                output_refs=[b.bucket_id for b in result.buckets],
                metadata={"judge_calls": result.judge_calls, "decisions": result.decisions},
            )

        return BucketingOutcome(
            agent=self.name,
            buckets=result.buckets,
            memberships=result.memberships,
            judge_calls=result.judge_calls,
            warnings=judge.warnings,
        )

    # -- 2. market research ------------------------------------------------ #
    async def research_products(self, buckets: list[DemandBucket]) -> ProductResearchOutcome:
        """Research every bucket in parallel, retrying broadened where nothing fits."""
        client = self.ctx.require_research()

        async with self.audit.step(
            node="research_products", input_refs=[b.bucket_id for b in buckets]
        ) as step:
            results = await asyncio.gather(
                *(self._research_one(bucket) for bucket in buckets), return_exceptions=True
            )

            products: list[ProductCandidate] = []
            warnings: list[str] = []
            outcomes: list[BucketOutcome] = []
            queries: dict[str, list[str]] = {}
            attempts: dict[str, int] = {}

            for bucket, result in zip(buckets, results, strict=True):
                if isinstance(result, BaseException):
                    warnings.append(f"product research crashed for {bucket.bucket_id}: {result}")
                    outcomes.append(
                        BucketOutcome(
                            bucket_id=bucket.bucket_id,
                            status=BucketStatus.NO_VIABLE_PRODUCT,
                            reason=f"research error: {result}",
                        )
                    )
                    continue

                found, bucket_warnings, bucket_queries = result
                warnings.extend(bucket_warnings)
                products.extend(found)
                queries[bucket.bucket_id] = bucket_queries
                attempts[bucket.bucket_id] = len(bucket_queries)

                if not found:
                    outcomes.append(
                        BucketOutcome(
                            bucket_id=bucket.bucket_id,
                            status=BucketStatus.NO_VIABLE_PRODUCT,
                            reason="no candidate products were found",
                        )
                    )

            step.complete(
                message=(
                    f"Researched {len(products)} product candidate(s) across "
                    f"{len(buckets)} bucket(s)"
                ),
                output_refs=[p.product_id for p in products],
                metadata={
                    "research_calls": getattr(client, "calls", 0),
                    "provider": client.name,
                    "attempts": attempts,
                },
            )

        return ProductResearchOutcome(
            agent=self.name,
            products=products,
            outcomes=outcomes,
            warnings=warnings,
            queries=queries,
            attempts=attempts,
            metrics={"provider": client.name, "research_calls": getattr(client, "calls", 0)},
        )

    async def _research_one(
        self, bucket: DemandBucket
    ) -> tuple[list[ProductCandidate], list[str], list[str]]:
        """Search for one bucket, deciding for itself whether to search again.

        After each attempt the agent checks the candidates against the bucket's binding
        requirements. If none of them fit, and it is allowed another attempt, it
        broadens the query rather than reporting an empty result.
        """
        client = self.ctx.require_research()
        found: dict[str, ProductCandidate] = {}
        warnings: list[str] = []
        queries: list[str] = []

        for attempt in range(1, max(1, self.config.max_research_attempts) + 1):
            broadened = attempt > 1
            result = await research_for_bucket(
                bucket,
                client=client,
                config=self.config,
                run_id=self.run_id,
                broadened=broadened,
            )
            warnings.extend(result.warnings)
            queries.append(result.query)
            for product in result.products:
                found.setdefault(product.product_id, product)

            candidates = list(found.values())
            if candidates:
                self.audit.event(
                    node="research_products",
                    event_type="products_found",
                    message=(
                        f"{client.name} research returned {len(result.products)} candidate(s) "
                        f"for {bucket.bucket_id} ({bucket.label})"
                        + (" on a broadened second pass" if broadened else "")
                    ),
                    input_refs=[bucket.bucket_id],
                    output_refs=[p.product_id for p in result.products],
                    sources=result.sources[:5],
                    metadata={
                        "query": result.query,
                        "verified": result.verified_count,
                        "provider": client.name,
                        "attempt": attempt,
                        "broadened": broadened,
                    },
                )

            # Cheap deterministic self-check: does anything actually fit?
            fits = viable_matches(
                evaluate_bucket(bucket, candidates, self.config, run_id=self.run_id)
            )
            if fits or attempt >= max(1, self.config.max_research_attempts):
                if not fits and candidates:
                    self.audit.warning(
                        node="research_products",
                        message=(
                            f"No candidate satisfies {bucket.bucket_id} ({bucket.label}) after "
                            f"{attempt} search attempt(s); keeping the evidence for the report"
                        ),
                        input_refs=[bucket.bucket_id],
                        metadata={"queries": queries},
                    )
                break

            self.audit.event(
                node="research_products",
                event_type="research_retry",
                message=(
                    f"None of the {len(candidates)} candidate(s) fit {bucket.bucket_id} "
                    f"({bucket.label}); searching again with a broadened query"
                ),
                input_refs=[bucket.bucket_id],
                decision="drop soft preferences and allow the negotiation headroom on price",
                metadata={"attempt": attempt},
            )

        if not found:
            self.audit.warning(
                node="research_products",
                message=f"No candidates found for {bucket.bucket_id} ({bucket.label})",
                input_refs=[bucket.bucket_id],
            )

        return list(found.values()), warnings, queries

    # -- 3. match evaluation ------------------------------------------------ #
    async def evaluate_matches(
        self, buckets: list[DemandBucket], products: list[ProductCandidate]
    ) -> MatchOutcome:
        by_id = {p.product_id: p for p in products}

        async with self.audit.step(
            node="evaluate_matches", input_refs=[b.bucket_id for b in buckets]
        ) as step:
            all_matches: list[ProductMatch] = []
            outcomes: list[BucketOutcome] = []
            warnings: list[str] = []

            for bucket in buckets:
                candidates = [p for p in products if p.bucket_id == bucket.bucket_id]
                if not candidates:
                    continue

                matches = evaluate_bucket(bucket, candidates, self.config, run_id=self.run_id)
                explained: list[ProductMatch] = []
                for index, match in enumerate(matches):
                    if self.llm is not None and index < MAX_EXPLANATIONS_PER_BUCKET:
                        match, explain_warnings = await explain_match(
                            match, by_id[match.product_id], llm=self.llm, bucket_label=bucket.label
                        )
                        warnings.extend(explain_warnings)
                    explained.append(match)
                    self._record_match(bucket, match, by_id[match.product_id])

                all_matches.extend(explained)
                if not viable_matches(explained):
                    outcomes.append(
                        BucketOutcome(
                            bucket_id=bucket.bucket_id,
                            status=BucketStatus.NO_VIABLE_PRODUCT,
                            reason="every candidate failed at least one hard requirement",
                        )
                    )

            viable = [m for m in all_matches if m.classification != MatchClassification.REJECTED]
            step.complete(
                message=(
                    f"Evaluated {len(all_matches)} candidate(s): {len(viable)} viable, "
                    f"{len(all_matches) - len(viable)} rejected"
                ),
                output_refs=[m.match_id for m in all_matches],
            )

        return MatchOutcome(
            agent=self.name, matches=all_matches, outcomes=outcomes, warnings=warnings
        )

    def _record_match(
        self, bucket: DemandBucket, match: ProductMatch, product: ProductCandidate
    ) -> None:
        passed = sum(1 for e in match.hard_constraint_results if e.result.value == "pass")
        total = len(match.hard_constraint_results)
        if match.classification == MatchClassification.REJECTED:
            message = (
                f"Candidate {product.canonical_name} rejected: "
                f"{match.rejection_reasons[0] if match.rejection_reasons else 'unknown'}"
            )
        else:
            message = (
                f"Candidate {product.canonical_name} passed {passed}/{total} hard "
                f"constraints ({match.classification.value})"
            )
        self.audit.event(
            node="evaluate_matches",
            event_type="match_evaluated",
            message=message,
            input_refs=[bucket.bucket_id, product.product_id],
            output_refs=[match.match_id],
            decision=match.explanation,
            confidence=match.overall_score,
            sources=product.sources[:2],
            metadata={
                "classification": match.classification.value,
                "unknown_specs": match.unknown_specs,
                "negotiable_gaps": match.negotiable_gaps,
            },
        )

    # -- whole phase -------------------------------------------------------- #
    async def run(self, intents: list[UserIntent]) -> MarketResearchResult:
        """Intents in, grouped demand plus researched and judged candidates out."""
        bucketing = await self.build_buckets(intents)
        research = await self.research_products(bucketing.buckets)
        evaluation = await self.evaluate_matches(bucketing.buckets, research.products)

        result = MarketResearchResult(
            agent=self.name,
            buckets=bucketing.buckets,
            memberships=bucketing.memberships,
            products=research.products,
            matches=evaluation.matches,
            outcomes=[*research.outcomes, *evaluation.outcomes],
            queries=research.queries,
            warnings=[*bucketing.warnings, *research.warnings, *evaluation.warnings],
        )
        ready = result.campaign_ready_buckets()
        result.metrics = {
            "intents": len(intents),
            "buckets": len(result.buckets),
            "campaign_ready_buckets": len(ready),
            "products_researched": len(result.products),
            "products_qualified": sum(
                1 for m in result.matches if m.classification == MatchClassification.QUALIFIED
            ),
            "products_negotiable": sum(
                1 for m in result.matches if m.classification == MatchClassification.NEGOTIABLE_GAP
            ),
            "products_rejected": sum(
                1 for m in result.matches if m.classification == MatchClassification.REJECTED
            ),
            "judge_calls": bucketing.judge_calls,
            "research_calls": research.metrics.get("research_calls", 0),
            "research_provider": research.metrics.get("provider"),
            "research_attempts": research.attempts,
            "engine": self.ctx.engine,
        }
        return result
