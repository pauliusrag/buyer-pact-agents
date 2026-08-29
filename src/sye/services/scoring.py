"""Transparent scoring formulas.

Every number the demo shows can be traced to one of these functions. No LLM is
involved in scoring.
"""

from __future__ import annotations

from decimal import Decimal

from sye.domain.enums import EvaluationResult
from sye.domain.models import ConstraintEvaluation

# Product match weights
MATCH_WEIGHTS = {"hard": 0.55, "soft": 0.25, "price": 0.20}


def weighted_soft_score(
    evaluations: list[ConstraintEvaluation], weights: dict[str, float]
) -> float:
    """Fraction of soft-preference weight that the product actually satisfies."""
    total = sum(weights.get(e.constraint_key, 1.0) for e in evaluations)
    if total <= 0:
        return 1.0
    earned = sum(
        weights.get(e.constraint_key, 1.0) for e in evaluations if e.result == EvaluationResult.PASS
    )
    return round(earned / total, 4)


def hard_pass_ratio(evaluations: list[ConstraintEvaluation]) -> float:
    if not evaluations:
        return 1.0
    passed = sum(1 for e in evaluations if e.result == EvaluationResult.PASS)
    return round(passed / len(evaluations), 4)


def price_fit(
    price: Decimal | None, ceiling: Decimal | None, target: Decimal | None, headroom: float
) -> float:
    """1.0 at or below target, decaying to 0 at ``ceiling * (1 + headroom)``."""
    if price is None:
        return 0.5
    if ceiling is None:
        return 0.7
    reference = target if target is not None else ceiling
    if price <= reference:
        return 1.0
    limit = Decimal(ceiling) * Decimal(str(1.0 + headroom))
    if price >= limit:
        return 0.0
    span = limit - Decimal(reference)
    if span <= 0:
        return 0.0
    return round(float((limit - Decimal(price)) / span), 4)


def overall_match_score(hard_ratio: float, soft_score: float, price_score: float) -> float:
    return round(
        MATCH_WEIGHTS["hard"] * hard_ratio
        + MATCH_WEIGHTS["soft"] * soft_score
        + MATCH_WEIGHTS["price"] * price_score,
        4,
    )


def delivery_score(days: int | None) -> float:
    if days is None:
        return 0.5
    if days <= 5:
        return 1.0
    if days >= 30:
        return 0.0
    return round(1.0 - (days - 5) / 25.0, 4)


def coverage_score(max_quantity: int | None, demand: int) -> float:
    if max_quantity is None:
        return 0.6
    if demand <= 0:
        return 1.0
    return round(min(1.0, max_quantity / demand), 4)


def warranty_score(months: int | None) -> float:
    if months is None:
        return 0.3
    return round(min(1.0, months / 36.0), 4)
