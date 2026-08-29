"""Opt-in smoke tests that hit real external APIs.

Deselected by default. Run explicitly:

    uv run pytest -m live
"""

from __future__ import annotations

import pytest

from sye.config import DemoConfig, get_settings
from sye.integrations.linkup_client import build_research_client
from tests.conftest import linkup_available, llm_available

pytestmark = pytest.mark.live


@pytest.mark.skipif(not linkup_available(), reason="LINKUP_API_KEY not configured")
async def test_one_real_linkup_product_search():
    settings = get_settings()
    config = DemoConfig(offline=False, max_linkup_calls=3)
    client = build_research_client(settings, offline=False, max_calls=config.max_linkup_calls)

    products = await client.search_products(
        query=(
            "Find 3 computer monitors sold in Sweden with a 27 inch screen, 2560x1440 "
            "resolution and USB-C power delivery, under 350 EUR. Return brand, model, "
            "canonical_name, normal_market_price, currency, listing_url and attributes."
        ),
        category="monitor",
        market="SE",
        max_results=3,
        run_id="run_live_smoke",
        bucket_id="bkt_live_smoke",
    )

    assert products, "live Linkup search returned no products"
    for product in products:
        assert product.canonical_name
        assert product.data_origin.value == "web_research"
        assert product.sources, "web-derived products must keep their sources"


@pytest.mark.skipif(not llm_available(), reason="no LLM API key configured")
async def test_one_real_llm_structured_extraction():
    from sye.domain.models import IntentExtraction
    from sye.integrations.llm import build_llm_provider

    provider = build_llm_provider(get_settings(), offline=False)
    extraction = await provider.structured(
        schema=IntentExtraction,
        system="Extract structured monitor requirements. Use canonical constraint keys.",
        user="I need a 27 inch 1440p monitor with USB-C charging, under 300 euro.",
        task="parse_intent",
    )
    assert extraction.category
    assert extraction.constraints
