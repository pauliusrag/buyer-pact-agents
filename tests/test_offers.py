"""Offer normalisation and seeded supplier simulation."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sye.config import DemoConfig
from sye.domain.enums import ConstraintOperator
from sye.domain.ids import utcnow
from sye.domain.models import RFQ, DemandBucket, SupplierOffer
from sye.integrations.simulated_supplier_gateway import SimulatedSupplierGateway
from sye.services import simulation
from sye.services.constraints import merge_hard_constraints
from sye.services.offer_normalizer import (
    best_evaluation,
    evaluate_offers,
    landed_unit_cost,
)
from tests.conftest import constraint, make_product, make_supplier

CONFIG = DemoConfig(offline=True, seed=42, write_snapshots=False)


def make_bucket(quantity: int = 5, ceiling: str = "300") -> DemandBucket:
    return DemandBucket(
        bucket_id="bkt_offers",
        category="monitor",
        label="offer test bucket",
        member_intent_ids=[f"int_{i}" for i in range(quantity)],
        member_user_ids=[f"u{i}" for i in range(quantity)],
        demand_quantity=quantity,
        shared_hard_constraints=merge_hard_constraints(
            [constraint("price.unit_price", ConstraintOperator.LTE, int(ceiling))]
        )[0],
        compatible_soft_constraints=[],
        price_ceiling=Decimal(ceiling),
        target_price=Decimal(ceiling) * Decimal("0.9"),
        currency="EUR",
        compatibility_score=1.0,
        compatibility_explanation="test",
        created_at=utcnow(),
    )


def offer(offer_id: str, unit: str, *, shipping: str = "0", days: int = 5, warranty: int = 24):
    return SupplierOffer(
        offer_id=offer_id,
        rfq_id="rfq_1",
        supplier_id=f"sup_{offer_id}",
        product_id="prd_1",
        unit_price=Decimal(unit),
        currency="EUR",
        max_quantity=50,
        shipping_cost_total=Decimal(shipping),
        estimated_delivery_days=days,
        warranty_months=warranty,
        returns_policy_summary="14-day returns",
        expires_at=utcnow(),
        negotiation_round=1,
    )


def test_landed_cost_includes_shipping_per_unit():
    assert landed_unit_cost(offer("o1", "200", shipping="50"), 5) == Decimal("210.00")


def test_cheap_headline_price_loses_to_lower_landed_cost():
    bucket = make_bucket(quantity=5)
    offers = [offer("o_cheap", "195", shipping="200"), offer("o_fair", "205", shipping="0")]
    evaluations = {e.offer_id: e for e in evaluate_offers(offers, bucket, CONFIG)}

    assert evaluations["o_cheap"].landed_unit_cost == Decimal("235.00")
    assert evaluations["o_fair"].landed_unit_cost == Decimal("205.00")
    assert best_evaluation(list(evaluations.values())).offer_id == "o_fair"


def test_offer_above_ceiling_does_not_qualify():
    bucket = make_bucket(quantity=3, ceiling="200")
    evaluations = evaluate_offers([offer("o_high", "260")], bucket, CONFIG)

    assert evaluations[0].qualifies is False
    assert evaluations[0].disqualification_reasons
    assert best_evaluation(evaluations) is None


def test_insufficient_quantity_disqualifies():
    bucket = make_bucket(quantity=10)
    small = offer("o_small", "150")
    small.max_quantity = 3
    evaluations = evaluate_offers([small], bucket, CONFIG)

    assert evaluations[0].qualifies is False
    assert "3 of 10" in evaluations[0].disqualification_reasons[0]


def test_scores_are_deterministic_for_identical_input():
    bucket = make_bucket()
    offers = [offer("o1", "200"), offer("o2", "220", days=20, warranty=12)]
    first = evaluate_offers(offers, bucket, CONFIG)
    second = evaluate_offers(offers, bucket, CONFIG)

    assert [e.model_dump() for e in first] == [e.model_dump() for e in second]
    assert first[0].overall_score > first[1].overall_score


# -- simulation ------------------------------------------------------------- #
def build_gateway(seed: int) -> tuple[SimulatedSupplierGateway, RFQ, object]:
    product = make_product("Sim Monitor", price=300.0, attributes={"display.size_in": 27})
    gateway = SimulatedSupplierGateway(
        seed=seed, products={product.product_id: product}, run_id="run_sim"
    )
    rfq = RFQ(
        rfq_id="rfq_sim",
        bucket_id="bkt_sim",
        product_ids=[product.product_id],
        quantity=8,
        requested_currency="EUR",
        requested_target_unit_price=Decimal("240"),
        summary="test",
        status="simulation_ready",
    )
    return gateway, rfq, make_supplier("Sim Supplier AB", "distributor")


def test_same_seed_produces_the_same_offer_sequence():
    results = []
    for _ in range(2):
        gateway, rfq, supplier = build_gateway(42)
        offers = asyncio.run(gateway.request_offer(rfq, supplier))
        results.append(
            (offers.unit_price, offers.shipping_cost_total, offers.estimated_delivery_days)
        )
    assert results[0] == results[1]


def test_different_seed_changes_the_simulation():
    gateway_a, rfq, supplier = build_gateway(42)
    gateway_b, _, _ = build_gateway(4242)
    a = asyncio.run(gateway_a.request_offer(rfq, supplier))
    b = asyncio.run(gateway_b.request_offer(rfq, supplier))
    assert a.unit_price != b.unit_price


def test_offers_are_marked_simulated_and_never_delivered():
    gateway, rfq, supplier = build_gateway(42)
    result = asyncio.run(gateway.request_offer(rfq, supplier))
    assert result.data_origin.value == "simulated"
    assert "seed=42" in (result.source_reference or "")
    assert any("Simulated quote" in c for c in result.conditions)


def test_volume_increases_the_discount():
    product = make_product("Sim", price=300.0, attributes={})
    supplier = make_supplier("Volume Supplier", "distributor")
    profile = simulation.profile_for(supplier, seed=42)
    small = simulation.offer_price(
        profile, reference_price=Decimal("300"), quantity=1, negotiation_round=1, seed=42
    )
    large = simulation.offer_price(
        profile,
        reference_price=Decimal("300"),
        quantity=profile.min_quantity_for_discount * 4,
        negotiation_round=1,
        seed=42,
    )
    assert large < small
    assert product.normal_market_price == Decimal("300.0")


def test_supplier_never_goes_below_its_floor():
    supplier = make_supplier("Stubborn Supplier", "retailer")
    profile = simulation.profile_for(supplier, seed=42)
    floor = simulation.floor_price(profile, Decimal("300"))
    price, outcome = simulation.respond_to_counter(
        profile,
        reference_price=Decimal("300"),
        current_price=Decimal("280"),
        requested_price=Decimal("10"),
        quantity=10,
        negotiation_round=2,
        seed=42,
    )
    assert price >= floor
    assert outcome in {"partial", "held"}


def test_counter_at_or_above_current_price_is_held():
    supplier = make_supplier("Firm Supplier", "distributor")
    profile = simulation.profile_for(supplier, seed=42)
    price, outcome = simulation.respond_to_counter(
        profile,
        reference_price=Decimal("300"),
        current_price=Decimal("260"),
        requested_price=Decimal("290"),
        quantity=5,
        negotiation_round=2,
        seed=42,
    )
    assert outcome == "held"
    assert price == Decimal("260")
