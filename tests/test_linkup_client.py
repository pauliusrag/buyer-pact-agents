"""The live Linkup code path, exercised without the network.

The SDK's own response models are used, and only the HTTP call is replaced. That
covers everything the live path does besides the request itself: which arguments
reach the SDK, how the structured payload is unpacked, and that sources survive
into the domain objects.

The genuinely live check lives in ``test_market_research_agent.py`` behind the
``live`` marker.
"""

from __future__ import annotations

import json

import pytest
from linkup import LinkupSearchStructuredResponse, LinkupSearchTextResult

from sye.agents import IntentAgent, MarketResearchAgent
from sye.agents.base import AgentContext
from sye.config import DemoConfig
from sye.domain.enums import DataOrigin, MatchClassification
from sye.domain.ids import new_run_id
from sye.integrations.linkup_client import (
    LinkupResearchClient,
    ResearchError,
    build_research_client,
)
from sye.observability.audit import AuditLogger
from sye.services.scenarios import parse_scenario

SCENARIO = {
    "scenario_name": "linkup path",
    "market": "SE",
    "currency": "EUR",
    "users": {
        "john doe": "27 inch 1440p monitor with USB-C charging, under €320.",
        "jane doe": "At least 27 inch QHD for work, max €300.",
    },
}

PRODUCTS_PAYLOAD = {
    "products": [
        {
            "brand": "Dell",
            "model": "P2723DE",
            "canonical_name": "Dell P2723DE",
            "normal_market_price": 289.0,
            "currency": "EUR",
            "merchant_or_listing_name": "Example Retailer",
            "listing_url": "https://retailer.invalid/dell-p2723de",
            "availability": "in_stock",
            "attributes": {
                "display.size_in": 27,
                "display.resolution": "2560x1440",
                "connectivity.usb_c_power_delivery": True,
            },
        },
        {
            "brand": "Acme",
            "model": "NoUSB27",
            "canonical_name": "Acme NoUSB27",
            "normal_market_price": 199.0,
            "currency": "EUR",
            "attributes": {
                "display.size_in": 27,
                "display.resolution": "2560x1440",
                "connectivity.usb_c_power_delivery": False,
            },
        },
    ]
}

SUPPLIERS_PAYLOAD = {
    "suppliers": [
        {
            "name": "Example Distribution AB",
            "supplier_type": "distributor",
            "website": "https://distributor.invalid",
            "market": "SE",
        }
    ]
}


def source(url: str) -> LinkupSearchTextResult:
    """A source exactly as the SDK returns it (``content``, not ``snippet``)."""
    return LinkupSearchTextResult(
        type="text",
        name="Example specification page",
        url=url,
        content="27 inch QHD monitor with USB-C power delivery",
        favicon="",
    )


