"""Run context: the non-serialisable collaborators a node needs.

LangGraph state holds domain data only. Clients, the audit logger and counters
live here and are bound into the node functions when the graph is built.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sye.agents.base import AgentContext
from sye.config import DemoConfig, Settings
from sye.domain.ids import utcnow
from sye.domain.models import ProductCandidate
from sye.integrations.linkup_client import ResearchClient, build_research_client
from sye.integrations.llm import LLMProvider, NullProvider, build_llm_provider
from sye.integrations.simulated_supplier_gateway import SimulatedSupplierGateway
from sye.integrations.supplier_gateway import HumanReviewedSupplierGateway, SupplierGateway
from sye.observability.audit import AuditLogger
from sye.services.snapshots import SnapshotWriter


@dataclass
class RunContext:
    run_id: str
    config: DemoConfig
    audit: AuditLogger
    research: ResearchClient
    llm: LLMProvider | None
    snapshots: SnapshotWriter
    gateway_factory: Callable[[dict[str, ProductCandidate]], SupplierGateway]
    started_at: datetime = field(default_factory=utcnow)
    node_durations: dict[str, int] = field(default_factory=dict)

    @property
    def llm_calls(self) -> int:
        return getattr(self.llm, "call_count", 0) if self.llm else 0

    @property
    def llm_failures(self) -> int:
        return getattr(self.llm, "failure_count", 0) if self.llm else 0

    @property
    def research_calls(self) -> int:
        return getattr(self.research, "calls", 0)

    @property
    def engine(self) -> str:
        if self.llm is None:
            return "deterministic"
        name = getattr(self.llm, "name", "unknown")
        return "deterministic" if name == "none" else f"llm:{name}"

    def record_duration(self, node: str, duration_ms: int) -> None:
        self.node_durations[node] = self.node_durations.get(node, 0) + duration_ms

    def agent_context(self) -> AgentContext:
        """The subset of this context that agents are allowed to see.

        Agents get collaborators and configuration — never the graph, the database or
        the snapshot writer.
        """
        return AgentContext(
            run_id=self.run_id,
            config=self.config,
            audit=self.audit,
            llm=self.llm,
            research=self.research,
            gateway_factory=self.gateway_factory,
        )


def build_context(
    *,
    run_id: str,
    config: DemoConfig,
    settings: Settings,
    audit: AuditLogger | None = None,
    research: ResearchClient | None = None,
    llm: LLMProvider | None = None,
    snapshots: SnapshotWriter | None = None,
) -> RunContext:
    """Assemble a run context, wiring live or offline collaborators."""
    audit = audit or AuditLogger(run_id)
    research = research or build_research_client(
        settings, offline=config.offline, seed=config.seed, max_calls=config.max_linkup_calls
    )
    if llm is None:
        llm = build_llm_provider(settings, offline=config.offline)
    if isinstance(llm, NullProvider):
        llm = None  # agents fall back to their deterministic implementations

    snapshots = snapshots or SnapshotWriter(
        settings.runs_dir, run_id, enabled=config.write_snapshots
    )

    def gateway_factory(products: dict[str, ProductCandidate]) -> SupplierGateway:
        if config.mode == "demo":
            return SimulatedSupplierGateway(seed=config.seed, products=products, run_id=run_id)
        return HumanReviewedSupplierGateway()

    return RunContext(
        run_id=run_id,
        config=config,
        audit=audit,
        research=research,
        llm=llm,
        snapshots=snapshots,
        gateway_factory=gateway_factory,
    )
