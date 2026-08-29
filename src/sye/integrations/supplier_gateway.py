"""Supplier gateway seam.

Demo mode always uses :class:`SimulatedSupplierGateway`. The real gateway is a
deliberate stub: it must never pretend that outreach happened.
"""

from __future__ import annotations

from typing import Protocol

from sye.domain.models import RFQ, NegotiationAction, SupplierCandidate, SupplierOffer


class SupplierGateway(Protocol):
    name: str

    async def request_offer(self, rfq: RFQ, supplier: SupplierCandidate) -> SupplierOffer:
        """Obtain an initial offer for an RFQ from one supplier."""
        ...

    async def submit_counter(
        self, rfq: RFQ, supplier: SupplierCandidate, offer: SupplierOffer, action: NegotiationAction
    ) -> SupplierOffer:
        """Send a counter-proposal and obtain the supplier's response."""
        ...


class SupplierOutreachNotImplemented(NotImplementedError):
    """Raised when live mode would need a real-world side effect."""


class HumanReviewedSupplierGateway:
    """Live-mode placeholder.

    Real supplier contact is a human-approved action. This gateway records a
    review task and refuses to fabricate an offer.
    """

    name = "human_review"

    def __init__(self) -> None:
        self.review_tasks: list[dict[str, str]] = []

    async def request_offer(self, rfq: RFQ, supplier: SupplierCandidate) -> SupplierOffer:
        self.review_tasks.append(
            {"type": "request_offer", "rfq_id": rfq.rfq_id, "supplier_id": supplier.supplier_id}
        )
        raise SupplierOutreachNotImplemented(
            f"Live supplier outreach to {supplier.name} requires human approval; "
            f"queued review task for RFQ {rfq.rfq_id}. No message was sent."
        )

    async def submit_counter(
        self, rfq: RFQ, supplier: SupplierCandidate, offer: SupplierOffer, action: NegotiationAction
    ) -> SupplierOffer:
        self.review_tasks.append(
            {"type": "counter", "rfq_id": rfq.rfq_id, "offer_id": offer.offer_id}
        )
        raise SupplierOutreachNotImplemented(
            "Live negotiation requires human approval; the drafted message was not sent."
        )
