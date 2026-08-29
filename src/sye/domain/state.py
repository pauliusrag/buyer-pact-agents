"""LangGraph state.

Every list uses an additive reducer, so nodes return only the objects they
created. Fan-out over buckets or users can therefore never clobber a shared array.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from sye.config import DemoConfig
from sye.domain.events import AuditEvent
from sye.domain.models import (
    RFQ,
    BucketMembershipExplanation,
    BucketOutcome,
    Campaign,
    DemandBucket,
    NegotiationAction,
    OfferEvaluation,
    ProductCandidate,
    ProductMatch,
    SupplierCandidate,
    SupplierOffer,
    UserIntent,
    UserRequest,
)


class PipelineState(TypedDict, total=False):
    run_id: str
    mode: str
    scenario_name: str
    config: DemoConfig

    user_requests: Annotated[list[UserRequest], operator.add]
    intents: Annotated[list[UserIntent], operator.add]
    buckets: Annotated[list[DemandBucket], operator.add]
    bucket_memberships: Annotated[list[BucketMembershipExplanation], operator.add]
    bucket_outcomes: Annotated[list[BucketOutcome], operator.add]

    products: Annotated[list[ProductCandidate], operator.add]
    matches: Annotated[list[ProductMatch], operator.add]
    suppliers: Annotated[list[SupplierCandidate], operator.add]

    rfqs: Annotated[list[RFQ], operator.add]
    offers: Annotated[list[SupplierOffer], operator.add]
    offer_evaluations: Annotated[list[OfferEvaluation], operator.add]
    negotiation_actions: Annotated[list[NegotiationAction], operator.add]
    campaigns: Annotated[list[Campaign], operator.add]

    audit_events: Annotated[list[AuditEvent], operator.add]
    warnings: Annotated[list[str], operator.add]

    active_negotiation_round: int
    metrics: dict[str, Any]


def initial_state(
    *,
    run_id: str,
    config: DemoConfig,
    scenario_name: str,
    user_requests: list[UserRequest],
) -> PipelineState:
    return PipelineState(
        run_id=run_id,
        mode=config.mode,
        scenario_name=scenario_name,
        config=config,
        user_requests=list(user_requests),
        intents=[],
        buckets=[],
        bucket_memberships=[],
        bucket_outcomes=[],
        products=[],
        matches=[],
        suppliers=[],
        rfqs=[],
        offers=[],
        offer_evaluations=[],
        negotiation_actions=[],
        campaigns=[],
        audit_events=[],
        warnings=[],
        active_negotiation_round=1,
        metrics={},
    )
