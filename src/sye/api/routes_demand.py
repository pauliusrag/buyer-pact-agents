"""Demand front door.

One endpoint, built for a website's submit button: take what people typed, return
which compatible group each of them belongs to and why.

It deliberately runs only the first half of the pipeline — parse intents, then
group compatible demand. No web research, no suppliers, no negotiation. That makes
it fast enough (milliseconds) and free enough to call on every form submission,
and it needs no Linkup or LLM key to work. Turning a group into a campaign is the
slow, expensive half, and it belongs on a schedule rather than in a page load.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator

from sye.agents import IntentAgent, MarketResearchAgent, SourcingAgent
from sye.agents.base import AgentContext
from sye.agents.market_research_agent import MarketResearchResult
from sye.api.schemas import ScenarioUser
from sye.api.service import RunManager, get_run_manager
from sye.config import get_settings
from sye.domain.ids import new_run_id, stable_id, utcnow
from sye.domain.models import (
    BucketMembershipExplanation,
    DemandBucket,
    UserIntent,
    UserRequest,
)
from sye.domain.primitives import SyeModel
from sye.integrations.linkup_client import ResearchError, build_research_client
from sye.integrations.llm import NullProvider, build_llm_provider
from sye.observability.audit import AuditLogger
from sye.services.bucketing import requirement_summary
from sye.services.constraints import describe
from sye.services.matching import rank_matches
from sye.services.scenarios import normalize_users

router = APIRouter(prefix="/api/v1/demand", tags=["demand"])


class Candidate(SyeModel):
    """A researched product, judged against one group's requirements."""

    product_id: str
    name: str
    brand: str
    price: float | None
    currency: str | None
    verdict: str
    score: float
    reason: str
    passed: int
    total: int
    attributes: dict[str, Any] = Field(default_factory=dict)
    listing_url: str | None = None
    origin: str
    sources: list[str] = Field(default_factory=list)
    price_implausible: bool = False


class Supplier(SyeModel):
    """A company that could plausibly fulfil the group's order."""

    supplier_id: str
    name: str
    type: str
    website: str | None = None
    market: str | None = None
    origin: str
    sources: list[str] = Field(default_factory=list)


class BucketResearch(SyeModel):
    bucket_id: str
    label: str
    queries: list[str] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    winner_id: str | None = None
    suppliers: list[Supplier] = Field(default_factory=list)
    demand_quantity: int = 0


