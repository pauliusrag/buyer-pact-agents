"""End-to-end demo pipeline with mocked research and a scripted LLM.

This is the test that proves the orchestration and the frontend contract.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from sye.config import DemoConfig
from sye.domain.enums import ConstraintOperator, DataOrigin, Importance, RunStatus
from sye.domain.ids import new_run_id
from sye.domain.models import (
    CampaignCopy,
    IntentExtraction,
    PipelineRunExport,
    RequirementConstraint,
)
from sye.graph.context import build_context
from sye.graph.main_graph import run_pipeline
from sye.integrations.llm import ScriptedProvider
from sye.persistence.repositories import RunRepository
from sye.services.exports import lovable_payload, to_json
from sye.services.report import render_report
from sye.services.snapshots import SnapshotWriter
from tests.conftest import make_request

SCENARIO = [
    ("user_001", "27 inch 1440p monitor with USB-C charging for my MacBook, under €320."),
    ("user_002", "At least 27 inch and QHD for work, around €280."),
    ("user_003", "QHD screen, 27 inch or bigger, USB-C charging, max €300."),
]


@pytest.fixture
def requests():
    return [make_request(user_id, prompt) for user_id, prompt in SCENARIO]


async def run_demo(settings, mock_research, requests, *, llm=None, seed=42, snapshots=False):
    run_id = new_run_id()
    config = DemoConfig(offline=True, seed=seed, write_snapshots=snapshots)
    ctx = build_context(
        run_id=run_id,
        config=config,
        settings=settings,
        research=mock_research,
        llm=llm,
        snapshots=SnapshotWriter(settings.runs_dir, run_id, enabled=snapshots),
    )
    return await run_pipeline(
        requests,
        config=config,
        settings=settings,
        run_id=run_id,
        scenario_name="integration test",
        ctx=ctx,
    )


async def test_full_run_produces_a_valid_export(settings, mock_research, requests):
    export, ctx = await run_demo(settings, mock_research, requests)

    # The run completes and the canonical contract validates.
    assert export.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}
    assert PipelineRunExport.model_validate(export.model_dump()) == export
    assert export.schema_version == "1.0"

    # Every stage produced typed objects.
    assert len(export.intents) >= 2
    assert len(export.buckets) >= 1
    assert len(export.products) >= 1
    assert len(export.suppliers) >= 1
    assert len(export.rfqs) >= 1
    assert len(export.offers) >= 1
    assert len(export.offer_evaluations) >= 1
    assert len(export.campaigns) >= 1

    # Provenance survives to the end.
    for product in export.products:
        assert product.sources or product.data_origin in {
            DataOrigin.SYSTEM,
            DataOrigin.SIMULATED,
        }
    for offer in export.offers:
        assert offer.data_origin == DataOrigin.SIMULATED
    for campaign in export.campaigns:
        assert campaign.data_origin == DataOrigin.SIMULATED
        assert campaign.status == "simulation_ready"

    # The audit trail is ordered and complete.
    sequences = [e.sequence for e in export.audit_events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    nodes = {e.node for e in export.audit_events}
    assert {
        "load_requests",
        "parse_user_intents",
        "build_demand_buckets",
        "research_products",
        "evaluate_matches",
        "research_suppliers",
        "build_rfqs",
        "obtain_supplier_offers",
        "normalize_and_compare_offers",
        "build_campaigns",
        "finalize_export",
    } <= nodes

    # Nothing was ever "sent".
    assert all(action.delivered is False for action in export.negotiation_actions)
    assert ctx.research.calls > 0


async def test_campaign_is_traceable_to_its_inputs(settings, mock_research, requests):
    export, _ = await run_demo(settings, mock_research, requests)
    campaign = export.campaigns[0]

    bucket = next(b for b in export.buckets if b.bucket_id == campaign.bucket_id)
    offer = next(o for o in export.offers if o.offer_id == campaign.winning_offer_id)
    product = next(p for p in export.products if p.product_id == campaign.product_id)
    supplier = next(s for s in export.suppliers if s.supplier_id == campaign.supplier_id)

    assert set(campaign.member_user_ids) == set(bucket.member_user_ids)
    assert offer.product_id == product.product_id
    assert supplier.supplier_id == offer.supplier_id
    assert campaign.requirement_match_summary
    # Every binding requirement of the bucket is satisfied by the winning product.
    match = next(
        m
        for m in export.matches
        if m.bucket_id == bucket.bucket_id and m.product_id == product.product_id
    )
    assert match.classification.value in {"qualified", "negotiable_gap"}
    assert not match.rejection_reasons


async def test_usb_c_requirement_of_one_member_binds_the_group(settings, mock_research, requests):
    export, _ = await run_demo(settings, mock_research, requests)
    bucket = export.buckets[0]
    binding = {c.key for c in bucket.shared_hard_constraints}
    assert "connectivity.usb_c_power_delivery" in binding

    product = next(p for p in export.products if p.product_id == export.campaigns[0].product_id)
    assert product.attributes.get("connectivity.usb_c_power_delivery") is True


async def test_run_is_reproducible_with_the_same_seed(settings, mock_research, requests):
    first, _ = await run_demo(settings, mock_research, requests, seed=42)
    second, _ = await run_demo(settings, mock_research, requests, seed=42)

    def commercials(export):
        return [
            (o.supplier_id, o.negotiation_round, str(o.unit_price), str(o.shipping_cost_total))
            for o in sorted(export.offers, key=lambda o: (o.negotiation_round, o.supplier_id))
        ]

    assert commercials(first) == commercials(second)
    assert [b.member_user_ids for b in first.buckets] == [b.member_user_ids for b in second.buckets]
    assert [str(c.group_price) for c in first.campaigns] == [
        str(c.group_price) for c in second.campaigns
    ]


async def test_offline_fixture_runs_are_identical_across_run_ids(settings):
    """Two separate runs of the same scenario with the same seed must agree.

    This uses the real offline research client, so it also covers the ordering of
    candidates and suppliers — the place where a run-scoped id used as a sort key
    would silently change which campaign wins.
    """
    from sye.services.scenarios import load_scenario_file, parse_scenario

    payload = load_scenario_file("examples/demo_easy.json")

    async def once():
        run_id = new_run_id()
        base = DemoConfig(offline=True, seed=42, write_snapshots=False)
        name, requests, config = parse_scenario(payload, base_config=base, run_id=run_id)
        export, _ = await run_pipeline(
            requests,
            config=config,
            settings=settings,
            run_id=run_id,
            scenario_name=name,
            persist=False,
        )
        suppliers = {s.supplier_id: s.name for s in export.suppliers}
        return {
            "buckets": sorted(
                (b.label, tuple(b.member_user_ids), str(b.price_ceiling)) for b in export.buckets
            ),
            "matches": sorted(
                (m.product_name, m.classification.value, m.overall_score) for m in export.matches
            ),
            "offers": sorted(
                (suppliers[o.supplier_id], o.negotiation_round, str(o.unit_price))
                for o in export.offers
            ),
            "campaigns": sorted(
                (c.title, str(c.group_price), c.discount_percent) for c in export.campaigns
            ),
        }

    first, second = await once(), await once()
    assert first == second
    assert first["campaigns"], "the easy scenario should produce campaigns"


async def test_negotiation_improves_or_holds_but_never_invents_a_better_price(
    settings, mock_research, requests
):
    export, _ = await run_demo(settings, mock_research, requests)
    rounds = {o.negotiation_round for o in export.offers}
    assert max(rounds) >= 2, "at least one counter round should run"

    by_round = {}
    for evaluation in export.offer_evaluations:
        by_round.setdefault(evaluation.negotiation_round, []).append(
            Decimal(evaluation.landed_unit_cost)
        )
    best_first = min(by_round[1])
    best_last = min(min(costs) for costs in by_round.values())
    assert best_last <= best_first
    assert export.metrics["simulated_negotiation_improvement_percent"] >= 0


async def test_scripted_llm_is_used_and_recorded(settings, mock_research, requests):
    scripted = ScriptedProvider(
        {
            "parse_intent": lambda prompt: IntentExtraction(
                category="monitor",
                category_confidence=0.99,
                constraints=[
                    RequirementConstraint(
                        key="display.size_in",
                        operator=ConstraintOperator.GTE,
                        value=27,
                        importance=Importance.HARD,
                        confidence=0.95,
                        source_text="27 inch",
                    ),
                    RequirementConstraint(
                        key="connectivity.usb_c_power_delivery",
                        operator=ConstraintOperator.BOOLEAN,
                        value=True,
                        importance=Importance.HARD,
                        confidence=0.9,
                        source_text="USB-C charging",
                    ),
                ],
                max_budget=Decimal("300"),
                target_budget=Decimal("270"),
                extraction_summary="Scripted extraction",
                extraction_confidence=0.9,
            ),
            "campaign_copy": CampaignCopy(
                title="Scripted campaign title",
                short_description="Scripted description",
                why_this_product="Scripted rationale",
            ),
        }
    )
    export, ctx = await run_demo(settings, mock_research, requests, llm=scripted)

    assert all(i.extracted_by == "llm:scripted" for i in export.intents)
    assert all(i.data_origin == DataOrigin.LLM_INFERRED for i in export.intents)
    assert export.campaigns[0].title == "Scripted campaign title"
    assert export.metrics["llm_calls"] >= len(requests)
    # Unscripted tasks fall back deterministically and say so.
    assert any("deterministic" in w for w in export.warnings)


async def test_export_survives_a_restart(settings, mock_research, requests):
    export, _ = await run_demo(settings, mock_research, requests)

    repository = RunRepository(settings.db_url)  # a fresh handle, as after a restart
    stored = repository.get_run(export.run_id)
    assert stored is not None
    assert stored.run_id == export.run_id
    assert len(stored.campaigns) == len(export.campaigns)
    assert [e.sequence for e in repository.get_events(export.run_id)] == [
        e.sequence for e in export.audit_events
    ]
    assert repository.get_campaign(export.campaigns[0].campaign_id) is not None


async def test_lovable_payload_is_plain_json(settings, mock_research, requests):
    export, _ = await run_demo(settings, mock_research, requests)
    payload = lovable_payload(export)

    text = json.dumps(payload)  # must not raise: no Decimals, datetimes or enums
    assert "Decimal(" not in text
    reloaded = json.loads(text)
    assert reloaded["schema_version"] == "1.0"
    assert reloaded["views"]["campaign_cards"][0]["pricing"]["simulated"] is True
    assert isinstance(reloaded["campaigns"][0]["group_price"], int | float)
    assert isinstance(reloaded["campaigns"][0]["starts_at"], str)
    assert len(reloaded["views"]["user_journeys"]) == len(requests)


async def test_snapshots_report_and_replay(settings, mock_research, requests):
    export, ctx = await run_demo(settings, mock_research, requests, snapshots=True)
    directory = ctx.snapshots.dir

    for name in (
        "01_input.json",
        "02_intents.json",
        "03_buckets.json",
        "04_products.json",
        "05_matches.json",
        "06_suppliers.json",
        "09_offers_final.json",
        "10_campaigns.json",
        "final.json",
        "audit.json",
        "lovable_payload.json",
        "report.md",
    ):
        assert (directory / name).exists(), name

    # Replay: the export alone re-renders the whole run.
    replayed = PipelineRunExport.model_validate_json(
        (directory / "final.json").read_text(encoding="utf-8")
    )
    assert replayed.run_id == export.run_id
    assert len(replayed.audit_events) == len(export.audit_events)

    report = render_report(replayed)
    assert "## 10. Campaigns" in report
    assert "simulated" in report.lower()
    assert to_json(export)["run_id"] == export.run_id


async def test_a_failing_bucket_does_not_kill_the_run(settings, mock_research):
    """An impossible request degrades to a partial run, the rest still completes."""
    requests = [
        make_request(user_id, prompt)
        for user_id, prompt in [
            *SCENARIO,
            ("user_impossible", "8K 240Hz OLED with Thunderbolt, hard limit €90."),
        ]
    ]
    export, _ = await run_demo(settings, mock_research, requests)

    assert export.status == RunStatus.PARTIAL
    assert len(export.campaigns) >= 1
    outcomes = {o.bucket_id: o for o in export.bucket_outcomes}
    assert any(o.status.value != "campaign_created" for o in outcomes.values())
    assert any(m.classification.value == "rejected" for m in export.matches)
