"""Product matching.

Deterministic evaluation of every candidate product against the bucket's binding
constraints. The rules, in order:

* a failed technical hard constraint  -> ``rejected``
* an unverifiable technical hard spec -> ``rejected`` (unknown never passes silently)
* only the price is above the ceiling -> ``negotiable_gap`` (that is what suppliers are for)
* everything passes                   -> ``qualified``
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sye.config import DemoConfig
from sye.domain.enums import EvaluationResult, MatchClassification
from sye.domain.ids import stable_id
from sye.domain.models import (
    ConstraintEvaluation,
    DemandBucket,
    ProductCandidate,
    ProductMatch,
    RequirementConstraint,
)
from sye.services import scoring
from sye.services.constraints import describe, evaluate

PRICE_KEY = "price.unit_price"


def currency_mismatch(product: ProductCandidate, bucket_currency: str | None) -> bool:
    """True when the product's price cannot be compared with the group's budget."""
    return bool(
        product.normal_market_price is not None
        and product.currency
        and bucket_currency
        and product.currency != bucket_currency
    )


def product_attributes(
    product: ProductCandidate, bucket_currency: str | None = None
) -> dict[str, Any]:
    """Product attributes plus its price, so price is evaluated by the same algebra.

    A price quoted in another currency is deliberately *not* injected: comparing
    218.99 GBP with a 440 EUR ceiling would silently pass a product nobody priced.
    """
    attributes = dict(product.attributes)
    if product.normal_market_price is not None and not currency_mismatch(product, bucket_currency):
        attributes.setdefault(PRICE_KEY, float(product.normal_market_price))
    return attributes


def evaluate_product(
    bucket: DemandBucket, product: ProductCandidate, config: DemoConfig, *, run_id: str
) -> ProductMatch:
    attributes = product_attributes(product, bucket.currency)
    mismatched_currency = currency_mismatch(product, bucket.currency)

    hard_results: list[ConstraintEvaluation] = []
    for constraint in bucket.shared_hard_constraints:
        result = evaluate(constraint, attributes)
        if constraint.key == PRICE_KEY:
            if mismatched_currency:
                result = result.model_copy(
                    update={
                        "observed": f"{product.normal_market_price} {product.currency}",
                        "explanation": (
                            f"Listed at {product.normal_market_price} {product.currency}, "
                            f"which cannot be compared with the group's {bucket.currency} "
                            "ceiling without an exchange rate."
                        ),
                    }
                )
            else:
                result = _soften_price(result, constraint, product, bucket, config)
        hard_results.append(result)

    soft_results = [
        evaluate(constraint, attributes) for constraint in bucket.compatible_soft_constraints
    ]

    rejection_reasons = [e.explanation for e in hard_results if e.result == EvaluationResult.FAIL]
    unknown_specs = [e.constraint_key for e in hard_results if e.result == EvaluationResult.UNKNOWN]
    negotiable_gaps = [
        e.explanation for e in hard_results if e.result == EvaluationResult.NEGOTIABLE
    ]

    if rejection_reasons:
        classification = MatchClassification.REJECTED
    elif unknown_specs:
        classification = MatchClassification.REJECTED
        rejection_reasons = [
            next(
                (
                    e.explanation
                    for e in hard_results
                    if e.constraint_key == key and e.result == EvaluationResult.UNKNOWN
                ),
                f"{key} could not be verified",
            )
            if key == PRICE_KEY
            else f"{key} could not be verified; unknown critical specs are not accepted"
            for key in unknown_specs
        ]
    elif negotiable_gaps:
        classification = MatchClassification.NEGOTIABLE_GAP
    else:
        classification = MatchClassification.QUALIFIED

    soft_weights = {c.key: c.weight for c in bucket.compatible_soft_constraints}
    soft_score = scoring.weighted_soft_score(soft_results, soft_weights)
    hard_ratio = scoring.hard_pass_ratio([e for e in hard_results if e.constraint_key != PRICE_KEY])
    price_score = scoring.price_fit(
        None if mismatched_currency else product.normal_market_price,
        bucket.price_ceiling,
        bucket.target_price,
        config.price_negotiable_headroom,
    )

    return ProductMatch(
        match_id=stable_id("mch", run_id, bucket.bucket_id, product.product_id),
        bucket_id=bucket.bucket_id,
        product_id=product.product_id,
        product_name=product.canonical_name,
        classification=classification,
        hard_constraint_results=hard_results,
        soft_constraint_results=soft_results,
        soft_constraint_score=soft_score,
        overall_score=scoring.overall_match_score(hard_ratio, soft_score, price_score),
        negotiable_gaps=negotiable_gaps,
        rejection_reasons=rejection_reasons,
        unknown_specs=unknown_specs,
        explanation=_deterministic_explanation(
            product, classification, hard_results, negotiable_gaps, rejection_reasons
        ),
    )


def _soften_price(
    result: ConstraintEvaluation,
    constraint: RequirementConstraint,
    product: ProductCandidate,
    bucket: DemandBucket,
    config: DemoConfig,
) -> ConstraintEvaluation:
    """Price above the ceiling is a commercial gap, not a technical rejection —
    unless it is so far above that no plausible negotiation closes it."""
    if result.result != EvaluationResult.FAIL or product.normal_market_price is None:
        return result
    ceiling = Decimal(constraint.value)
    limit = ceiling * Decimal(str(1.0 + config.price_negotiable_headroom))
    price = Decimal(product.normal_market_price)
    if price <= limit:
        gap = price - ceiling
        percent = float(gap / ceiling * 100) if ceiling else 0.0
        return result.model_copy(
            update={
                "result": EvaluationResult.NEGOTIABLE,
                "explanation": (
                    f"Market price {price} {bucket.currency} is {percent:.1f}% above the group "
                    f"ceiling {ceiling} {bucket.currency} — a commercial gap to negotiate, not a "
                    "technical failure."
                ),
            }
        )
    return result.model_copy(
        update={
            "explanation": (
                f"Market price {price} {bucket.currency} exceeds the group ceiling "
                f"{ceiling} {bucket.currency} by more than the "
                f"{config.price_negotiable_headroom:.0%} negotiation headroom."
            )
        }
    )


def _deterministic_explanation(
    product: ProductCandidate,
    classification: MatchClassification,
    hard_results: list[ConstraintEvaluation],
    negotiable_gaps: list[str],
    rejection_reasons: list[str],
) -> str:
    passed = sum(1 for e in hard_results if e.result == EvaluationResult.PASS)
    total = len(hard_results)
    head = f"{product.canonical_name} passed {passed}/{total} hard constraints"
    if classification == MatchClassification.QUALIFIED:
        return f"{head}; qualified."
    if classification == MatchClassification.NEGOTIABLE_GAP:
        return f"{head}; technically qualified with a commercial gap: {negotiable_gaps[0]}"
    return f"{head}; rejected: {rejection_reasons[0] if rejection_reasons else 'unknown reason'}"


def evaluate_bucket(
    bucket: DemandBucket,
    products: list[ProductCandidate],
    config: DemoConfig,
    *,
    run_id: str,
) -> list[ProductMatch]:
    matches = [evaluate_product(bucket, p, config, run_id=run_id) for p in products]
    return rank_matches(matches)


_CLASS_ORDER = {
    MatchClassification.QUALIFIED: 0,
    MatchClassification.NEGOTIABLE_GAP: 1,
    MatchClassification.REJECTED: 2,
}


def rank_matches(matches: list[ProductMatch]) -> list[ProductMatch]:
    """Qualified first, then best score.

    Ties break on the product *name*, not its id: ids are scoped to a run, so an
    id-based tie-break would silently reorder equal candidates between runs and make
    the winning campaign depend on the run id rather than on the seed.
    """
    return sorted(
        matches,
        key=lambda m: (
            _CLASS_ORDER[m.classification],
            -m.overall_score,
            m.product_name,
            m.product_id,
        ),
    )


def viable_matches(matches: list[ProductMatch]) -> list[ProductMatch]:
    return [m for m in rank_matches(matches) if m.classification != MatchClassification.REJECTED]


def describe_requirements(bucket: DemandBucket) -> list[str]:
    return [describe(c) for c in bucket.shared_hard_constraints]
