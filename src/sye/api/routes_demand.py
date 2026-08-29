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

from sye.agents import IntentAgent, MarketResearchAgent
from sye.agents.base import AgentContext
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
from sye.integrations.llm import NullProvider, build_llm_provider
from sye.observability.audit import AuditLogger
from sye.services.bucketing import requirement_summary
from sye.services.constraints import describe
from sye.services.scenarios import normalize_users

router = APIRouter(prefix="/api/v1/demand", tags=["demand"])


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
