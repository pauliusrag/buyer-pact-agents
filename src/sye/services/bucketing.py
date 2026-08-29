"""Demand bucketing.

A bucket is *compatible demand*, not a semantic cluster: users are grouped only
when a single product could satisfy every member's hard requirements. The
algorithm is deterministic; an LLM judge is consulted only for the narrow score
band where the deterministic answer is genuinely ambiguous.

Merged bucket constraints are binding for the whole group. If one member of five
requires USB-C power delivery, every candidate product must have it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sye.config import DemoConfig
from sye.domain.enums import ConstraintOperator, Importance
from sye.domain.ids import stable_id, utcnow
from sye.domain.models import (
    BucketMembershipExplanation,
    DemandBucket,
    RequirementConstraint,
    UserIntent,
)
from sye.domain.vocabulary import CATEGORY_WEARABLE, human_label, resolution_label
from sye.services.constraints import comparable, describe, merge_hard_constraints

JudgeFn = Callable[["BucketDraft", UserIntent, "JoinAssessment"], Awaitable[bool | None]]


@dataclass
class JoinAssessment:
    """Why a user can or cannot join a bucket."""

    feasible: bool
    score: float
    conflicts: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)
    decided_by: str = "deterministic"

    @property
    def accepted_deterministically(self) -> bool:
        return self.feasible and not self.blocking_reasons


@dataclass
class BucketDraft:
    category: str
    intents: list[UserIntent] = field(default_factory=list)
    join_scores: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def user_ids(self) -> list[str]:
        return [i.user_id for i in self.intents]

    def merged_hard(self) -> tuple[list[RequirementConstraint], list[str]]:
        return merge_hard_constraints(
            [c for intent in self.intents for c in hard_constraints_of(intent)]
        )

    def hard_constraints(self) -> list[RequirementConstraint]:
        merged, _ = self.merged_hard()
        return merged

    def soft_constraints(self) -> list[RequirementConstraint]:
        seen: dict[str, RequirementConstraint] = {}
        for intent in self.intents:
            for constraint in intent.soft_constraints():
                current = seen.get(constraint.key)
                if current is None:
                    seen[constraint.key] = constraint.model_copy(deep=True)
                else:
                    users = sorted(
                        set(current.required_by_user_ids) | set(constraint.required_by_user_ids)
                    )
                    seen[constraint.key] = current.model_copy(
                        update={
                            "required_by_user_ids": users,
                            "weight": current.weight + constraint.weight,
                        }
                    )
        return sorted(seen.values(), key=lambda c: c.key)

    def price_ceiling(self) -> Decimal | None:
        ceilings = [i.max_budget for i in self.intents if i.max_budget is not None]
        return min(ceilings) if ceilings else None

    def target_price(self) -> Decimal | None:
        targets = [i.target_budget for i in self.intents if i.target_budget is not None]
        if targets:
            return min(targets)
        ceiling = self.price_ceiling()
        return None if ceiling is None else (ceiling * Decimal("0.9")).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# Intent projections
# --------------------------------------------------------------------------- #
def hard_constraints_of(intent: UserIntent) -> list[RequirementConstraint]:
    """Hard constraints plus the budget, expressed in the same algebra."""
    out = [c.model_copy(deep=True) for c in intent.hard_constraints()]
    for constraint in out:
        if not constraint.required_by_user_ids:
            constraint.required_by_user_ids = [intent.user_id]
    if intent.max_budget is not None and not any(
        c.key == "price.unit_price" and c.operator == ConstraintOperator.LTE for c in out
    ):
        out.append(
            RequirementConstraint(
                key="price.unit_price",
                operator=ConstraintOperator.LTE,
                value=intent.max_budget,
                unit=intent.currency,
                importance=Importance.HARD,
                weight=1.0,
                source_text=f"budget ≤ {intent.max_budget} {intent.currency}",
                confidence=0.9,
                required_by_user_ids=[intent.user_id],
            )
        )
    return out


def _constraint_map(constraints: list[RequirementConstraint]) -> dict[str, RequirementConstraint]:
    return {c.key: c for c in constraints if c.key != "price.unit_price"}


def _value_agreement(key: str, left: Any, right: Any) -> float:
    """1.0 for identical requirements, decaying quadratically for ordered keys."""
    a, b = comparable(key, left), comparable(key, right)
    if a is None or b is None:
        return 0.0
    if isinstance(a, bool) or isinstance(b, bool):
        return 1.0 if bool(a) == bool(b) else 0.0
    if isinstance(a, int | float) and isinstance(b, int | float):
        if a == b:
            return 1.0
        low, high = sorted((float(a), float(b)))
        if high <= 0:
            return 0.0
        return (low / high) ** 2
    return 1.0 if a == b else 0.0


# Market baselines used to judge how demanding an inherited requirement is.
_BASELINE: dict[str, float] = {
    "display.size_in": 24.0,
    "display.refresh_rate_hz": 75.0,
    "display.resolution": float(1920 * 1080),
    "wearable.battery_days": 5.0,
    "wearable.water_resistance_atm": 5.0,
}
_STRICTNESS_FACTOR: dict[str, float] = {
    "display.size_in": 1.5,
    "display.refresh_rate_hz": 0.5,
    "display.resolution": 0.4,
    "wearable.battery_days": 0.5,
    "wearable.water_resistance_atm": 0.3,
}
_BOOLEAN_COST: dict[str, float] = {
    "connectivity.usb_c_power_delivery": 0.30,
    "connectivity.thunderbolt": 0.45,
    "connectivity.usb_c": 0.25,
    "adaptive_sync.freesync": 0.35,
    "adaptive_sync.gsync": 0.40,
    "display.curved": 0.30,
    "display.hdr": 0.35,
    "wearable.subscription_required": 0.35,
    "sensors.ecg": 0.45,
    "sensors.gps": 0.45,
    "sensors.temperature": 0.3,
    "sensors.spo2": 0.3,
    "sensors.sleep_tracking": 0.2,
    "sensors.heart_rate": 0.2,
    "material.titanium": 0.35,
    "compat.ios": 0.2,
    "compat.android": 0.2,
}
_DEFAULT_INHERIT_COST = 0.25


def inheritance_cost(constraint: RequirementConstraint) -> float:
    """How much a member pays for a requirement they never asked for.

    A 165 Hz refresh rate changes which product (and price bracket) the whole
    group ends up in; a VESA mount does not. Costs are bounded to [0, 0.8].
    """
    key = constraint.key
    if constraint.operator == ConstraintOperator.BOOLEAN:
        return _BOOLEAN_COST.get(key, _DEFAULT_INHERIT_COST)
    baseline = _BASELINE.get(key)
    value = comparable(key, constraint.value)
    if baseline is None or value is None or not isinstance(value, int | float):
        return _DEFAULT_INHERIT_COST
    factor = _STRICTNESS_FACTOR.get(key, 0.5)
    if constraint.operator == ConstraintOperator.LTE:
        ratio = baseline / float(value) if value else 1.0
    else:
        ratio = float(value) / baseline if baseline else 1.0
    return max(0.0, min(0.8, (ratio - 1.0) * factor))


def divergence_costs(
    left: dict[str, RequirementConstraint], right: dict[str, RequirementConstraint]
) -> dict[str, float]:
    """Per-key cost of forcing both sides under one merged requirement."""
    costs: dict[str, float] = {}
    for key in set(left) | set(right):
        a, b = left.get(key), right.get(key)
        if a is not None and b is not None:
            costs[key] = round(1.0 - _value_agreement(key, a.value, b.value), 4)
        else:
            costs[key] = round(inheritance_cost(a or b), 4)  # type: ignore[arg-type]
    return costs


# --------------------------------------------------------------------------- #
# Assessment
# --------------------------------------------------------------------------- #
def assess_join(draft: BucketDraft, intent: UserIntent, config: DemoConfig) -> JoinAssessment:
    """Score a candidate merge of ``intent`` into ``draft``."""
    if draft.category != intent.category:
        return JoinAssessment(
            feasible=False,
            score=0.0,
            conflicts=[f"different category: {draft.category} vs {intent.category}"],
            blocking_reasons=["category mismatch"],
        )

    combined = [c for member in draft.intents for c in hard_constraints_of(member)]
    combined += hard_constraints_of(intent)
    _, conflicts = merge_hard_constraints(combined)
    if conflicts:
        return JoinAssessment(
            feasible=False, score=0.0, conflicts=conflicts, blocking_reasons=list(conflicts)
        )

    draft_hard = _constraint_map(draft.hard_constraints())
    intent_hard = _constraint_map(hard_constraints_of(intent))
    costs = divergence_costs(draft_hard, intent_hard)
    agreement = 1.0 - (sum(costs.values()) / len(costs) if costs else 0.0)

    blocking: list[str] = []
    for key, cost in sorted(costs.items()):
        if cost > config.materiality_threshold:
            constraint = intent_hard.get(key) or draft_hard[key]
            who = "the new member" if key in intent_hard and key not in draft_hard else "the group"
            blocking.append(
                f"{describe(constraint)} would be imposed on members who did not ask for it "
                f"({human_label(key)} divergence {cost:.2f} > {config.materiality_threshold:.2f}, "
                f"materially changes which product {who} ends up buying)"
            )

    draft_ceiling = draft.price_ceiling()
    intent_ceiling = intent.max_budget
    if draft_ceiling is not None and intent_ceiling is not None:
        low, high = sorted((float(draft_ceiling), float(intent_ceiling)))
        price_proximity = low / high if high else 0.0
        if price_proximity < config.price_tier_ratio:
            blocking.append(
                f"price tiers too far apart ({low:.0f} vs {high:.0f} {intent.currency})"
            )
    else:
        price_proximity = 0.7  # one side unconstrained: neutral, slightly positive

    draft_soft = {c.key for c in draft.soft_constraints()}
    intent_soft = {c.key for c in intent.soft_constraints()}
    if draft_soft or intent_soft:
        union = draft_soft | intent_soft
        soft_overlap = len(draft_soft & intent_soft) / len(union) if union else 0.5
    else:
        soft_overlap = 0.5

    score = round(0.5 * agreement + 0.3 * price_proximity + 0.2 * soft_overlap, 4)
    return JoinAssessment(
        feasible=True,
        score=score,
        conflicts=[],
        blocking_reasons=blocking,
        components={
            "hard_constraint_agreement": round(agreement, 4),
            "price_proximity": round(price_proximity, 4),
            "soft_preference_overlap": round(soft_overlap, 4),
        },
    )


# --------------------------------------------------------------------------- #
# Bucket construction
# --------------------------------------------------------------------------- #
@dataclass
class BucketingResult:
    buckets: list[DemandBucket]
    memberships: list[BucketMembershipExplanation]
    judge_calls: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)


async def build_buckets(
    intents: list[UserIntent],
    *,
    config: DemoConfig,
    run_id: str,
    judge: JudgeFn | None = None,
) -> BucketingResult:
    """Greedy, deterministic agglomeration with an optional LLM tie-breaker."""
    drafts: list[BucketDraft] = []
    memberships: list[BucketMembershipExplanation] = []
    decisions: list[dict[str, Any]] = []
    judge_calls = 0
    rejections: dict[str, list[tuple[BucketDraft, JoinAssessment]]] = {}

    low, high = config.judge_gray_zone
    ordered = sorted(intents, key=lambda i: (i.category, i.user_id))

    for intent in ordered:
        scored: list[tuple[BucketDraft, JoinAssessment]] = []
        for draft in drafts:
            assessment = assess_join(draft, intent, config)
            scored.append((draft, assessment))

        candidates = [(d, a) for d, a in scored if a.feasible and not a.blocking_reasons]
        candidates.sort(key=lambda pair: (-pair[1].score, pair[0].user_ids))

        joined: BucketDraft | None = None
        for draft, assessment in candidates:
            accept = assessment.score >= config.bucket_merge_threshold
            if judge is not None and low <= assessment.score <= high:
                judge_calls += 1
                verdict = await judge(draft, intent, assessment)
                if verdict is not None:
                    accept = verdict
                    assessment.decided_by = "compatibility_judge"
            if accept:
                joined = draft
                draft.intents.append(intent)
                draft.join_scores.append(assessment.score)
                decisions.append(
                    {
                        "user_id": intent.user_id,
                        "action": "joined",
                        "score": assessment.score,
                        "decided_by": assessment.decided_by,
                        "components": assessment.components,
                    }
                )
                break
            rejections.setdefault(intent.user_id, []).append((draft, assessment))

        for draft, assessment in scored:
            if not assessment.feasible or assessment.blocking_reasons:
                rejections.setdefault(intent.user_id, []).append((draft, assessment))

        if joined is None:
            new_draft = BucketDraft(category=intent.category, intents=[intent], join_scores=[1.0])
            drafts.append(new_draft)
            decisions.append({"user_id": intent.user_id, "action": "new_bucket", "score": 1.0})

    buckets: list[DemandBucket] = []
    draft_ids: dict[int, str] = {}
    for draft in drafts:
        bucket = _finalize(draft, run_id=run_id, config=config)
        draft_ids[id(draft)] = bucket.bucket_id
        buckets.append(bucket)
        memberships.extend(_membership_explanations(draft, bucket))

    for user_id, entries in rejections.items():
        best = max(entries, key=lambda pair: pair[1].score, default=None)
        if best is None:
            continue
        draft, assessment = best
        bucket_id = draft_ids.get(id(draft))
        if bucket_id is None or user_id in draft.user_ids:
            continue
        reasons = (
            assessment.blocking_reasons
            or assessment.conflicts
            or [
                f"compatibility score {assessment.score:.2f} below merge threshold "
                f"{config.bucket_merge_threshold:.2f}"
            ]
        )
        memberships.append(
            BucketMembershipExplanation(
                user_id=user_id,
                bucket_id=bucket_id,
                joined=False,
                conflicts=reasons,
                explanation=(
                    f"{user_id} was not merged into {bucket_id}: " + "; ".join(reasons) + "."
                ),
            )
        )

    return BucketingResult(
        buckets=buckets, memberships=memberships, judge_calls=judge_calls, decisions=decisions
    )


def _finalize(draft: BucketDraft, *, run_id: str, config: DemoConfig) -> DemandBucket:
    hard, conflicts = draft.merged_hard()
    soft = draft.soft_constraints()
    user_ids = sorted(draft.user_ids)
    bucket_id = stable_id("bkt", run_id, draft.category, *user_ids)
    ceiling = draft.price_ceiling()
    currency = draft.intents[0].currency if draft.intents else config.currency

    return DemandBucket(
        bucket_id=bucket_id,
        category=draft.category,
        label=bucket_label(draft.category, hard, ceiling, currency),
        member_intent_ids=[i.intent_id for i in draft.intents],
        member_user_ids=[i.user_id for i in draft.intents],
        demand_quantity=sum(max(1, i.quantity) for i in draft.intents),
        shared_hard_constraints=hard,
        compatible_soft_constraints=soft,
        price_ceiling=ceiling,
        target_price=draft.target_price(),
        currency=currency,
        compatibility_score=round(sum(draft.join_scores) / max(len(draft.join_scores), 1), 4),
        compatibility_explanation=_bucket_explanation(draft, hard),
        conflicts=conflicts,
        created_at=utcnow(),
    )


def bucket_label(
    category: str,
    hard: list[RequirementConstraint],
    ceiling: Decimal | None,
    currency: str,
) -> str:
    """Deterministic, human-readable bucket name.

    This is user-facing — on a demand front door it is what a person is told they
    joined — so it names the requirements that actually define the group.
    """
    by_key = {c.key: c for c in hard}
    parts = (
        _wearable_label_parts(by_key)
        if category == CATEGORY_WEARABLE
        else _monitor_label_parts(by_key)
    )
    noun = category
    if parts and category == CATEGORY_WEARABLE and parts[-1] in ("ring", "watch", "band"):
        noun = parts.pop()
    label = " ".join(parts) if parts else "flexible"
    suffix = f" ≤ {_num(ceiling)} {currency}" if ceiling is not None else ""
    return f"{label} {noun}s{suffix}"


def _monitor_label_parts(by_key: dict[str, RequirementConstraint]) -> list[str]:
    parts: list[str] = []
    size = by_key.get("display.size_in")
    if size is not None and size.operator == ConstraintOperator.GTE:
        parts.append(f'{_num(size.value)}"+')
    elif size is not None:
        parts.append(f'{_num(size.value)}"')
    resolution = by_key.get("display.resolution")
    if resolution is not None:
        parts.append(resolution_label(resolution.value).split(" / ")[0])
    refresh = by_key.get("display.refresh_rate_hz")
    if refresh is not None:
        parts.append(f"{_num(refresh.value)}Hz+")
    if by_key.get("connectivity.usb_c_power_delivery"):
        parts.append("USB-C")
    if by_key.get("adaptive_sync.freesync") or by_key.get("adaptive_sync.gsync"):
        parts.append("adaptive sync")
    return parts


def _wearable_label_parts(by_key: dict[str, RequirementConstraint]) -> list[str]:
    parts: list[str] = []
    subscription = by_key.get("wearable.subscription_required")
    if subscription is not None and not subscription.value:
        parts.append("subscription-free")
    if by_key.get("sensors.sleep_tracking"):
        parts.append("sleep-tracking")
    if by_key.get("sensors.gps"):
        parts.append("GPS")
    if by_key.get("sensors.ecg"):
        parts.append("ECG")
    battery = by_key.get("wearable.battery_days")
    if battery is not None and battery.operator == ConstraintOperator.GTE:
        parts.append(f"{_num(battery.value)}-day battery")
    if by_key.get("material.titanium"):
        parts.append("titanium")
    form = by_key.get("wearable.form_factor")
    if form is not None:
        # The form factor pluralises the label ("sleep-tracking rings"), so it is
        # handled by the caller's category suffix instead of repeated here.
        parts.append(str(form.value))
    return parts


def _num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def _bucket_explanation(draft: BucketDraft, hard: list[RequirementConstraint]) -> str:
    members = len(draft.intents)
    shared = [c for c in hard if len(c.required_by_user_ids) > 1 and c.key != "price.unit_price"]
    individual = [
        c for c in hard if len(c.required_by_user_ids) == 1 and c.key != "price.unit_price"
    ]
    lines = [
        f"{members} users were grouped because a single product can satisfy every hard "
        "requirement they stated."
        if members != 1
        else "This user forms a bucket of their own; no other request was compatible."
    ]
    self_conflicts = draft.merged_hard()[1]
    if self_conflicts:
        lines.append(
            "This request contradicts itself, so no product can satisfy it: "
            + "; ".join(self_conflicts)
            + "."
        )
    if shared:
        lines.append("Shared requirements: " + ", ".join(describe(c) for c in shared) + ".")
    if individual:
        detail = ", ".join(
            f"{describe(c)} (required by {'/'.join(c.required_by_user_ids)})" for c in individual
        )
        lines.append(
            f"Requirements held by individual members that still bind the whole group: {detail}."
        )
    ceiling = draft.price_ceiling()
    if ceiling is not None:
        lines.append(
            f"The group price ceiling is the strictest member budget: "
            f"{_num(ceiling)} {draft.intents[0].currency}."
        )
    return " ".join(lines)


def _membership_explanations(
    draft: BucketDraft, bucket: DemandBucket
) -> list[BucketMembershipExplanation]:
    hard = bucket.shared_hard_constraints
    out: list[BucketMembershipExplanation] = []
    for intent in draft.intents:
        common = [
            describe(c)
            for c in hard
            if intent.user_id in c.required_by_user_ids and len(c.required_by_user_ids) > 1
        ]
        individual = [describe(c) for c in hard if c.required_by_user_ids == [intent.user_id]]
        inherited = [
            f"{describe(c)} (from {'/'.join(c.required_by_user_ids)})"
            for c in hard
            if intent.user_id not in c.required_by_user_ids
        ]
        explanation = (
            f"{intent.user_id} joined {bucket.label}. "
            + (f"Requirements shared with the group: {', '.join(common)}. " if common else "")
            + (
                f"Own requirements preserved for the whole group: {', '.join(individual)}. "
                if individual
                else ""
            )
            + (
                f"Also accepts stricter requirements from other members: {', '.join(inherited)}."
                if inherited
                else ""
            )
        ).strip()
        out.append(
            BucketMembershipExplanation(
                user_id=intent.user_id,
                bucket_id=bucket.bucket_id,
                joined=True,
                common_requirements=common,
                individual_requirements_preserved=individual,
                conflicts=[],
                explanation=explanation,
            )
        )
    return out


def requirement_summary(bucket: DemandBucket) -> list[str]:
    """One line per binding requirement, for campaign/report rendering."""
    lines = []
    for constraint in bucket.shared_hard_constraints:
        if constraint.key == "price.unit_price":
            continue
        who = (
            f" (required by {len(constraint.required_by_user_ids)}/"
            f"{len(bucket.member_user_ids)} buyers)"
            if constraint.required_by_user_ids
            else ""
        )
        lines.append(f"{describe(constraint)}{who}")
    if bucket.price_ceiling is not None:
        lines.append(
            f"{human_label('price.unit_price')} ≤ {_num(bucket.price_ceiling)} {bucket.currency}"
        )
    return lines