class FakeSDKClient:
    """Stands in for ``linkup.LinkupClient``: records kwargs, returns SDK models."""

    def __init__(self, *, api_key: str, **_: object) -> None:
        self.api_key = api_key
        self.calls: list[dict] = []
        self.raise_with: Exception | None = None

    async def async_search(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_with is not None:
            raise self.raise_with
        payload = SUPPLIERS_PAYLOAD if "supply" in kwargs["query"].lower() else PRODUCTS_PAYLOAD
        return LinkupSearchStructuredResponse(
            data=payload,
            sources=[source("https://retailer.invalid/dell-p2723de")],
        )

    def search(self, **kwargs):  # pragma: no cover - the async path is used
        raise AssertionError("the async entry point should be preferred")


@pytest.fixture
def sdk(monkeypatch) -> FakeSDKClient:
    created: dict[str, FakeSDKClient] = {}

    def factory(*args, **kwargs):
        client = FakeSDKClient(api_key=kwargs.get("api_key", "fake"))
        created["client"] = client
        return client

    monkeypatch.setattr("linkup.LinkupClient", factory)
    yield created
    created.clear()


def make_client(**overrides) -> LinkupResearchClient:
    params = {"api_key": "fake-key", "depth": "standard", "timeout": 5.0, "max_calls": 20}
    params.update(overrides)
    return LinkupResearchClient(**params)


# --------------------------------------------------------------------------- #
# Request shaping
# --------------------------------------------------------------------------- #
async def test_search_passes_the_expected_arguments_to_the_sdk(sdk):
    client = make_client()
    products = await client.search_products(
        query="Find 6 computer monitors with 2560x1440 under 300 EUR",
        category="monitor",
        market="SE",
        max_results=6,
        run_id="run_test",
        bucket_id="bkt_test",
    )

    call = sdk["client"].calls[0]
    assert call["depth"] == "standard"
    assert call["output_type"] == "structured"
    assert call["include_sources"] is True
    assert call["max_results"] == 12
    assert call["timeout"] == 5.0
    schema = json.loads(call["structured_output_schema"])
    assert "products" in schema["properties"]
    assert len(products) == 2
    assert client.calls == 1


async def test_products_are_marked_web_research_and_keep_sources(sdk):
    client = make_client()
    products = await client.search_products(
        query="monitors",
        category="monitor",
        market="SE",
        max_results=6,
        run_id="run_test",
        bucket_id="bkt_test",
    )

    dell = products[0]
    assert dell.canonical_name == "Dell P2723DE"
    assert dell.data_origin == DataOrigin.WEB_RESEARCH
    assert dell.attributes["connectivity.usb_c_power_delivery"] is True
    assert dell.normal_market_price == 289.0
    assert dell.sources[0].url.startswith("https://")
    assert dell.sources[0].provider == "linkup"
    assert dell.sources[0].title == "Example specification page"
    assert "USB-C" in (dell.sources[0].snippet or "")
    assert dell.bucket_id == "bkt_test"


async def test_supplier_search_maps_the_structured_payload(sdk):
    client = make_client()
    products = await client.search_products(
        query="monitors",
        category="monitor",
        market="SE",
        max_results=1,
        run_id="run_test",
        bucket_id="bkt_test",
    )
    suppliers = await client.search_suppliers(
        product=products[0], market="SE", max_results=3, run_id="run_test", bucket_id="bkt_test"
    )

    assert len(suppliers) == 1
    assert suppliers[0].name == "Example Distribution AB"
    assert suppliers[0].supplier_type == "distributor"
    assert suppliers[0].data_origin == DataOrigin.WEB_RESEARCH
    assert suppliers[0].authorization_claimed is False
    assert suppliers[0].evidence


async def test_verification_uses_deep_search_and_merges_specs(sdk):
    client = make_client()
    products = await client.search_products(
        query="monitors",
        category="monitor",
        market="SE",
        max_results=1,
        run_id="run_test",
        bucket_id="bkt_test",
    )
    thin = products[0].model_copy(update={"attributes": {"display.size_in": 27}})
    verified = await client.verify_product(thin, run_id="run_test")

    assert sdk["client"].calls[-1]["depth"] == "deep"
    assert verified.verified is True
    assert verified.attributes["display.resolution"] == "2560x1440"
    assert len(verified.sources) >= 1


# --------------------------------------------------------------------------- #
# Failure behaviour
# --------------------------------------------------------------------------- #
async def test_call_budget_is_enforced(sdk):
    client = make_client(max_calls=1)
    await client.search_products(
        query="monitors",
        category="monitor",
        market="SE",
        max_results=1,
        run_id="r",
        bucket_id="b",
    )
    with pytest.raises(ResearchError, match="budget exhausted"):
        await client.search_products(
            query="monitors",
            category="monitor",
            market="SE",
            max_results=1,
            run_id="r",
            bucket_id="b",
        )


async def test_authentication_errors_are_not_retried(sdk):
    class LinkupAuthenticationError(Exception):
        pass

    client = make_client(max_retries=3)
    await client.search_products(
        query="monitors",
        category="monitor",
        market="SE",
        max_results=1,
        run_id="r",
        bucket_id="b",
    )
    sdk["client"].raise_with = LinkupAuthenticationError("bad key")

    with pytest.raises(ResearchError, match="rejected the request"):
        await client.search_products(
            query="monitors",
            category="monitor",
            market="SE",
            max_results=1,
            run_id="r",
            bucket_id="b",
        )
    assert len(sdk["client"].calls) == 2  # one success, one failed attempt, no retries


async def test_transient_errors_are_retried_then_reported(sdk):
    client = make_client(max_retries=1)
    await client.search_products(
        query="monitors",
        category="monitor",
        market="SE",
        max_results=1,
        run_id="r",
        bucket_id="b",
    )
    sdk["client"].raise_with = TimeoutError("upstream timeout")

    with pytest.raises(ResearchError, match="failed after 2 attempts"):
        await client.search_products(
            query="monitors",
            category="monitor",
            market="SE",
            max_results=1,
            run_id="r",
            bucket_id="b",
        )
    assert len(sdk["client"].calls) == 3  # 1 success + 2 attempts


def test_live_mode_without_a_key_is_an_error_not_a_fixture_fallback(settings):
    with pytest.raises(ResearchError, match="LINKUP_API_KEY"):
        build_research_client(settings, offline=False)


# --------------------------------------------------------------------------- #
# The agent over the live client
# --------------------------------------------------------------------------- #
async def test_market_research_agent_over_the_linkup_client(sdk):
    config = DemoConfig(offline=False, seed=42, write_snapshots=False)
    run_id = new_run_id()
    ctx = AgentContext(
        run_id=run_id,
        config=config,
        audit=AuditLogger(run_id),
        llm=None,
        research=make_client(),
    )
    _, requests, _ = parse_scenario(SCENARIO, base_config=config, run_id=run_id)
    intents = (await IntentAgent(ctx).run(requests)).intents
    result = await MarketResearchAgent(ctx).run(intents)

    assert result.metrics["research_provider"] == "linkup"
    assert result.buckets
    assert all(p.data_origin == DataOrigin.WEB_RESEARCH for p in result.products)
    assert all(p.sources for p in result.products)

    best = result.best_match(result.buckets[0].bucket_id)
    assert best is not None and best.product_name == "Dell P2723DE"
    rejected = [m for m in result.matches if m.classification == MatchClassification.REJECTED]
    assert any("USB-C" in r.rejection_reasons[0] for r in rejected)

    # The query actually sent to the web carries the group's binding requirements.
    query = sdk["client"].calls[0]["query"]
    assert "2560x1440" in query and "27" in query and "SE" in query
