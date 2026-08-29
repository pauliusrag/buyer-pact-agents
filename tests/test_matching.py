"""Matching: pass / fail / unknown / negotiable, and the rules built on them."""

from __future__ import annotations

from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.enums import (
    ConstraintOperator,
    EvaluationResult,
    Importance,
    MatchClassification,
)
from sye.domain.ids import utcnow
from sye.domain.models import DemandBucket
from sye.services.constraints import evaluate, merge_hard_constraints
from sye.services.matching import evaluate_product, rank_matches, viable_matches
from tests.conftest import constraint, make_product

CONFIG = DemoConfig(offline=True, write_snapshots=False)


def bucket(constraints, *, ceiling="300", target="270", soft=()) -> DemandBucket:
    return DemandBucket(
        bucket_id="bkt_test",
        category="monitor",
        label="test bucket",
        member_intent_ids=["int_1", "int_2"],
        member_user_ids=["u1", "u2"],
        demand_quantity=2,
        shared_hard_constraints=merge_hard_constraints(list(constraints))[0],
        compatible_soft_constraints=list(soft),
        price_ceiling=Decimal(ceiling) if ceiling else None,
        target_price=Decimal(target) if target else None,
        currency="EUR",
        compatibility_score=0.9,
        compatibility_explanation="test",
        created_at=utcnow(),
    )


def test_constraint_pass_and_fail():
    size = constraint("display.size_in", ConstraintOperator.GTE, 27)
    assert evaluate(size, {"display.size_in": 32}).result == EvaluationResult.PASS
    assert evaluate(size, {"display.size_in": 24}).result == EvaluationResult.FAIL


def test_missing_spec_is_unknown_not_pass():
    usb_c = constraint("connectivity.usb_c_power_delivery", ConstraintOperator.BOOLEAN, True)
    result = evaluate(usb_c, {"display.size_in": 27})
    assert result.result == EvaluationResult.UNKNOWN
    assert "unknown" in result.explanation.lower()


def test_thunderbolt_substitutes_for_usb_c_power_delivery():
    usb_c = constraint("connectivity.usb_c_power_delivery", ConstraintOperator.BOOLEAN, True)
    assert evaluate(usb_c, {"connectivity.thunderbolt": True}).result == EvaluationResult.PASS


def test_resolution_comparison_is_ordinal():
    qhd = constraint("display.resolution", ConstraintOperator.GTE, "2560x1440")
    assert evaluate(qhd, {"display.resolution": "4K"}).result == EvaluationResult.PASS
    assert evaluate(qhd, {"display.resolution": "1080p"}).result == EvaluationResult.FAIL


def test_qualified_product():
    target = bucket(
        [
            constraint("display.size_in", ConstraintOperator.GTE, 27),
            constraint("display.resolution", ConstraintOperator.GTE, "2560x1440"),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ]
    )
    product = make_product(
        "Good Monitor",
        price=269.0,
        attributes={"display.size_in": 27, "display.resolution": "2560x1440"},
    )
    match = evaluate_product(target, product, CONFIG, run_id="run_test")

    assert match.classification == MatchClassification.QUALIFIED
    assert not match.rejection_reasons
    assert match.overall_score > 0.5


def test_technical_failure_rejects():
    target = bucket(
        [
            constraint("connectivity.usb_c_power_delivery", ConstraintOperator.BOOLEAN, True),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ]
    )
    product = make_product(
        "No USB-C Monitor",
        price=199.0,
        attributes={
            "display.size_in": 27,
            "connectivity.usb_c_power_delivery": False,
        },
    )
    match = evaluate_product(target, product, CONFIG, run_id="run_test")

    assert match.classification == MatchClassification.REJECTED
    assert match.rejection_reasons


def test_unknown_critical_spec_does_not_qualify():
    target = bucket(
        [
            constraint("connectivity.usb_c_power_delivery", ConstraintOperator.BOOLEAN, True),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ]
    )
    product = make_product("Mystery Monitor", price=189.0, attributes={"display.size_in": 27})
    match = evaluate_product(target, product, CONFIG, run_id="run_test")

    assert match.classification == MatchClassification.REJECTED
    assert match.unknown_specs == ["connectivity.usb_c_power_delivery"]
    assert any(e.result == EvaluationResult.UNKNOWN for e in match.hard_constraint_results)