class DemandResearchResponse(SyeModel):
    grouped_at: str
    groups: list[DemandGroup]
    research: list[BucketResearch] = Field(default_factory=list)
    parsed: list[ParsedRequest] = Field(default_factory=list)
    trace: list[AgentStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    engine: str
    provider: str
    groups_total: int = 0
    groups_researched: int = 0


class DemandResearchRequest(SyeModel):
    """Same input as grouping, plus whether to search the live web."""

    users: list[ScenarioUser] = Field(default_factory=list)
    market: str = "SE"
    currency: str = "EUR"
    live: bool = False
    include_suppliers: bool = True
    max_groups_to_research: int = 3
    """Research is the expensive stage. With hundreds of customers there are dozens of
    groups, so only the largest are taken to the web."""

    @field_validator("users", mode="before")
    @classmethod
    def _accept_any_user_shape(cls, value: Any) -> Any:
        if value in (None, [], {}):
            return []
        return normalize_users(value)


class DemandGroupRequest(SyeModel):
    """Everyone whose demand should be considered together.

    Send the new submission *and* the ones already collected: a group only exists
    relative to other people, so grouping one request on its own is meaningless.
    """

    users: list[ScenarioUser] = Field(default_factory=list)
    market: str = "SE"
    currency: str = "EUR"

    @field_validator("users", mode="before")
    @classmethod
    def _accept_any_user_shape(cls, value: Any) -> Any:
        if value in (None, [], {}):
            return []
        return normalize_users(value)


class GroupMember(SyeModel):
    user_id: str
    joined: bool
    explanation: str
    common_requirements: list[str] = Field(default_factory=list)
    own_requirements_kept: list[str] = Field(default_factory=list)
    inherited_requirements: list[str] = Field(default_factory=list)


class DemandGroup(SyeModel):
    """A compatible group, phrased for a person reading a web page."""

    bucket_id: str
    label: str
    category: str
    member_user_ids: list[str]
    size: int
    demand_quantity: int
    price_ceiling: float | None
    currency: str
    requirements: list[str]
    explanation: str
    compatibility_score: float
    members: list[GroupMember] = Field(default_factory=list)


class ParsedRequest(SyeModel):
    user_id: str
    prompt: str
    category: str
    summary: str
    hard_requirements: list[str]
    soft_preferences: list[str]
    max_budget: float | None
    confidence: float
    clarification_questions: list[str] = Field(default_factory=list)
    engine: str


class AgentStep(SyeModel):
    """One recorded decision, for showing what the agents actually did."""

    sequence: int
    agent: str
    node: str
    status: str
    message: str
    decision: str | None = None
    confidence: float | None = None
    duration_ms: int | None = None


class DemandGroupResponse(SyeModel):
    grouped_at: str
    groups: list[DemandGroup]
    parsed: list[ParsedRequest]
    trace: list[AgentStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    engine: str

    def group_for(self, user_id: str) -> DemandGroup | None:
        return next((g for g in self.groups if user_id in g.member_user_ids), None)


def _member_view(
    memberships: list[BucketMembershipExplanation], bucket: DemandBucket
) -> list[GroupMember]:
    out: list[GroupMember] = []
    for membership in memberships:
        if membership.bucket_id != bucket.bucket_id or not membership.joined:
            continue
        inherited = [
            describe(c)
            for c in bucket.shared_hard_constraints
            if membership.user_id not in c.required_by_user_ids and c.key != "price.unit_price"
        ]
        out.append(
            GroupMember(
                user_id=membership.user_id,
                joined=True,
                explanation=membership.explanation,
                common_requirements=membership.common_requirements,
                own_requirements_kept=membership.individual_requirements_preserved,
                inherited_requirements=inherited,
            )
        )
    return out


AGENT_BY_NODE = {
    "parse_user_intents": "Intent Agent",
    "validate_intents": "Intent Agent",
    "build_demand_buckets": "Market Research Agent",
}


def _trace_view(events) -> list[AgentStep]:
    """The audit trail, trimmed to what is worth showing a person."""
    steps: list[AgentStep] = []
    for event in events:
        if event.status.value == "started":
            continue
        steps.append(
            AgentStep(
                sequence=len(steps) + 1,
                agent=AGENT_BY_NODE.get(event.node, "Pipeline"),
                node=event.node,
                status=event.status.value,
                message=event.message,
                decision=event.decision,
                confidence=event.confidence,
                duration_ms=event.duration_ms,
            )
        )
    return steps


def _parsed_view(intents: list[UserIntent], prompts: dict[str, str]) -> list[ParsedRequest]:
    return [
        ParsedRequest(
            user_id=intent.user_id,
            prompt=prompts.get(intent.user_id, ""),
            category=intent.category,
            summary=intent.extraction_summary,
            hard_requirements=[describe(c) for c in intent.hard_constraints()],
            soft_preferences=[describe(c) for c in intent.soft_constraints()],
            max_budget=float(intent.max_budget) if intent.max_budget is not None else None,
            confidence=intent.extraction_confidence,
            clarification_questions=intent.clarification_questions,
            engine=intent.extracted_by,
        )
        for intent in intents
    ]


@router.post(
    "/group",
    response_model=DemandGroupResponse,
    summary="Group free-text demand into compatible buying groups",
    description=(
        "Parses each person's request and returns the compatible groups they form. "
        "Fast and key-free: no web research, no suppliers, no negotiation."
    ),
)
async def group_demand(
    request: DemandGroupRequest, manager: RunManager = Depends(get_run_manager)
) -> DemandGroupResponse:
    if not request.users:
        raise HTTPException(status_code=422, detail="no users to group")

    settings = get_settings()
    run_id = new_run_id()
    config = settings.demo_config(offline=True, write_snapshots=False).model_copy(
        update={"market": request.market, "currency": request.currency}
    )

    llm = build_llm_provider(settings, offline=False)
    ctx = AgentContext(
        run_id=run_id,
        config=config,
        audit=AuditLogger(run_id),
        llm=None if isinstance(llm, NullProvider) else llm,
        research=None,  # grouping never touches the web
    )

    now = utcnow()
    requests = [
        UserRequest(
            user_id=user.user_id or f"user_{index + 1:03d}",
            request_id=stable_id("req", run_id, user.user_id or str(index)),
            prompt=user.prompt,
            market=user.market or request.market,
            currency=user.currency or request.currency,
            created_at=now,
        )
        for index, user in enumerate(request.users)
    ]

    intent_result = await IntentAgent(ctx).run(requests)
    bucketing = await MarketResearchAgent(ctx).build_buckets(intent_result.intents)

    groups = [
        DemandGroup(
            bucket_id=bucket.bucket_id,
            label=bucket.label,
            category=bucket.category,
            member_user_ids=bucket.member_user_ids,
            size=len(bucket.member_user_ids),
            demand_quantity=bucket.demand_quantity,
            price_ceiling=float(bucket.price_ceiling) if bucket.price_ceiling is not None else None,
            currency=bucket.currency,
            requirements=requirement_summary(bucket),
            explanation=bucket.compatibility_explanation,
            compatibility_score=bucket.compatibility_score,
            members=_member_view(bucketing.memberships, bucket),
        )
        for bucket in bucketing.buckets
    ]

    return DemandGroupResponse(
        grouped_at=now.isoformat(),
        groups=groups,
        parsed=_parsed_view(intent_result.intents, {r.user_id: r.prompt for r in requests}),
        trace=_trace_view(ctx.audit.ordered()),
        warnings=[*intent_result.warnings, *bucketing.warnings],
        engine=ctx.engine,
    )


def _candidate_view(match, product, bucket_currency: str) -> Candidate:
    passed = sum(1 for e in match.hard_constraint_results if e.result.value == "pass")
    reason = (
        match.rejection_reasons[0]
        if match.rejection_reasons
        else (match.negotiable_gaps[0] if match.negotiable_gaps else match.explanation)
    )
    return Candidate(
        product_id=product.product_id,
        name=product.canonical_name,
        brand=product.brand,
        price=float(product.normal_market_price)
        if product.normal_market_price is not None
        else None,
        currency=product.currency or bucket_currency,
        verdict=match.classification.value,
        score=match.overall_score,
        reason=reason,
        passed=passed,
        total=len(match.hard_constraint_results),
        attributes=product.attributes,
        listing_url=product.listing_url,
        origin=product.data_origin.value,
        sources=[source.url for source in product.sources][:3],
        price_implausible=match.price_implausible,
    )


@router.post(
    "/research",
    response_model=DemandResearchResponse,
    summary="Group demand, then research products that fit each group",
    description=(
        "Runs the market research agent end to end: parse, group, search for "
        "candidates and judge every one against the group's binding requirements. "
        "Set `live: true` to search the real web with Linkup."
    ),
)
async def research_demand(request: DemandResearchRequest) -> DemandResearchResponse:
    if not request.users:
        raise HTTPException(status_code=422, detail="no users to research for")

    settings = get_settings()
    run_id = new_run_id()
    config = settings.demo_config(offline=not request.live, write_snapshots=False).model_copy(
        update={"market": request.market, "currency": request.currency}
    )

    llm = build_llm_provider(settings, offline=False)
    try:
        research_client = build_research_client(
            settings, offline=config.offline, seed=config.seed, max_calls=config.max_linkup_calls
        )
    except ResearchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    ctx = AgentContext(
        run_id=run_id,
        config=config,
        audit=AuditLogger(run_id),
        llm=None if isinstance(llm, NullProvider) else llm,
        research=research_client,
    )

    now = utcnow()
    requests = [
        UserRequest(
            user_id=user.user_id or f"user_{index + 1:03d}",
            request_id=stable_id("req", run_id, user.user_id or str(index)),
            prompt=user.prompt,
            market=user.market or request.market,
            currency=user.currency or request.currency,
            created_at=now,
        )
        for index, user in enumerate(request.users)
    ]

    intent_result = await IntentAgent(ctx).run(requests)
    agent = MarketResearchAgent(ctx)

    bucketing = await agent.build_buckets(intent_result.intents)
    all_buckets = sorted(bucketing.buckets, key=lambda b: (-len(b.member_user_ids), b.bucket_id))
    researched = all_buckets[: max(1, request.max_groups_to_research)]

    product_outcome = await agent.research_products(researched)
    match_outcome = await agent.evaluate_matches(researched, product_outcome.products)

    result = MarketResearchResult(
        agent=agent.name,
        buckets=researched,
        memberships=bucketing.memberships,
        products=product_outcome.products,
        matches=match_outcome.matches,
        outcomes=[*product_outcome.outcomes, *match_outcome.outcomes],
        queries=product_outcome.queries,
        warnings=[*bucketing.warnings, *product_outcome.warnings, *match_outcome.warnings],
    )

    suppliers_by_bucket: dict[str, list[Supplier]] = {}
    if request.include_suppliers and result.campaign_ready_buckets():
        sourcing = await SourcingAgent(ctx).research_suppliers(
            result.campaign_ready_buckets(), result.products, result.matches
        )
        for supplier in sourcing.suppliers:
            suppliers_by_bucket.setdefault(supplier.bucket_id or "", []).append(
                Supplier(
                    supplier_id=supplier.supplier_id,
                    name=supplier.name,
                    type=supplier.supplier_type,
                    website=supplier.website,
                    market=supplier.market,
                    origin=supplier.data_origin.value,
                    sources=[source.url for source in supplier.evidence][:2],
                )
            )

    products = {p.product_id: p for p in result.products}
    groups: list[DemandGroup] = []
    research: list[BucketResearch] = []

    for bucket in all_buckets:
        groups.append(
            DemandGroup(
                bucket_id=bucket.bucket_id,
                label=bucket.label,
                category=bucket.category,
                member_user_ids=bucket.member_user_ids,
                size=len(bucket.member_user_ids),
                demand_quantity=bucket.demand_quantity,
                price_ceiling=float(bucket.price_ceiling)
                if bucket.price_ceiling is not None
                else None,
                currency=bucket.currency,
                requirements=requirement_summary(bucket),
                explanation=bucket.compatibility_explanation,
                compatibility_score=bucket.compatibility_score,
                members=_member_view(result.memberships, bucket),
            )
        )

        if bucket not in researched:
            continue
        matches = [m for m in result.matches if m.bucket_id == bucket.bucket_id]
        ranked = rank_matches(matches)
        candidates = [
            _candidate_view(match, products[match.product_id], bucket.currency)
            for match in ranked
            if match.product_id in products
        ]
        best = result.best_match(bucket.bucket_id)
        research.append(
            BucketResearch(
                bucket_id=bucket.bucket_id,
                label=bucket.label,
                queries=result.queries.get(bucket.bucket_id, []),
                candidates=candidates,
                winner_id=best.product_id if best else None,
                suppliers=suppliers_by_bucket.get(bucket.bucket_id, []),
                demand_quantity=bucket.demand_quantity,
            )
        )

    return DemandResearchResponse(
        grouped_at=now.isoformat(),
        groups=groups,
        research=research,
        parsed=_parsed_view(intent_result.intents, {r.user_id: r.prompt for r in requests}),
        trace=_trace_view(ctx.audit.ordered()),
        warnings=[*intent_result.warnings, *result.warnings],
        engine=ctx.engine,
        provider=research_client.name,
        groups_total=len(all_buckets),
        groups_researched=len(researched),
    )
