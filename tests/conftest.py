"""Shared test fixtures.

Tests never touch the network: research is mocked or served from fixtures, and the
LLM is either absent (deterministic fallback) or a scripted double.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sye.config import DemoConfig, Settings
from sye.domain.enums import ConstraintOperator, DataOrigin, Importance
from sye.domain.ids import stable_id
from sye.domain.models import (
    EvidenceSource,
    ProductCandidate,
    RequirementConstraint,
    SupplierCandidate,
    UserIntent,
    UserRequest,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def linkup_available() -> bool:
    """Whether live web research is configured.

    Checks the application's own settings, not ``os.environ``: keys normally live in
    ``.env``, which pydantic-settings reads without exporting to the process.
    """
    from sye.config import get_settings

    return get_settings().has_linkup_key


def llm_available() -> bool:
    from sye.config import get_settings

    return get_settings().has_llm_key


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        SYE_DB_URL=f"sqlite:///{tmp_path / 'test.db'}",
        SYE_DATA_DIR=str(tmp_path / "data"),
        SYE_OFFLINE="true",
        SYE_SEED="42",
        LLM_PROVIDER="none",
        ANTHROPIC_API_KEY="",
        LINKUP_API_KEY="",
    )


@pytest.fixture
def config() -> DemoConfig:
    return DemoConfig(offline=True, seed=42, write_snapshots=False)


def constraint(
    key: str,
    operator: ConstraintOperator,
    value,
    *,
    importance: Importance = Importance.HARD,
    user: str = "user_x",
    weight: float = 1.0,
) -> RequirementConstraint:
    return RequirementConstraint(
        key=key,
        operator=operator,
        value=value,
        importance=importance,
        weight=weight,
        confidence=0.9,
        source_text=f"{key} {operator.value} {value}",
        required_by_user_ids=[user],
    )


def make_intent(
    user_id: str,
    constraints: list[RequirementConstraint],
    *,
    max_budget=None,
    target_budget=None,
    category: str = "monitor",
) -> UserIntent:
    return UserIntent(
        intent_id=stable_id("int", user_id),
        user_id=user_id,
        request_id=stable_id("req", user_id),
        category=category,
        category_confidence=0.95,
        constraints=[c.model_copy(update={"required_by_user_ids": [user_id]}) for c in constraints],
        max_budget=max_budget,
        target_budget=target_budget,
        currency="EUR",
        extraction_summary=f"{user_id} wants a {category}",
        extraction_confidence=0.9,
    )


def make_request(user_id: str, prompt: str) -> UserRequest:
    return UserRequest(
        user_id=user_id,
        request_id=stable_id("req", user_id),
        prompt=prompt,
        market="SE",
        currency="EUR",
        created_at=NOW,
    )


def make_product(
    name: str,
    *,
    price: float,
    attributes: dict,
    bucket_id: str = "bkt_test",
    origin: DataOrigin = DataOrigin.WEB_RESEARCH,
) -> ProductCandidate:
    return ProductCandidate(
        product_id=stable_id("prd", name),
        category="monitor",
        brand=name.split()[0],
        model=name,
        canonical_name=name,
        attributes=attributes,
        normal_market_price=price,
        currency="EUR",
        merchant_or_listing_name="Test merchant",
        listing_url=f"https://example.invalid/{name.replace(' ', '-').lower()}",
        availability="in_stock",
        sources=[
            EvidenceSource(
                title=f"{name} specification",
                url=f"https://example.invalid/source/{name.replace(' ', '-').lower()}",
                snippet="test evidence",
                retrieved_at=NOW,
                provider="test",
            )
        ],
        data_origin=origin,
        researched_at=NOW,
        bucket_id=bucket_id,
    )


def make_supplier(name: str, supplier_type: str = "distributor") -> SupplierCandidate:
    return SupplierCandidate(
        supplier_id=stable_id("sup", name),
        name=name,
        supplier_type=supplier_type,
        website=f"https://example.invalid/{name.replace(' ', '-').lower()}",
        market="SE",
        evidence=[
            EvidenceSource(
                title=name,
                url=f"https://example.invalid/supplier/{name.replace(' ', '-').lower()}",
                retrieved_at=NOW,
                provider="test",
            )
        ],
        data_origin=DataOrigin.WEB_RESEARCH,
    )


class MockResearchClient:
    """Stands in for Linkup: returns fixed candidates with real evidence objects."""

    name = "mock"

    def __init__(self, products: list[ProductCandidate], suppliers: list[SupplierCandidate]):
        self._products = products
        self._suppliers = suppliers
        self.calls = 0
        self.queries: list[str] = []

    async def search_products(
        self, *, query, category, market, max_results, run_id, bucket_id, constraints=None
    ):
        self.calls += 1
        self.queries.append(query)
        return [
            p.model_copy(update={"bucket_id": bucket_id, "product_id": f"{p.product_id}"})
            for p in self._products[:max_results]
        ]

    async def verify_product(self, product, *, run_id):
        self.calls += 1
        return product.model_copy(update={"verified": True})

    async def search_suppliers(
        self, *, product, market, max_results, run_id, bucket_id, constraints=None
    ):
        self.calls += 1
        return [
            s.model_copy(update={"bucket_id": bucket_id, "product_ids": [product.product_id]})
            for s in self._suppliers[:max_results]
        ]


@pytest.fixture
def mock_research() -> MockResearchClient:
    products = [
        make_product(
            "Testtron QHD27C",
            price=289.0,
            attributes={
                "display.size_in": 27,
                "display.resolution": "2560x1440",
                "display.refresh_rate_hz": 75,
                "connectivity.usb_c": True,
                "connectivity.usb_c_power_delivery": True,
                "adaptive_sync.freesync": True,
                "ergonomics.height_adjustable": True,
            },
        ),
        make_product(
            "Testtron QHD27 Basic",
            price=199.0,
            attributes={
                "display.size_in": 27,
                "display.resolution": "2560x1440",
                "display.refresh_rate_hz": 75,
                "connectivity.usb_c": False,
                "connectivity.usb_c_power_delivery": False,
            },
        ),
        make_product(
            "Testtron FHD24",
            price=129.0,
            attributes={
                "display.size_in": 24,
                "display.resolution": "1920x1080",
                "connectivity.usb_c_power_delivery": False,
            },
        ),
    ]
    suppliers = [
        make_supplier("Test Distribution AB", "distributor"),
        make_supplier("Test Retail Nordic", "retailer"),
        make_supplier("Test Manufacturing", "manufacturer"),
    ]
    return MockResearchClient(products, suppliers)
