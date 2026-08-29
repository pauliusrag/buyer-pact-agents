"""End-to-end tests for the market research & group bucketing agent.

The agent is exercised on its own — no suppliers, no negotiation, no campaign —
from raw JSON in the shape a caller would send, through bucketing, to researched
candidates and a best match per bucket.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from sye.agents import IntentAgent, MarketResearchAgent
from sye.agents.base import AgentContext, AgentError
from sye.config import DemoConfig
from sye.domain.enums import BucketStatus, DataOrigin, MatchClassification
from sye.domain.ids import new_run_id
from sye.integrations.linkup_client import FixtureResearchClient, build_research_client
from sye.observability.audit import AuditLogger
from sye.services.scenarios import normalize_users, parse_scenario
from tests.conftest import PROJECT_ROOT, linkup_available, make_product

# The input shape the caller asked for: a mapping of person -> natural language request.
NAMED_SCENARIO = {
    "scenario_name": "named requests",
    "market": "SE",
    "currency": "EUR",
    "users": {
        "john doe": (
            "I work from a laptop all day and want a 27 inch 1440p monitor that charges "
            "over USB-C. Under €320."
        ),
        "jane doe": (
            "Looking for at least 27 inches, QHD, for spreadsheets and documents. Around €280."
        ),
        "sam patel": "Need a QHD screen with USB-C charging, 27 inch or bigger. Max €300.",
        "otto berg": (
            "I want a 27 inch 1440p gaming monitor, 165Hz or better with FreeSync. Budget €440."
        ),
    },
}


def make_context(settings, config: DemoConfig, *, research=None, llm=None) -> AgentContext:
    run_id = new_run_id()
    return AgentContext(
        run_id=run_id,
        config=config,
        audit=AuditLogger(run_id),
        llm=llm,
        research=research or build_research_client(settings, offline=True, seed=config.seed),
    )


async def ingest_and_research(ctx: AgentContext, scenario: dict):
    _, requests, _ = parse_scenario(scenario, base_config=ctx.config, run_id=ctx.run_id)
    intents = (await IntentAgent(ctx).run(requests)).intents
    return requests, intents, await MarketResearchAgent(ctx).run(intents)


# --------------------------------------------------------------------------- #
# Input handling
# --------------------------------------------------------------------------- #
def test_accepts_the_named_mapping_shape():
    users = normalize_users(NAMED_SCENARIO["users"])
    assert [u["user_id"] for u in users] == ["john doe", "jane doe", "sam patel", "otto berg"]
    assert all(isinstance(u["prompt"], str) and u["prompt"] for u in users)


def test_accepts_bare_prompts_and_object_lists():
    assert normalize_users(["a monitor", "another monitor"])[0]["user_id"] == "user_001"
    assert normalize_users([{"user_id": "x", "prompt": "p"}])[0]["prompt"] == "p"
    assert normalize_users([{"john doe": "p"}])[0]["user_id"] == "john doe"


# --------------------------------------------------------------------------- #
# End to end: ingest -> bucket -> research -> best match
# --------------------------------------------------------------------------- #
async def test_agent_ingests_buckets_and_researches_end_to_end(settings, config):
    ctx = make_context(settings, config)
    requests, intents, result = await ingest_and_research(ctx, NAMED_SCENARIO)

    # 1. ingestion
    assert len(intents) == len(requests) == 4
    assert {i.user_id for i in intents} == {"john doe", "jane doe", "sam patel", "otto berg"}

    # 2. grouping — the office buyers share a bucket, the gamer does not
    assert len(result.buckets) == 2
    office = next(b for b in result.buckets if "john doe" in b.member_user_ids)
    gaming = next(b for b in result.buckets if "otto berg" in b.member_user_ids)
    assert set(office.member_user_ids) == {"john doe", "jane doe", "sam patel"}
    assert gaming.member_user_ids == ["otto berg"]

    # the group's binding requirements include one member's USB-C requirement
    binding = {c.key for c in office.shared_hard_constraints}
    assert "connectivity.usb_c_power_delivery" in binding
    # The ceiling is the strictest *stated maximum*: sam patel's €300. Jane's hedged
    # "around €280" is a target, not a limit, so it does not bind the group.
    assert office.price_ceiling == Decimal("300")
    jane = next(i for i in intents if i.user_id == "jane doe")
    assert jane.target_budget == Decimal("280") and jane.max_budget > jane.target_budget
    assert office.compatibility_explanation

    # 3. market research — a query per bucket, candidates with evidence
    assert result.queries[office.bucket_id]
    assert (
        "2560x1440" in result.queries[office.bucket_id][0]
        or "QHD" in result.queries[office.bucket_id][0]
    )
    assert result.products
    for product in result.products:
        assert product.sources, "every researched product must carry its evidence"
        assert product.bucket_id in {b.bucket_id for b in result.buckets}

    # 4. verdicts — every candidate judged against the binding requirements
    assert result.matches
    classifications = {m.classification for m in result.matches}
    assert MatchClassification.QUALIFIED in classifications
    assert MatchClassification.REJECTED in classifications

    # 5. best item match per bucket, ready for a campaign
    best_office = result.best_match(office.bucket_id)
    assert best_office is not None
    winner = next(p for p in result.products if p.product_id == best_office.product_id)
    assert winner.attributes["connectivity.usb_c_power_delivery"] is True
    assert winner.normal_market_price <= office.price_ceiling
    assert len(result.campaign_ready_buckets()) == 2

    # the agent stayed in its lane
    assert result.agent == "market_research_agent"
    assert result.metrics["research_provider"] == "fixtures"


async def test_rejections_are_explained_and_unknown_specs_never_pass(settings, config):
    ctx = make_context(settings, config)
    _, _, result = await ingest_and_research(ctx, NAMED_SCENARIO)

    rejected = [m for m in result.matches if m.classification == MatchClassification.REJECTED]
    assert rejected
    assert all(m.rejection_reasons for m in rejected)

    unknown = [m for m in rejected if m.unknown_specs]
    assert unknown, "the fixture catalogue contains a listing with an unverifiable spec"
    assert "could not be verified" in unknown[0].rejection_reasons[0]


async def test_agent_records_its_reasoning_in_the_audit_trail(settings, config):
    ctx = make_context(settings, config)
    await ingest_and_research(ctx, NAMED_SCENARIO)

    nodes = {e.node for e in ctx.audit.events}
    assert {"build_demand_buckets", "research_products", "evaluate_matches"} <= nodes

    bucket_events = [e for e in ctx.audit.events if e.event_type == "bucket_created"]
    assert bucket_events and all(e.decision for e in bucket_events)

    match_events = [e for e in ctx.audit.events if e.event_type == "match_evaluated"]
    assert match_events and all(e.confidence is not None for e in match_events)

    sequences = [e.sequence for e in ctx.audit.ordered()]
    assert sequences == sorted(sequences)


async def test_agent_needs_no_supplier_gateway(settings, config):
    """It is genuinely independent of the negotiation half of the pipeline."""
    ctx = make_context(settings, config)
    assert ctx.gateway_factory is None
    _, _, result = await ingest_and_research(ctx, NAMED_SCENARIO)

    assert result.buckets and result.products and result.matches
    assert not hasattr(result, "offers")
    assert not hasattr(result, "campaigns")

    with pytest.raises(AgentError):
        ctx.require_gateway({})


async def test_result_is_deterministic_for_the_same_input(settings, config):
    async def once():
        ctx = make_context(settings, config)
        _, _, result = await ingest_and_research(ctx, NAMED_SCENARIO)
        return json.dumps(
            {
                "buckets": sorted(
                    (b.label, tuple(sorted(b.member_user_ids))) for b in result.buckets
                ),
                "matches": sorted(
                    (m.product_name, m.classification.value, m.overall_score)
                    for m in result.matches
                ),
            }
        )

    assert await once() == await once()


# --------------------------------------------------------------------------- #
# Autonomy: the agent decides to search again
# --------------------------------------------------------------------------- #
class NarrowThenBroadResearchClient:
    """First search returns only over-priced candidates; the broadened one fits."""

    name = "scripted-research"

    def __init__(self):
        self.calls = 0
        self.queries: list[str] = []

    async def search_products(
        self, *, query, category, market, max_results, run_id, bucket_id, constraints=None
    ):
        self.calls += 1
        self.queries.append(query)
        broadened = "up to" in query  # the agent's relaxed price line
        product = make_product(
            "Broad Match Monitor" if broadened else "Too Expensive Monitor",
            price=305.0 if broadened else 900.0,
            attributes={
                "display.size_in": 27,
                "display.resolution": "2560x1440",
                "connectivity.usb_c_power_delivery": True,
            },
            bucket_id=bucket_id,
        )
        return [product.model_copy(update={"bucket_id": bucket_id})]

    async def verify_product(self, product, *, run_id):
        self.calls += 1
        return product

    async def search_suppliers(self, **kwargs):  # pragma: no cover - not used here
        return []


async def test_agent_searches_again_with_a_broadened_query_when_nothing_fits(settings):
    config = DemoConfig(offline=True, seed=42, write_snapshots=False, max_research_attempts=2)
    client = NarrowThenBroadResearchClient()
    ctx = make_context(settings, config, research=client)
    _, _, result = await ingest_and_research(ctx, NAMED_SCENARIO)

    # Two attempts per bucket: the first found nothing that fits.
    assert client.calls >= 4
    for queries in result.queries.values():
        assert len(queries) == 2
        assert "under" in queries[0]
        assert "up to" in queries[1]

    retries = [e for e in ctx.audit.events if e.event_type == "research_retry"]
    assert retries and "broadened" in retries[0].message

    # And the broadened pass produced a usable match.
    assert any(m.product_name == "Broad Match Monitor" for m in result.matches)
    assert result.campaign_ready_buckets()


async def test_agent_reports_honestly_when_nothing_fits(settings):
    config = DemoConfig(offline=True, seed=42, write_snapshots=False, max_research_attempts=1)
    client = NarrowThenBroadResearchClient()
    ctx = make_context(settings, config, research=client)
    _, _, result = await ingest_and_research(ctx, NAMED_SCENARIO)

    assert not result.campaign_ready_buckets()
    assert all(m.classification == MatchClassification.REJECTED for m in result.matches)
    assert any(o.status == BucketStatus.NO_VIABLE_PRODUCT for o in result.outcomes)


async def test_offline_products_are_marked_as_fixtures_not_web_research(settings, config):
    ctx = make_context(settings, config)
    _, _, result = await ingest_and_research(ctx, NAMED_SCENARIO)

    assert isinstance(ctx.research, FixtureResearchClient)
    assert all(p.data_origin == DataOrigin.SYSTEM for p in result.products)
    assert all(s.url.startswith("fixture://") for p in result.products for s in p.sources)
    assert (PROJECT_ROOT / "data" / "fixtures" / "monitors.json").exists()


# --------------------------------------------------------------------------- #
# Live web research (opt-in)
# --------------------------------------------------------------------------- #
@pytest.mark.live
@pytest.mark.skipif(not linkup_available(), reason="LINKUP_API_KEY not configured")
async def test_agent_researches_the_real_web_with_linkup():
    """The full ask: JSON in, buckets out, real web research, best matches out."""
    from sye.config import get_settings

    live_settings = get_settings()  # the real .env, not the hermetic test settings
    config = DemoConfig(offline=False, seed=42, write_snapshots=False, max_linkup_calls=12)
    client = build_research_client(live_settings, offline=False, max_calls=config.max_linkup_calls)
    ctx = make_context(live_settings, config, research=client)

    _, intents, result = await ingest_and_research(ctx, NAMED_SCENARIO)

    assert len(intents) == 4
    assert result.buckets, "live research must still produce demand buckets"
    assert result.products, "Linkup returned no candidates"

    for product in result.products:
        assert product.data_origin == DataOrigin.WEB_RESEARCH
        assert product.sources, "web-derived products must keep their sources"
        assert any(s.url.startswith("http") for s in product.sources)
        assert product.canonical_name

    assert result.matches, "every candidate must be judged"
    for match in result.matches:
        assert match.classification in set(MatchClassification)
        assert match.hard_constraint_results

    # Web content varies, so assert the shape of the outcome rather than a product.
    for bucket in result.buckets:
        best = result.best_match(bucket.bucket_id)
        if best is None:
            assert any(
                o.bucket_id == bucket.bucket_id and o.status == BucketStatus.NO_VIABLE_PRODUCT
                for o in result.outcomes
            )
        else:
            assert best.classification != MatchClassification.REJECTED
            assert best.explanation

    assert result.metrics["research_provider"] == "linkup"
    assert result.metrics["research_calls"] > 0


# --------------------------------------------------------------------------- #
# A second category end to end (the demand front door runs on wearables)
# --------------------------------------------------------------------------- #
RING_SCENARIO = {
    "scenario_name": "smart rings",
    "market": "SE",
    "currency": "EUR",
    "users": {
        "anna": "I want a smart ring that tracks my sleep and HRV. No monthly subscription. Under €300.",
        "ben": "Looking for a sleep tracking ring, works with my iPhone, at least a week of battery. Max €320.",
        "cara": "A ring for sleep and recovery, I refuse to pay a subscription. Around €280.",
        "eva": "Fitness band with GPS and heart rate for running, waterproof, around €200.",
    },
}


async def test_agent_handles_a_second_category_end_to_end(settings, config):
    ctx = make_context(settings, config)
    _, intents, result = await ingest_and_research(ctx, RING_SCENARIO)

    assert {i.category for i in intents} == {"wearable"}

    rings = next(b for b in result.buckets if "anna" in b.member_user_ids)
    assert set(rings.member_user_ids) == {"anna", "ben", "cara"}
    # The label is user-facing on a demand front door.
    assert "subscription-free" in rings.label and "ring" in rings.label

    binding = {c.key for c in rings.shared_hard_constraints}
    assert {"sensors.sleep_tracking", "wearable.subscription_required"} <= binding

    best = result.best_match(rings.bucket_id)
    assert best is not None
    winner = next(p for p in result.products if p.product_id == best.product_id)
    assert winner.attributes["wearable.subscription_required"] is False

    # A subscription-required product is rejected for exactly that reason.
    rejected = {
        m.product_name: m.rejection_reasons
        for m in result.matches
        if m.bucket_id == rings.bucket_id and m.classification == MatchClassification.REJECTED
    }
    assert any("subscription" in " ".join(reasons) for reasons in rejected.values())
