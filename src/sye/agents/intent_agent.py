"""Intent Agent — owns the ingestion phase.

Autonomous work: reading what a person actually meant. Which statements are
requirements and which are wishes, what "one cable for my MacBook" implies and how
confident we should be about it, what a hedged budget means, whether a request is
even about this category. It decides to proceed under conservative assumptions
rather than blocking on clarification, and records the uncertainty it accepted.
"""

from __future__ import annotations

import asyncio

from pydantic import Field

from sye.agents.base import Agent, AgentResult
from sye.agents.tools.intent_parser import parse_intent
from sye.domain.enums import AuditStatus
from sye.domain.models import UserIntent, UserRequest
from sye.services.bucketing import hard_constraints_of
from sye.services.constraints import merge_hard_constraints

MAX_PARSE_CONCURRENCY = 5


class IntentAgentResult(AgentResult):
    intents: list[UserIntent] = Field(default_factory=list)
    unparsed_user_ids: list[str] = Field(default_factory=list)
    self_contradictory_user_ids: list[str] = Field(default_factory=list)
    low_confidence_user_ids: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class IntentAgent(Agent):
    name = "intent_agent"

    async def ingest(self, requests: list[UserRequest]) -> IntentAgentResult:
        """Free text in, validated :class:`UserIntent` out — one per person."""
        semaphore = asyncio.Semaphore(MAX_PARSE_CONCURRENCY)

        async def parse_one(request: UserRequest):
            async with semaphore:
                return await parse_intent(request, llm=self.llm)

        intents: list[UserIntent] = []
        warnings: list[str] = []
        unparsed: list[str] = []

        async with self.audit.step(
            node="parse_user_intents", input_refs=[r.request_id for r in requests]
        ) as step:
            results = await asyncio.gather(
                *(parse_one(r) for r in requests), return_exceptions=True
            )

            for request, result in zip(requests, results, strict=True):
                if isinstance(result, BaseException):
                    unparsed.append(request.user_id)
                    warnings.append(f"intent parsing failed for {request.user_id}: {result}")
                    self.audit.warning(
                        node="parse_user_intents",
                        message=f"Could not parse {request.user_id}: {result}",
                        input_refs=[request.request_id],
                    )
                    continue

                intent, engine, parse_warnings = result
                warnings.extend(parse_warnings)
                intents.append(intent)
                self.audit.event(
                    node="parse_user_intents",
                    event_type="intent_parsed",
                    message=(
                        f"Parsed {intent.user_id} -> {intent.category} / "
                        f"{len(intent.hard_constraints())} hard + "
                        f"{len(intent.soft_constraints())} soft constraints"
                    ),
                    input_refs=[request.request_id],
                    output_refs=[intent.intent_id],
                    decision=intent.extraction_summary,
                    confidence=intent.extraction_confidence,
                    metadata={"engine": engine, "budget": str(intent.max_budget)},
                )

            step.complete(
                message=f"Parsed {len(intents)}/{len(requests)} requests into structured intents",
                output_refs=[i.intent_id for i in intents],
                metadata={"engine": self.ctx.engine},
            )

        return IntentAgentResult(
            agent=self.name,
            intents=intents,
            unparsed_user_ids=unparsed,
            warnings=warnings,
            categories=sorted({i.category for i in intents}),
        )

    async def validate(self, intents: list[UserIntent]) -> IntentAgentResult:
        """Record uncertainty and proceed. The demo never blocks on clarification."""
        warnings: list[str] = []
        low_confidence = [i for i in intents if i.extraction_confidence < 0.5]
        need_clarification = [i for i in intents if i.clarification_needed]
        no_constraints = [i for i in intents if not i.hard_constraints()]
        categories = sorted({i.category for i in intents})

        contradictory: list[tuple[str, list[str]]] = []
        for intent in intents:
            _, conflicts = merge_hard_constraints(hard_constraints_of(intent))
            if conflicts:
                contradictory.append((intent.user_id, conflicts))

        async with self.audit.step(
            node="validate_intents", input_refs=[i.intent_id for i in intents]
        ) as step:
            for intent in low_confidence:
                warnings.append(
                    f"low extraction confidence ({intent.extraction_confidence:.2f}) for "
                    f"{intent.user_id}; proceeding with conservative assumptions"
                )
            for intent in no_constraints:
                warnings.append(
                    f"{intent.user_id} stated no hard requirement; only budget and preferences "
                    "will constrain matching"
                )
            for user_id, conflicts in contradictory:
                warnings.append(
                    f"{user_id} stated contradictory hard requirements "
                    f"({'; '.join(conflicts)}); the request is isolated so it cannot "
                    "constrain other buyers"
                )
                self.audit.warning(
                    node="validate_intents",
                    message=f"{user_id} contradicts itself: {'; '.join(conflicts)}",
                    metadata={"conflicts": conflicts},
                )
            if len(categories) > 1:
                warnings.append(
                    f"requests span multiple categories {categories}; each category is "
                    "bucketed separately"
                )

            step.complete(
                message=(
                    f"Validated {len(intents)} intents across {len(categories)} "
                    f"categor{'y' if len(categories) == 1 else 'ies'}: {', '.join(categories)}"
                ),
                output_refs=[i.intent_id for i in intents],
                metadata={
                    "low_confidence": [i.user_id for i in low_confidence],
                    "clarification_needed": [i.user_id for i in need_clarification],
                    "self_contradictory": [user_id for user_id, _ in contradictory],
                    "categories": categories,
                },
            )

        return IntentAgentResult(
            agent=self.name,
            intents=intents,
            warnings=warnings,
            categories=categories,
            low_confidence_user_ids=[i.user_id for i in low_confidence],
            self_contradictory_user_ids=[user_id for user_id, _ in contradictory],
        )

    async def run(self, requests: list[UserRequest]) -> IntentAgentResult:
        """Ingest and validate in one call (used standalone and in tests)."""
        ingested = await self.ingest(requests)
        validated = await self.validate(ingested.intents)
        return IntentAgentResult(
            agent=self.name,
            intents=ingested.intents,
            unparsed_user_ids=ingested.unparsed_user_ids,
            self_contradictory_user_ids=validated.self_contradictory_user_ids,
            low_confidence_user_ids=validated.low_confidence_user_ids,
            categories=validated.categories,
            warnings=[*ingested.warnings, *validated.warnings],
            metrics={
                "requests": len(requests),
                "intents": len(ingested.intents),
                "engine": self.ctx.engine,
                "audit_events": sum(
                    1 for e in self.audit.events if e.status != AuditStatus.STARTED
                ),
            },
        )
