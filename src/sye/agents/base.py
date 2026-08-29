"""Agent foundations.

Each pipeline phase that involves real autonomous work — interpreting language,
deciding who can be served by one product, deciding what to research and whether
the evidence is good enough, deciding what to ask a supplier for, deciding when a
deal is good enough to stop — is owned by exactly one agent.

An agent:

* receives a typed input and returns a typed result (never prose across a boundary),
* owns its own decisions, including how many times to use a tool and when to stop,
* records every decision it makes in the audit trail,
* degrades to a deterministic path when the LLM or the web is unavailable,
* knows nothing about LangGraph, FastAPI or the database.

The graph wires agents together; it does not contain their reasoning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import Field

from sye.config import DemoConfig
from sye.domain.models import ProductCandidate
from sye.domain.primitives import SyeModel
from sye.integrations.linkup_client import ResearchClient
from sye.integrations.llm import LLMProvider
from sye.integrations.supplier_gateway import SupplierGateway
from sye.observability.audit import AuditLogger


@dataclass
class AgentContext:
    """The collaborators an agent is allowed to use.

    Everything an agent needs from the outside world arrives here, which is what
    makes an agent runnable on its own — in a test, a script, or one node of the
    graph — without booting the rest of the pipeline.
    """

    run_id: str
    config: DemoConfig
    audit: AuditLogger
    llm: LLMProvider | None = None
    research: ResearchClient | None = None
    gateway_factory: Callable[[dict[str, ProductCandidate]], SupplierGateway] | None = None

    @property
    def engine(self) -> str:
        """Which reasoning engine the agents will actually use."""
        if self.llm is None:
            return "deterministic"
        name = getattr(self.llm, "name", "unknown")
        return "deterministic" if name == "none" else f"llm:{name}"

    def require_research(self) -> ResearchClient:
        if self.research is None:
            raise AgentError("this agent needs a research client but none was provided")
        return self.research

    def require_gateway(self, products: dict[str, ProductCandidate]) -> SupplierGateway:
        if self.gateway_factory is None:
            raise AgentError("this agent needs a supplier gateway but none was provided")
        return self.gateway_factory(products)


class AgentError(RuntimeError):
    """An agent could not run at all (missing collaborator, invalid input)."""


class AgentResult(SyeModel):
    """Base for every agent's typed output.

    ``warnings`` and ``metrics`` are part of the contract: an agent that degraded
    says so in its own result rather than failing silently.
    """

    agent: str
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class Agent(ABC):
    """Base class: a named, context-bound unit of autonomous work."""

    name: str = "agent"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    @property
    def config(self) -> DemoConfig:
        return self.ctx.config

    @property
    def audit(self) -> AuditLogger:
        return self.ctx.audit

    @property
    def llm(self) -> LLMProvider | None:
        return self.ctx.llm

    @property
    def run_id(self) -> str:
        return self.ctx.run_id

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> AgentResult:
        """Execute the agent's whole phase and return its typed result."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} run_id={self.run_id} engine={self.ctx.engine}>"