def test_price_above_ceiling_is_a_negotiable_gap():
    target = bucket(
        [
            constraint("display.size_in", ConstraintOperator.GTE, 27),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ]
    )
    product = make_product(
        "Slightly Pricey Monitor", price=349.0, attributes={"display.size_in": 27}
    )
    match = evaluate_product(target, product, CONFIG, run_id="run_test")

    assert match.classification == MatchClassification.NEGOTIABLE_GAP
    assert match.negotiable_gaps
    assert any(e.result == EvaluationResult.NEGOTIABLE for e in match.hard_constraint_results)


def test_price_far_above_ceiling_is_rejected():
    target = bucket(
        [
            constraint("display.size_in", ConstraintOperator.GTE, 27),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ]
    )
    product = make_product("Luxury Monitor", price=899.0, attributes={"display.size_in": 27})
    match = evaluate_product(target, product, CONFIG, run_id="run_test")

    assert match.classification == MatchClassification.REJECTED


def test_soft_constraints_affect_score_but_not_classification():
    soft = [
        constraint(
            "ergonomics.height_adjustable",
            ConstraintOperator.BOOLEAN,
            True,
            importance=Importance.SOFT,
        )
    ]
    target = bucket(
        [
            constraint("display.size_in", ConstraintOperator.GTE, 27),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ],
        soft=soft,
    )
    attributes = {"display.size_in": 27}
    without = evaluate_product(
        target, make_product("Plain", price=250.0, attributes=attributes), CONFIG, run_id="r"
    )
    with_feature = evaluate_product(
        target,
        make_product(
            "Ergonomic",
            price=250.0,
            attributes={**attributes, "ergonomics.height_adjustable": True},
        ),
        CONFIG,
        run_id="r",
    )

    assert without.classification == with_feature.classification == MatchClassification.QUALIFIED
    assert with_feature.overall_score > without.overall_score


def test_ranking_puts_qualified_first_and_is_stable():
    target = bucket(
        [
            constraint("display.size_in", ConstraintOperator.GTE, 27),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ]
    )
    products = [
        make_product("Too Small", price=150.0, attributes={"display.size_in": 24}),
        make_product("Pricey", price=349.0, attributes={"display.size_in": 27}),
        make_product("Perfect", price=249.0, attributes={"display.size_in": 27}),
    ]
    matches = [evaluate_product(target, p, CONFIG, run_id="r") for p in products]
    ranked = rank_matches(matches)

    assert ranked[0].classification == MatchClassification.QUALIFIED
    assert ranked[-1].classification == MatchClassification.REJECTED
    assert len(viable_matches(matches)) == 2
    assert [m.match_id for m in rank_matches(list(reversed(matches)))] == [
        m.match_id for m in ranked
    ]


def test_price_in_another_currency_is_unknown_not_a_pass():
    """A GBP price must not be compared with a EUR ceiling."""
    target = bucket(
        [
            constraint("display.size_in", ConstraintOperator.GTE, 27),
            constraint("price.unit_price", ConstraintOperator.LTE, 300),
        ]
    )
    product = make_product(
        "Foreign Currency Monitor", price=219.0, attributes={"display.size_in": 27}
    )
    product.currency = "GBP"
    match = evaluate_product(target, product, CONFIG, run_id="run_test")

    price_result = next(
        e for e in match.hard_constraint_results if e.constraint_key == "price.unit_price"
    )
    assert price_result.result == EvaluationResult.UNKNOWN
    assert "GBP" in price_result.explanation and "EUR" in price_result.explanation
    assert match.classification == MatchClassification.REJECTED
    assert "exchange rate" in match.rejection_reasons[0]


def test_missing_price_triggers_verification():
    from sye.agents.tools.product_researcher import needs_verification

    target = bucket([constraint("display.size_in", ConstraintOperator.GTE, 27)])
    priced = make_product("Priced", price=250.0, attributes={"display.size_in": 27})
    unpriced = make_product("Unpriced", price=250.0, attributes={"display.size_in": 27})
    unpriced.normal_market_price = None
    foreign = make_product("Foreign", price=250.0, attributes={"display.size_in": 27})
    foreign.currency = "GBP"

    assert needs_verification(priced, target) is False
    assert needs_verification(unpriced, target) is True
    assert needs_verification(foreign, target) is True
