"""Seeded supplier simulation.

All simulated commercial economics are computed here, in plain Python, from:
normal market price, demand quantity, the supplier's seeded profile and the
negotiation round. The LLM may write the negotiation *message*; it never invents
a price.

The same ``(seed, supplier_id, product_id, quantity, round)`` always produces the
same numbers.
"""

from __future__ import annotations

import hashlib
import random
from decimal import ROUND_HALF_UP, Decimal

from sye.domain.models import SimulatedSupplierProfile, SupplierCandidate

CENTS = Decimal("0.01")

_TYPE_BIAS: dict[str, dict[str, float]] = {
    # manufacturers and distributors have more room than retailers
    "manufacturer": {"flex": 0.16, "max_discount": 0.30, "moq": 8},
    "distributor": {"flex": 0.13, "max_discount": 0.26, "moq": 6},
    "retailer": {"flex": 0.08, "max_discount": 0.17, "moq": 4},
    "marketplace_seller": {"flex": 0.10, "max_discount": 0.20, "moq": 3},
    "unknown": {"flex": 0.09, "max_discount": 0.18, "moq": 5},
}


def _rng(*parts: object) -> random.Random:
    """A Random seeded by a stable hash — ``hash()`` is salted per process, so no."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


def seed_key_for(supplier: SupplierCandidate) -> str:
    """Stable simulation key: the supplier's name, not its run-scoped id."""
    return supplier.name.strip().lower()


def profile_for(supplier: SupplierCandidate, *, seed: int) -> SimulatedSupplierProfile:
    """Derive a stable negotiation personality for a supplier.

    Keyed by supplier name so ``--seed 42`` reproduces the same economics in every
    run of the same scenario.
    """
    bias = _TYPE_BIAS.get(supplier.supplier_type, _TYPE_BIAS["unknown"])
    key = seed_key_for(supplier)
    rng = _rng(seed, "profile", key)

    margin_flexibility = round(bias["flex"] + rng.uniform(-0.03, 0.05), 4)
    max_discount = round(bias["max_discount"] + rng.uniform(-0.04, 0.06), 4)
    moq = int(bias["moq"] + rng.randint(-2, 3))
    fast = rng.randint(2, 5)
    slow = fast + rng.randint(2, 9)
    warranty = rng.choice([12, 24, 24, 36])
    stubbornness = round(rng.uniform(0.25, 0.8), 4)
    shipping_per_unit = money(rng.choice([0, 0, 4.5, 7.9, 9.9]))

    return SimulatedSupplierProfile(
        supplier_id=supplier.supplier_id,
        seed_key=key,
        margin_flexibility=max(0.02, margin_flexibility),
        min_quantity_for_discount=max(2, moq),
        max_discount_percent=max(0.05, min(0.42, max_discount)),
        shipping_days=(fast, slow),
        warranty_months=warranty,
        negotiation_stubbornness=stubbornness,
        base_markup=round(1.0 + rng.uniform(-0.03, 0.04), 4),
        shipping_cost_per_unit=shipping_per_unit,
    )


def volume_coverage(quantity: int, profile: SimulatedSupplierProfile) -> float:
    """How well the aggregated demand covers the supplier's discount threshold."""
    return min(1.0, quantity / max(profile.min_quantity_for_discount, 1))


def discount_fraction(
    profile: SimulatedSupplierProfile, *, quantity: int, negotiation_round: int, seed: int
) -> float:
    """Fraction of the reference price the supplier is willing to give up."""
    coverage = volume_coverage(quantity, profile)
    opening = profile.max_discount_percent * (0.35 + 0.40 * coverage)
    concession = (1.0 - profile.negotiation_stubbornness) * profile.margin_flexibility
    total = opening + concession * max(0, negotiation_round - 1)
    noise = _rng(
        seed, "discount", profile.seed_key or profile.supplier_id, quantity, negotiation_round
    ).uniform(-0.012, 0.012)
    return max(0.0, min(profile.max_discount_percent, total + noise))


def floor_price(profile: SimulatedSupplierProfile, reference_price: Decimal) -> Decimal:
    """The lowest unit price this supplier will ever accept."""
    return money(reference_price * Decimal(str(1.0 - profile.max_discount_percent)))


def offer_price(
    profile: SimulatedSupplierProfile,
    *,
    reference_price: Decimal,
    quantity: int,
    negotiation_round: int,
    seed: int,
) -> Decimal:
    base = money(reference_price * Decimal(str(profile.base_markup)))
    fraction = discount_fraction(
        profile, quantity=quantity, negotiation_round=negotiation_round, seed=seed
    )
    return money(base * Decimal(str(1.0 - fraction)))


def respond_to_counter(
    profile: SimulatedSupplierProfile,
    *,
    reference_price: Decimal,
    current_price: Decimal,
    requested_price: Decimal,
    quantity: int,
    negotiation_round: int,
    seed: int,
) -> tuple[Decimal, str]:
    """Supplier's deterministic reaction to a counter-offer.

    Returns ``(new_unit_price, outcome)`` where outcome is one of
    ``accepted``, ``partial`` or ``held``.
    """
    floor = floor_price(profile, reference_price)
    scheduled = offer_price(
        profile,
        reference_price=reference_price,
        quantity=quantity,
        negotiation_round=negotiation_round,
        seed=seed,
    )
    best_possible = max(floor, min(scheduled, current_price))

    if requested_price >= current_price:
        return current_price, "held"
    if requested_price >= best_possible:
        return money(requested_price), "accepted"

    # Meet part-way, but never below the floor.
    give = Decimal(str(1.0 - profile.negotiation_stubbornness))
    midpoint = current_price - (current_price - requested_price) * give
    new_price = money(max(floor, best_possible, midpoint))
    if new_price >= current_price:
        return current_price, "held"
    return new_price, "partial"


def shipping_total(profile: SimulatedSupplierProfile, quantity: int) -> Decimal:
    """Free shipping once the order clears the supplier's volume threshold."""
    if quantity >= profile.min_quantity_for_discount:
        return money(profile.shipping_cost_per_unit * quantity * Decimal("0.5"))
    return money(profile.shipping_cost_per_unit * quantity)


def delivery_days(profile: SimulatedSupplierProfile, *, quantity: int, seed: int) -> int:
    fast, slow = profile.shipping_days
    rng = _rng(seed, "delivery", profile.seed_key or profile.supplier_id, quantity)
    return int(rng.randint(fast, slow))
