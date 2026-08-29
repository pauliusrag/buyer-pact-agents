"""Bucketing: pure deterministic behaviour, no LLM involved."""

from __future__ import annotations

from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.enums import ConstraintOperator, Importance
from sye.services.bucketing import build_buckets, hard_constraints_of
from sye.services.constraints import merge_hard_constraints
from tests.conftest import constraint, make_intent

CONFIG = DemoConfig(offline=True, write_snapshots=False)


async def build(intents, config: DemoConfig = CONFIG):
    return await build_buckets(intents, config=config, run_id="run_test")


async def test_three_compatible_requests_merge():
    intents = [
        make_intent(
            "u1",
            [constraint("display.size_in", ConstraintOperator.GTE, 27)],
            max_budget=Decimal("300"),
        ),
        make_intent(
            "u2",
            [constraint("display.resolution", ConstraintOperator.GTE, "2560x1440")],
            max_budget=Decimal("280"),
        ),
        make_intent(
            "u3",
            [
                constraint("display.size_in", ConstraintOperator.GTE, 27),
                constraint("display.resolution", ConstraintOperator.GTE, "2560x1440"),
            ],
            max_budget=Decimal("320"),
        ),
    ]
    result = await build(intents)

    assert len(result.buckets) == 1
    bucket = result.buckets[0]
    assert sorted(bucket.member_user_ids) == ["u1", "u2", "u3"]
    assert bucket.price_ceiling == Decimal("280")  # the strictest member budget binds
    assert bucket.demand_quantity == 3
    assert bucket.compatibility_explanation


async def test_contradictory_size_constraints_split():
    intents = [
        make_intent("u1", [constraint("display.size_in", ConstraintOperator.GTE, 32)]),
        make_intent("u2", [constraint("display.size_in", ConstraintOperator.LTE, 24)]),
    ]
    result = await build(intents)

    assert len(result.buckets) == 2
    rejection = next(m for m in result.memberships if not m.joined)
    assert "screen size" in rejection.conflicts[0]


async def test_hard_price_floor_and_ceiling_do_not_merge():
    """€150 hard maximum vs a €300 hard minimum is infeasible, not merely distant."""
    cheap = make_intent("u1", [constraint("price.unit_price", ConstraintOperator.LTE, 150)])
    premium = make_intent("u2", [constraint("price.unit_price", ConstraintOperator.GTE, 300)])
    merged, conflicts = merge_hard_constraints(
        hard_constraints_of(cheap) + hard_constraints_of(premium)
    )
    assert conflicts

    result = await build([cheap, premium])
    assert len(result.buckets) == 2


async def test_soft_brand_preferences_do_not_split_a_group():
    intents = [
        make_intent(
            "u1",
            [
                constraint("display.size_in", ConstraintOperator.GTE, 27),
                constraint(
                    "display.panel_type",
                    ConstraintOperator.EQ,
                    "ips",
                    importance=Importance.SOFT,
                ),
            ],
            max_budget=Decimal("300"),
        ),
        make_intent(
            "u2",
            [
                constraint("display.size_in", ConstraintOperator.GTE, 27),
                constraint(
                    "display.panel_type",
                    ConstraintOperator.EQ,
                    "va",
                    importance=Importance.SOFT,
                ),
            ],
            max_budget=Decimal("290"),
        ),
    ]
    intents[0].named_brands = ["Dell"]
    intents[1].named_brands = ["LG"]

    result = await build(intents)
    assert len(result.buckets) == 1
    assert len(result.buckets[0].member_user_ids) == 2


async def test_a_single_member_hard_requirement_binds_the_whole_bucket():
    intents = [
        make_intent(
            "u1",
            [constraint("display.size_in", ConstraintOperator.GTE, 27)],
            max_budget=Decimal("300"),
        ),
        make_intent(
            "u2",
            [
                constraint("display.size_in", ConstraintOperator.GTE, 27),
                constraint("connectivity.usb_c_power_delivery", ConstraintOperator.BOOLEAN, True),
            ],
            max_budget=Decimal("300"),
        ),
    ]
    result = await build(intents)

    bucket = result.buckets[0]
    binding = {c.key for c in bucket.shared_hard_constraints}
    assert "connectivity.usb_c_power_delivery" in binding

    membership = next(m for m in result.memberships if m.user_id == "u1" and m.joined)
    assert any("USB-C" in text for text in membership.explanation.split("."))


async def test_different_categories_never_merge():
    intents = [
        make_intent("u1", [constraint("display.size_in", ConstraintOperator.GTE, 27)]),
        make_intent("u2", [], category="keyboard"),
    ]
    result = await build(intents)
    assert len(result.buckets) == 2
    assert {b.category for b in result.buckets} == {"monitor", "keyboard"}


async def test_price_tiers_far_apart_do_not_merge():
    intents = [
        make_intent(
            "u1",
            [constraint("display.size_in", ConstraintOperator.GTE, 27)],
            max_budget=Decimal("250"),
        ),
        make_intent(
            "u2",
            [constraint("display.size_in", ConstraintOperator.GTE, 27)],
            max_budget=Decimal("900"),
        ),
    ]
    result = await build(intents)
    assert len(result.buckets) == 2


async def test_bucketing_is_order_independent_and_stable():
    intents = [
        make_intent(
            f"u{i}",
            [constraint("display.size_in", ConstraintOperator.GTE, 27)],
            max_budget=Decimal("300"),
        )
        for i in range(1, 5)
    ]
    first = await build(list(intents))
    second = await build(list(reversed(intents)))

    assert [b.bucket_id for b in first.buckets] == [b.bucket_id for b in second.buckets]
    assert first.buckets[0].member_user_ids == second.buckets[0].member_user_ids
