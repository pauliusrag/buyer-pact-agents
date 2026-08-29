"""Simulated supplier gateway — the only gateway used in demo mode.

Nothing leaves the process: no email, no form, no order. Every object it returns
carries ``data_origin="simulated"`` and a ``source_reference`` naming the seed
that produced it, so a simulated offer can never be mistaken for a real one.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sye.domain.enums import DataOrigin
from sye.domain.ids import stable_id, utcnow
from sye.domain.models import (
    RFQ,
    NegotiationAction,
    ProductCandidate,
    SimulatedSupplierProfile,
    SupplierCandidate,
    SupplierOffer,
)
from sye.services import simulation


class SimulatedSupplierGateway:
    """Deterministic supplier behaviour driven by seeded profiles."""

    name = "simulated"

    def __init__(self, *, seed: int, products: dict[str, ProductCandidate], run_id: str) -> None:
        self.seed = seed
        self.products = products
        self.run_id = run_id
        self.profiles: dict[str, SimulatedSupplierProfile] = {}
        self.offer_count = 0

    # -- helpers ----------------------------------------------------------- #
    def profile(self, supplier: SupplierCandidate) -> SimulatedSupplierProfile:
        if supplier.supplier_id not in self.profiles:
            self.profiles[supplier.supplier_id] = simulation.profile_for(supplier, seed=self.seed)
        return self.profiles[supplier.supplier_id]

    def _reference_price(self, rfq: RFQ) -> tuple[str, Decimal]:
        """Reference (normal market) price for the RFQ's primary product."""
        for product_id in rfq.product_ids:
            product = self.products.get(product_id)
            if product and product.normal_market_price:
                return product_id, Decimal(product.normal_market_price)
        product_id = rfq.product_ids[0] if rfq.product_ids else "unknown"
        target = rfq.requested_target_unit_price or Decimal("250")
        return product_id, simulation.money(Decimal(target) * Decimal("1.25"))

    def _build_offer(
        self,
        *,
        rfq: RFQ,
        supplier: SupplierCandidate,
        profile: SimulatedSupplierProfile,
        product_id: str,
        unit_price: Decimal,
        negotiation_round: int,
    ) -> SupplierOffer:
        self.offer_count += 1
        quantity = max(rfq.quantity, 1)
        days = simulation.delivery_days(profile, quantity=quantity, seed=self.seed)
        conditions = [
            f"Simulated quote, seed={self.seed}, round={negotiation_round}",
            f"Volume pricing applies from {profile.min_quantity_for_discount} units",
        ]
        if negotiation_round > 1:
            conditions.append("Improved pricing valid for this aggregated order only")
        return SupplierOffer(
            offer_id=stable_id(
                "off", self.run_id, rfq.rfq_id, supplier.supplier_id, negotiation_round
            ),
            rfq_id=rfq.rfq_id,
            supplier_id=supplier.supplier_id,
            product_id=product_id,
            unit_price=unit_price,
            currency=rfq.requested_currency,
            max_quantity=quantity + profile.min_quantity_for_discount * 2,
            shipping_cost_total=simulation.shipping_total(profile, quantity),
            estimated_delivery_days=days,
            warranty_months=profile.warranty_months,
            returns_policy_summary=f"{14 if profile.warranty_months < 24 else 30}-day returns",
            expires_at=utcnow() + timedelta(days=14),
            conditions=conditions,
            negotiation_round=negotiation_round,
            data_origin=DataOrigin.SIMULATED,
            source_reference=f"simulated:{supplier.supplier_id}:seed={self.seed}",
            created_at=utcnow(),
        )

    # -- gateway API ------------------------------------------------------- #
    async def request_offer(self, rfq: RFQ, supplier: SupplierCandidate) -> SupplierOffer:
        profile = self.profile(supplier)
        product_id, reference = self._reference_price(rfq)
        price = simulation.offer_price(
            profile,
            reference_price=reference,
            quantity=max(rfq.quantity, 1),
            negotiation_round=1,
            seed=self.seed,
        )
        return self._build_offer(
            rfq=rfq,
            supplier=supplier,
            profile=profile,
            product_id=product_id,
            unit_price=price,
            negotiation_round=1,
        )

    async def submit_counter(
        self,
        rfq: RFQ,
        supplier: SupplierCandidate,
        offer: SupplierOffer,
        action: NegotiationAction,
    ) -> SupplierOffer:
        """The counter-message is *not* sent anywhere; the response is simulated."""
        profile = self.profile(supplier)
        product_id, reference = self._reference_price(rfq)
        requested = Decimal(action.proposed_unit_price or offer.unit_price)
        new_price, outcome = simulation.respond_to_counter(
            profile,
            reference_price=reference,
            current_price=Decimal(offer.unit_price),
            requested_price=requested,
            quantity=max(rfq.quantity, 1),
            negotiation_round=action.round,
            seed=self.seed,
        )
        new_offer = self._build_offer(
            rfq=rfq,
            supplier=supplier,
            profile=profile,
            product_id=product_id,
            unit_price=new_price,
            negotiation_round=action.round,
        )
        new_offer.conditions.append(f"Counter-offer outcome: {outcome}")
        return new_offer
