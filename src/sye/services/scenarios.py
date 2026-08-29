"""Scenario loading.

A scenario file is the demo's input contract: a name, a market, a currency and a
list of users with one free-text prompt each. Optional ``config`` overrides the
per-run knobs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sye.config import DemoConfig
from sye.domain.ids import stable_id, utcnow
from sye.domain.models import UserRequest

BUILTIN_SCENARIOS = {
    "easy": "examples/demo_easy.json",
    "edge-cases": "examples/demo_edge_cases.json",
    "scale": "examples/demo_scale.json",
    "monitors": "examples/users_monitors.json",
    "monitors-mixed": "examples/users_monitors_mixed.json",
}


class ScenarioError(ValueError):
    pass


def load_scenario_file(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise ScenarioError(f"scenario file not found: {target}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"invalid scenario JSON in {target}: {exc}") from exc


def normalize_users(raw: Any) -> list[dict[str, Any]]:
    """Accept any of the shapes a scenario file is written in.

    * ``[{"user_id": "john", "prompt": "..."}]``  — the canonical form
    * ``{"john doe": "...", "jane doe": "..."}``  — a name → request mapping
    * ``["...", "..."]``                          — bare prompts, ids generated
    """
    if isinstance(raw, dict):
        return [{"user_id": str(key), "prompt": value} for key, value in raw.items()]
    if isinstance(raw, list):
        users: list[dict[str, Any]] = []
        for index, entry in enumerate(raw):
            if isinstance(entry, str):
                users.append({"user_id": f"user_{index + 1:03d}", "prompt": entry})
            elif isinstance(entry, dict) and len(entry) == 1 and "prompt" not in entry:
                # {"john doe": "..."} written as a one-key object inside a list
                key, value = next(iter(entry.items()))
                users.append({"user_id": str(key), "prompt": value})
            elif isinstance(entry, dict):
                users.append(dict(entry))
            else:
                raise ScenarioError(f"cannot read user entry {entry!r}")
        return users
    raise ScenarioError("'users' must be a list or an object mapping names to requests")


def parse_scenario(
    payload: dict[str, Any], *, base_config: DemoConfig, run_id: str
) -> tuple[str, list[UserRequest], DemoConfig]:
    """Return ``(scenario_name, user_requests, config)``."""
    users = normalize_users(payload.get("users") or [])
    if not users:
        raise ScenarioError("scenario contains no users")

    market = payload.get("market", base_config.market)
    currency = payload.get("currency", base_config.currency)
    overrides = {k: v for k, v in (payload.get("config") or {}).items() if v is not None}
    config = base_config.model_copy(update={"market": market, "currency": currency, **overrides})

    now = utcnow()
    requests: list[UserRequest] = []
    for index, user in enumerate(users):
        user_id = str(user.get("user_id") or f"user_{index + 1:03d}")
        prompt = str(user.get("prompt") or "").strip()
        if not isinstance(user.get("prompt"), str) and user.get("prompt") is not None:
            raise ScenarioError(f"user {user_id} has a non-text prompt")
        if not prompt:
            raise ScenarioError(f"user {user_id} has an empty prompt")
        requests.append(
            UserRequest(
                user_id=user_id,
                request_id=stable_id("req", run_id, user_id),
                prompt=prompt,
                market=user.get("market", market),
                currency=user.get("currency", currency),
                created_at=now,
            )
        )

    return str(payload.get("scenario_name") or "demo scenario"), requests, config


def resolve_builtin(name: str) -> Path:
    key = name.strip().lower()
    if key not in BUILTIN_SCENARIOS:
        raise ScenarioError(
            f"unknown scenario {name!r}; available: {', '.join(sorted(BUILTIN_SCENARIOS))}"
        )
    return Path(BUILTIN_SCENARIOS[key])


def list_builtin() -> list[dict[str, Any]]:
    out = []
    for key, path in sorted(BUILTIN_SCENARIOS.items()):
        target = Path(path)
        payload = load_scenario_file(target) if target.exists() else {}
        out.append(
            {
                "key": key,
                "path": path,
                "scenario_name": payload.get("scenario_name"),
                "users": len(payload.get("users", [])),
                "available": target.exists(),
            }
        )
    return out
