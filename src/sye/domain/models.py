"""Canonical Pydantic models.

These types are the single contract between agents, LangGraph state, the database,
the REST API, the simulation layer and the Lovable frontend. There is exactly one
definition of "product", "intent", "offer" and "campaign" in this codebase.

Money is modelled as ``Decimal`` internally (deterministic arithmetic) but always
serialises to a plain JSON number, so the frontend never decodes strings.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from sye.domain.enums import (
    BucketStatus,
    ConstraintOperator,
    DataOrigin,
    EvaluationResult,
    Importance,
    MatchClassification,
    RunMode,
    RunStatus,
)
from sye.domain.events import AuditEvent
from sye.domain.primitives import EvidenceSource, Money, SyeModel

__all_primitives__ = ("EvidenceSource", "Money", "SyeModel", "CENTS")


# --------------------------------------------------------------------------- #
# 5.2 Raw user request
# --------------------------------------------------------------------------- #
class UserRequest(SyeModel):
    user_id: str
    request_id: str
    prompt: str
    market: str = "SE"
    currency: str = "EUR"
    created_at: datetime


# --------------------------------------------------------------------------- #
# 5.3 Requirements
# --------------------------------------------------------------------------- #
class RequirementConstraint(SyeModel):
    key: str
    operator: ConstraintOperator
    value: Any
    unit: str | None = None
    importance: Importance
    weight: float = 1.0
    acceptable_substitutions: list[Any] = Field(default_factory=list)
    source_text: str | None = None
    confidence: float = 0.5
    required_by_user_ids: list[str] = Field(default_factory=list)
    """Users whose request produced this constraint. A merged bucket constraint is
    binding even when only one member asked for it."""

    @property
    def is_hard(self) -> bool:
        return self.importance == Importance.HARD


# --------------------------------------------------------------------------- #
# 5.4 Structured user intent
# --------------------------------------------------------------------------- #
class UserIntent(SyeModel):
    intent_id: str
    user_id: str
    request_id: str
    category: str
    category_confidence: float

    constraints: list[RequirementConstraint] = Field(default_factory=list)
    max_budget: Money | None = None
    target_budget: Money | None = None
    currency: str = "EUR"

    purchase_timing: str | None = None
    quantity: int = 1

    named_products: list[str] = Field(default_factory=list)
    named_brands: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)

    freeform_preferences: list[str] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_questions: list[str] = Field(default_factory=list)

    extraction_summary: str = ""
    extraction_confidence: float = 0.5
    extracted_by: str = "heuristic"
    data_origin: DataOrigin = DataOrigin.LLM_INFERRED

    def hard_constraints(self) -> list[RequirementConstraint]:
        return [c for c in self.constraints if c.is_hard]

    def soft_constraints(self) -> list[RequirementConstraint]:
        return [c for c in self.constraints if not c.is_hard]


class IntentExtraction(SyeModel):
    """LLM-facing projection of :class:`UserIntent` (no server-assigned IDs)."""

    category: str
    category_confidence: float = 0.8
    constraints: list[RequirementConstraint] = Field(default_factory=list)
    max_budget: Money | None = None
    target_budget: Money | None = None
    purchase_timing: str | None = None
    quantity: int = 1
    named_products: list[str] = Field(default_factory=list)
    named_brands: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    freeform_preferences: list[str] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    extraction_summary: str = ""
    extraction_confidence: float = 0.6


# --------------------------------------------------------------------------- #
# 5.5 Demand bucket
# --------------------------------------------------------------------------- #
class DemandBucket(SyeModel):
    bucket_id: str
    category: str
    label: str

    member_intent_ids: list[str] = Field(default_factory=list)
    member_user_ids: list[str] = Field(default_factory=list)
    demand_quantity: int = 0

    shared_hard_constraints: list[RequirementConstraint] = Field(default_factory=list)
    compatible_soft_constraints: list[RequirementConstraint] = Field(default_factory=list)

    price_ceiling: Money | None = None
    target_price: Money | None = None
    currency: str = "EUR"

    compatibility_score: float = 1.0
    compatibility_explanation: str = ""

    conflicts: list[str] = Field(default_factory=list)
    status: BucketStatus = BucketStatus.OPEN
    created_at: datetime


class BucketMembershipExplanation(SyeModel):
    """Per-user answer to: why is this person in (or out of) this bucket?"""

    user_id: str
    bucket_id: str
    joined: bool
    common_requirements: list[str] = Field(default_factory=list)
    individual_requirements_preserved: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    explanation: str = ""


class BucketOutcome(SyeModel):
    """Terminal state of a bucket, so a failed bucket is data rather than a crash."""

    bucket_id: str
    status: BucketStatus
    reason: str = ""
    campaign_id: str | None = None


# --------------------------------------------------------------------------- #
# 5.6 Product candidate
# --------------------------------------------------------------------------- #
class ProductCandidate(SyeModel):
    product_id: str
    category: str

    brand: str
    model: str
    canonical_name: str

    attributes: dict[str, Any] = Field(default_factory=dict)
    normal_market_price: Money | None = None
    currency: str | None = None

    merchant_or_listing_name: str | None = None
    listing_url: str | None = None
    availability: str | None = None

    sources: list[EvidenceSource] = Field(default_factory=list)
    data_origin: DataOrigin = DataOrigin.WEB_RESEARCH

    researched_at: datetime
    bucket_id: str | None = None
    verified: bool = False


class ProductDiscovery(SyeModel):
    """LLM/Linkup-facing projection of a product candidate (no server IDs)."""

    brand: str = "Unknown"
    model: str = ""
    canonical_name: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    normal_market_price: Money | None = None
    currency: str | None = None
    merchant_or_listing_name: str | None = None
    listing_url: str | None = None
    availability: str | None = None


class ProductDiscoveryList(SyeModel):
    products: list[ProductDiscovery] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 5.7 Match evaluation
# --------------------------------------------------------------------------- #
class ConstraintEvaluation(SyeModel):
    constraint_key: str
    result: EvaluationResult
    expected: Any = None
    observed: Any | None = None
    explanation: str = ""
    importance: Importance = Importance.HARD
    required_by_user_ids: list[str] = Field(default_factory=list)


class ProductMatch(SyeModel):
    match_id: str
    bucket_id: str
    product_id: str
    product_name: str = ""
    """Denormalised for the frontend, and the stable tie-breaker for ranking:
    product ids are scoped to a run, names are not."""

    classification: MatchClassification

    hard_constraint_results: list[ConstraintEvaluation] = Field(default_factory=list)
    soft_constraint_results: list[ConstraintEvaluation] = Field(default_factory=list)
    soft_constraint_score: float = 0.0
    overall_score: float = 0.0

    negotiable_gaps: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    unknown_specs: list[str] = Field(default_factory=list)

    explanation: str = ""
    explained_by: str = "deterministic"


class MatchExplanation(SyeModel):
    """LLM-facing copy for a single evaluated match."""

    explanation: str


# --------------------------------------------------------------------------- #
# 5.8 Supplier
# --------------------------------------------------------------------------- #
SupplierType = Literal["manufacturer", "distributor", "retailer", "marketplace_seller", "unknown"]


class SupplierCandidate(SyeModel):
    supplier_id: str
    name: str
    supplier_type: SupplierType = "unknown"
    website: str | None = None
    market: str | None = None
    evidence: list[EvidenceSource] = Field(default_factory=list)
    data_origin: DataOrigin = DataOrigin.WEB_RESEARCH
    product_ids: list[str] = Field(default_factory=list)
    bucket_id: str | None = None
    authorization_claimed: bool = False


class SupplierDiscovery(SyeModel):
    name: str
    supplier_type: SupplierType = "unknown"
    website: str | None = None
    market: str | None = None


class SupplierDiscoveryList(SyeModel):
    suppliers: list[SupplierDiscovery] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 5.9 RFQ
# --------------------------------------------------------------------------- #
class RFQ(SyeModel):
    rfq_id: str
    bucket_id: str
    product_ids: list[str] = Field(default_factory=list)
    supplier_ids: list[str] = Field(default_factory=list)
    quantity: int = 1
    requested_currency: str = "EUR"
    requested_target_unit_price: Money | None = None

    requested_terms: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""

    status: Literal["draft", "simulation_ready", "ready_for_human_review"] = "draft"
    created_at: datetime | None = None


class RFQCopy(SyeModel):
    """LLM-facing RFQ prose. Commercial numbers never come from here."""

    summary: str


# --------------------------------------------------------------------------- #
# 5.10 Supplier offer
# --------------------------------------------------------------------------- #
class SupplierOffer(SyeModel):
    offer_id: str
    rfq_id: str
    supplier_id: str
    product_id: str

    unit_price: Money
    currency: str = "EUR"
    max_quantity: int | None = None

    shipping_cost_total: Money | None = None
    estimated_delivery_days: int | None = None
    warranty_months: int | None = None
    returns_policy_summary: str | None = None
    expires_at: datetime | None = None

    conditions: list[str] = Field(default_factory=list)

    negotiation_round: int = 1
    data_origin: DataOrigin = DataOrigin.SIMULATED
    source_reference: str | None = None
    created_at: datetime | None = None


# --------------------------------------------------------------------------- #
# 5.11 Offer evaluation
# --------------------------------------------------------------------------- #
class OfferEvaluation(SyeModel):
    offer_id: str
    bucket_id: str | None = None
    landed_unit_cost: Money
    price_score: float
    fulfillment_score: float
    warranty_score: float
    terms_score: float = 0.0
    overall_score: float
    qualifies: bool
    disqualification_reasons: list[str] = Field(default_factory=list)
    negotiation_round: int = 1


class NegotiationAction(SyeModel):
    offer_id: str
    supplier_id: str
    round: int
    action: Literal["accept", "counter", "reject"]
    proposed_unit_price: Money | None = None
    requested_term_changes: dict[str, Any] = Field(default_factory=dict)
    supplier_message: str = ""
    rationale_summary: str = ""
    delivered: bool = False  # demo mode never sends anything
    authored_by: str = "deterministic"


class NegotiationCopy(SyeModel):
    """LLM-facing negotiation prose. The price policy is computed in Python."""

    supplier_message: str
    rationale_summary: str


# --------------------------------------------------------------------------- #
# 5.12 Campaign
# --------------------------------------------------------------------------- #
class Campaign(SyeModel):
    campaign_id: str
    bucket_id: str
    winning_offer_id: str
    product_id: str
    supplier_id: str

    title: str
    short_description: str
    why_this_product: str

    currency: str = "EUR"
    normal_market_price: Money | None = None
    group_price: Money
    discount_amount: Money | None = None
    discount_percent: float | None = None

    committed_demand: int
    min_buyers: int
    max_buyers: int | None = None

    starts_at: datetime
    ends_at: datetime

    terms_summary: list[str] = Field(default_factory=list)
    requirement_match_summary: list[str] = Field(default_factory=list)
    member_user_ids: list[str] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)

    status: Literal["draft", "simulation_ready", "ready_for_review"] = "simulation_ready"
    data_origin: DataOrigin = DataOrigin.SIMULATED
    run_id: str | None = None
    disclaimer: str = (
        "Commercial terms are simulated for demonstration purposes and are not a "
        "supplier commitment."
    )


class CampaignCopy(SyeModel):
    """LLM-facing campaign copy. All numbers are computed deterministically."""

    title: str
    short_description: str
    why_this_product: str


# --------------------------------------------------------------------------- #
# 5.13 Simulation profile
# --------------------------------------------------------------------------- #
class SimulatedSupplierProfile(SyeModel):
    supplier_id: str
    seed_key: str = ""
    """Stable key the simulation is seeded from (the supplier's name), so the same
    seed reproduces the same economics across runs even though IDs are run-scoped."""
    margin_flexibility: float
    min_quantity_for_discount: int
    max_discount_percent: float
    shipping_days: tuple[int, int]
    warranty_months: int
    negotiation_stubbornness: float
    base_markup: float = 1.0
    shipping_cost_per_unit: Money = Decimal("0")


# --------------------------------------------------------------------------- #
# 5.14 Unified run export — the frontend contract
# --------------------------------------------------------------------------- #
class PipelineRunExport(SyeModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    mode: RunMode
    status: RunStatus
    scenario_name: str | None = None
    market: str = "SE"
    currency: str = "EUR"

    started_at: datetime
    completed_at: datetime | None = None

    user_requests: list[UserRequest] = Field(default_factory=list)
    intents: list[UserIntent] = Field(default_factory=list)
    buckets: list[DemandBucket] = Field(default_factory=list)
    bucket_memberships: list[BucketMembershipExplanation] = Field(default_factory=list)
    bucket_outcomes: list[BucketOutcome] = Field(default_factory=list)
    products: list[ProductCandidate] = Field(default_factory=list)
    matches: list[ProductMatch] = Field(default_factory=list)
    suppliers: list[SupplierCandidate] = Field(default_factory=list)
    rfqs: list[RFQ] = Field(default_factory=list)
    offers: list[SupplierOffer] = Field(default_factory=list)
    offer_evaluations: list[OfferEvaluation] = Field(default_factory=list)
    negotiation_actions: list[NegotiationAction] = Field(default_factory=list)
    campaigns: list[Campaign] = Field(default_factory=list)

    audit_events: list[AuditEvent] = Field(default_factory=list)

    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Demo mode: supplier offers, negotiations and campaign commercial terms are "
        "simulated. Product and supplier research may come from live web sources; "
        "sources are attached to every web-derived object."
    )
