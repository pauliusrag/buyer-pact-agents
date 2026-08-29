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
