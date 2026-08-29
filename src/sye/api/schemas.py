"""API request/response models.

Responses are canonical domain objects wherever possible: the frontend consumes
:class:`PipelineRunExport`, :class:`AuditEvent` and :class:`Campaign` directly.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from sye.domain.primitives import SyeModel
from sye.services.scenarios import normalize_users


class ScenarioUser(SyeModel):
    user_id: str | None = None
    prompt: str
    market: str | None = None
    currency: str | None = None


class RunRequest(SyeModel):
    """The scenario JSON accepted by ``POST /api/v1/demo/runs``.

    ``users`` accepts three shapes, so a frontend can send whichever it holds:

    * ``[{"user_id": "john doe", "prompt": "..."}]``
    * ``{"john doe": "...", "jane doe": "..."}``
    * ``["...", "..."]``
    """

    scenario_name: str | None = "API scenario"
    market: str = "SE"
    currency: str = "EUR"
    users: list[ScenarioUser] = Field(default_factory=list)
    config: dict[str, Any] | None = None
    background: bool = False
    seed: int | None = None
    offline: bool | None = None

    @field_validator("users", mode="before")
    @classmethod
    def _accept_any_user_shape(cls, value: Any) -> Any:
        if value in (None, [], {}):
            return []
        return normalize_users(value)

    def to_scenario(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "market": self.market,
            "currency": self.currency,
            "users": [u.model_dump(exclude_none=True) for u in self.users],
            "config": self.config or {},
        }


class RunAccepted(SyeModel):
    run_id: str
    status: str
    stream_url: str
    export_url: str


class RunSummary(SyeModel):
    run_id: str
    scenario_name: str | None = None
    mode: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    campaigns: int = 0
    warnings: int = 0


class ScenarioInfo(SyeModel):
    key: str
    path: str
    scenario_name: str | None = None
    users: int = 0
    available: bool = True
