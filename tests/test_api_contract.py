"""The REST contract the Lovable frontend depends on."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sye.api.service import reset_run_manager
from sye.config import reset_settings_cache

SCENARIO = {
    "scenario_name": "API contract test",
    "market": "SE",
    "currency": "EUR",
    "users": [
        {"user_id": "api_1", "prompt": "27 inch 1440p monitor with USB-C charging, max €320."},
        {"user_id": "api_2", "prompt": "At least 27 inch QHD for work, around €280."},
    ],
    "seed": 42,
    "offline": True,
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SYE_DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("SYE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SYE_OFFLINE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("LINKUP_API_KEY", "")
    monkeypatch.setenv("SYE_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
    reset_settings_cache()
    reset_run_manager()

    from sye.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    reset_settings_cache()
    reset_run_manager()


@pytest.fixture
def completed_run(client):
    response = client.post("/api/v1/demo/runs", json=SCENARIO)
    assert response.status_code == 200
    return response.json()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["cors_origins"] == ["http://localhost:3000", "http://localhost:5173"]


def test_post_run_returns_the_canonical_export(completed_run):
    body = completed_run
    assert body["schema_version"] == "1.0"
    assert body["status"] in {"completed", "partial"}
    assert body["mode"] == "demo"
    for key in (
        "user_requests",
        "intents",
        "buckets",
        "products",
        "matches",
        "suppliers",
        "rfqs",
        "offers",
        "offer_evaluations",
        "campaigns",
        "audit_events",
        "metrics",
        "warnings",
    ):
        assert key in body, key
    assert body["campaigns"], "the demo scenario should produce a campaign"


def test_money_and_time_are_frontend_friendly(completed_run):
    campaign = completed_run["campaigns"][0]
    assert isinstance(campaign["group_price"], int | float)
    assert isinstance(campaign["starts_at"], str) and campaign["starts_at"].endswith("Z") or True
    assert campaign["data_origin"] == "simulated"
    assert campaign["status"] == "simulation_ready"
    # The whole body round-trips through plain JSON.
    json.loads(json.dumps(completed_run))


def test_get_run_events_and_export(client, completed_run):
    run_id = completed_run["run_id"]

    run = client.get(f"/api/v1/demo/runs/{run_id}")
    assert run.status_code == 200
    assert run.json()["run_id"] == run_id

    events = client.get(f"/api/v1/demo/runs/{run_id}/events").json()
    assert [e["sequence"] for e in events] == sorted(e["sequence"] for e in events)
    assert all("message" in e for e in events)

    export = client.get(f"/api/v1/demo/runs/{run_id}/export")
    assert export.status_code == 200
    assert run_id in export.headers["content-disposition"]

    report = client.get(f"/api/v1/demo/runs/{run_id}/report")
    assert report.status_code == 200
    assert "# SYE demo run" in report.text


def test_lovable_projection(client, completed_run):
    payload = client.get(f"/api/v1/demo/runs/{completed_run['run_id']}/lovable").json()
    views = payload["views"]
    assert {"campaign_cards", "user_journeys", "timeline", "bucket_summaries"} <= set(views)
    card = views["campaign_cards"][0]
    assert card["pricing"]["simulated"] is True
    assert card["product"]["name"]
    assert card["demand"]["member_user_ids"]


def test_stream_emits_audit_events(client, completed_run):
    run_id = completed_run["run_id"]
    with client.stream("GET", f"/api/v1/demo/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        body = "".join(chunk for chunk in response.iter_text())
    assert "event: audit" in body
    assert "event: end" in body
    assert "chain-of-thought" not in body


def test_campaign_endpoints(client, completed_run):
    campaign_id = completed_run["campaigns"][0]["campaign_id"]

    listed = client.get("/api/v1/campaigns").json()
    assert any(c["campaign_id"] == campaign_id for c in listed)

    single = client.get(f"/api/v1/campaigns/{campaign_id}")
    assert single.status_code == 200
    assert single.json()["campaign_id"] == campaign_id
    assert client.get("/api/v1/campaigns/does-not-exist").status_code == 404


def test_schema_endpoints_describe_the_contract(client):
    pipeline = client.get("/api/v1/schema/pipeline-run").json()
    assert pipeline["title"] == "PipelineRunExport"
    assert "campaigns" in pipeline["properties"]
    campaign = client.get("/api/v1/schema/campaign").json()
    assert campaign["title"] == "Campaign"
    assert "group_price" in campaign["properties"]


def test_scenarios_endpoints(client):
    scenarios = client.get("/api/v1/demo/scenarios").json()
    keys = {s["key"] for s in scenarios}
    assert {"easy", "edge-cases", "scale"} <= keys

    response = client.post("/api/v1/demo/scenarios/easy/run?seed=42")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert len(body["buckets"]) >= 2

    assert client.post("/api/v1/demo/scenarios/nope/run").status_code == 404


def test_background_run_returns_a_handle(client):
    response = client.post("/api/v1/demo/runs", json={**SCENARIO, "background": True})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["stream_url"].endswith("/stream")

    with client.stream("GET", body["stream_url"]) as stream:
        text = "".join(chunk for chunk in stream.iter_text())
    assert "event: end" in text

    final = client.get(f"/api/v1/demo/runs/{body['run_id']}")
    assert final.status_code == 200
    assert final.json()["status"] in {"completed", "partial"}


def test_empty_scenario_is_rejected(client):
    assert client.post("/api/v1/demo/runs", json={"users": []}).status_code == 422


def test_unknown_run_is_404(client):
    assert client.get("/api/v1/demo/runs/run_missing").status_code == 404


# --------------------------------------------------------------------------- #
# Demand front door — the endpoint a website calls on every submission
# --------------------------------------------------------------------------- #
DEMAND_BODY = {
    "market": "SE",
    "currency": "EUR",
    "users": {
        "anna": "I want a smart ring that tracks my sleep and HRV. No monthly subscription. Under €300.",
        "ben": "Looking for a sleep tracking ring, works with my iPhone, at least a week of battery. Max €320.",
        "cara": "A ring for sleep and recovery, I refuse to pay a subscription. Around €280.",
        "eva": "Fitness band with GPS and heart rate for running, waterproof, around €200.",
    },
}


def test_demand_grouping_returns_compatible_groups(client):
    response = client.post("/api/v1/demand/group", json=DEMAND_BODY)
    assert response.status_code == 200
    body = response.json()

    groups = {g["label"]: g for g in body["groups"]}
    ring_group = next(g for g in body["groups"] if "anna" in g["member_user_ids"])
    assert set(ring_group["member_user_ids"]) == {"anna", "ben", "cara"}
    assert ring_group["size"] == 3
    assert "subscription-free" in ring_group["label"]
    assert ring_group["requirements"]
    assert ring_group["explanation"]

    # eva wants a different device and is grouped separately
    assert any(g["member_user_ids"] == ["eva"] for g in body["groups"])
    assert len(groups) == 2

    # every member can be told why they are in the group
    anna = next(m for m in ring_group["members"] if m["user_id"] == "anna")
    assert anna["joined"] is True
    assert anna["explanation"]
    assert anna["inherited_requirements"] or anna["common_requirements"]

    # and what we understood from their own words
    parsed = {p["user_id"]: p for p in body["parsed"]}
    assert parsed["anna"]["category"] == "wearable"
    assert "subscription excluded" in parsed["anna"]["hard_requirements"]
    assert parsed["anna"]["max_budget"] == 300.0


def test_demand_grouping_needs_no_research_or_keys(client):
    """The front door must answer on every page load, with no external calls."""
    body = client.post("/api/v1/demand/group", json=DEMAND_BODY).json()
    assert body["engine"] == "deterministic"
    assert body["grouped_at"]


def test_demand_grouping_accepts_the_other_user_shapes(client):
    listed = client.post(
        "/api/v1/demand/group",
        json={
            "users": ["27 inch 1440p monitor with USB-C, max €320", "at least 27 inch QHD, €300"]
        },
    )
    assert listed.status_code == 200
    assert listed.json()["groups"]

    assert client.post("/api/v1/demand/group", json={"users": []}).status_code == 422


# --------------------------------------------------------------------------- #
# The SPA and the agent trace it renders
# --------------------------------------------------------------------------- #
def test_spa_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "Demand bucketing agents" in page.text
    assert 'id="input"' in page.text

    script = client.get("/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "/api/v1/demand/group" in script.text


def test_grouping_returns_a_readable_agent_trace(client):
    body = client.post("/api/v1/demand/group", json=DEMAND_BODY).json()

    trace = body["trace"]
    assert trace, "the SPA shows what the agents did; it needs a trace"
    assert [step["sequence"] for step in trace] == list(range(1, len(trace) + 1))
    agents = {step["agent"] for step in trace}
    assert {"Intent Agent", "Market Research Agent"} <= agents
    assert all(step["message"] for step in trace)
    # no half-finished steps leak into the view
    assert all(step["status"] != "started" for step in trace)


def test_parsed_view_keeps_the_customers_own_words(client):
    body = client.post("/api/v1/demand/group", json=DEMAND_BODY).json()
    anna = next(entry for entry in body["parsed"] if entry["user_id"] == "anna")
    assert anna["prompt"] == DEMAND_BODY["users"]["anna"]
    assert anna["summary"] != anna["prompt"]  # interpretation, shown side by side
