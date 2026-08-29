"""Terminal rendering for the demo CLI.

Uses Rich when available and degrades to plain text otherwise. It never prints
model prompts or responses — only recorded audit events and export data.
"""

from __future__ import annotations

from decimal import Decimal

from sye.domain.enums import AuditStatus
from sye.domain.models import PipelineRunExport

try:  # pragma: no cover - presentation only
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    _RICH = True
except Exception:  # pragma: no cover
    _RICH = False

_ICONS = {
    AuditStatus.STARTED: "·",
    AuditStatus.COMPLETED: "✓",
    AuditStatus.WARNING: "!",
    AuditStatus.FAILED: "✗",
}
_STYLES = {
    AuditStatus.STARTED: "dim",
    AuditStatus.COMPLETED: "green",
    AuditStatus.WARNING: "yellow",
    AuditStatus.FAILED: "red",
}


class DemoRenderer:
    def __init__(self, *, verbose: bool = False, use_rich: bool = True) -> None:
        self.verbose = verbose
        self.console = Console() if (_RICH and use_rich) else None

    # -- primitives -------------------------------------------------------- #
    def _print(self, text: str, style: str | None = None) -> None:
        if self.console is not None:
            self.console.print(text, style=style, highlight=False)
        else:
            print(text)

    def rule(self, title: str) -> None:
        if self.console is not None:
            self.console.rule(f"[bold]{title}")
        else:
            print(f"\n=== {title} ===")

    # -- live timeline ----------------------------------------------------- #
    def event(self, event) -> None:
        if event.status == AuditStatus.STARTED and not self.verbose:
            return
        icon = _ICONS[event.status]
        line = f"[{event.sequence:02d}] {icon} {event.node}: {event.message}"
        self._print(line, style=_STYLES[event.status])
        if self.verbose and event.decision:
            self._print(f"        ↳ {event.decision}", style="dim")

    # -- final summary ----------------------------------------------------- #
    def summary(self, export: PipelineRunExport, *, output_path: str | None = None) -> None:
        metrics = export.metrics
        self.rule("DEMO COMPLETE")
        lines = [
            f"Run:                 {export.run_id}  ({export.status.value})",
            f"Scenario:            {export.scenario_name}",
            f"Users:               {metrics.get('users', len(export.user_requests))}",
            f"Demand buckets:      {metrics.get('demand_buckets', len(export.buckets))}",
            f"Products researched: {metrics.get('products_researched', 0)} "
            f"(qualified {metrics.get('products_qualified', 0)}, "
            f"negotiable {metrics.get('products_negotiable', 0)}, "
            f"rejected {metrics.get('products_rejected', 0)})",
            f"Suppliers:           {metrics.get('suppliers_researched', 0)}",
            f"Simulated offers:    {metrics.get('simulated_offers', 0)} "
            f"across {metrics.get('negotiation_rounds', 1)} round(s)",
            f"Campaigns created:   {metrics.get('campaigns_created', 0)}",
            f"Research calls:      {metrics.get('linkup_calls', 0)}   "
            f"LLM calls: {metrics.get('llm_calls', 0)} ({metrics.get('reasoning_engine')})",
            f"Duration:            {metrics.get('total_duration_ms', 0)} ms",
        ]
        self._print("\n".join(lines))

        for index, campaign in enumerate(export.campaigns, start=1):
            bucket = next((b for b in export.buckets if b.bucket_id == campaign.bucket_id), None)
            body = [
                f"{len(campaign.member_user_ids)} buyers · {campaign.committed_demand} units "
                f"(min {campaign.min_buyers})",
                f"Selected: {campaign.title}",
                f"Bucket: {bucket.label if bucket else campaign.bucket_id}",
                f"Reference market price:    {campaign.normal_market_price} {campaign.currency}",
                f"SIMULATED group price:     {campaign.group_price} {campaign.currency}",
                f"SIMULATED discount:        {campaign.discount_percent}%",
                f"Why: {campaign.why_this_product}",
            ]
            title = f"Campaign {chr(64 + index)} · {campaign.campaign_id}"
            if self.console is not None:
                self.console.print(Panel("\n".join(body), title=title, border_style="cyan"))
            else:
                print(f"\n--- {title} ---\n" + "\n".join(body))

        if not export.campaigns:
            self._print(
                "No campaign was created: see warnings and bucket outcomes below.", style="yellow"
            )

        if export.warnings:
            self.rule("Warnings")
            for warning in export.warnings:
                self._print(f"  ! {warning}", style="yellow")

        improvement = metrics.get("simulated_negotiation_improvement_percent")
        if improvement:
            self._print(
                f"\nSimulated negotiation improved the best landed cost by {improvement}% "
                f"({metrics.get('initial_best_offer')} → {metrics.get('final_best_offer')} "
                f"{export.currency}).",
                style="cyan",
            )

        self._print(
            "\nAll supplier offers, negotiations and campaign prices above are SIMULATED.",
            style="bold yellow",
        )
        if output_path:
            self._print(f"\nExport:\n{output_path}")

    # -- inspection helpers ------------------------------------------------ #
    def buckets(self, export: PipelineRunExport) -> None:
        self.rule("Demand buckets")
        if self.console is not None:
            table = Table(show_header=True, header_style="bold")
            for column in ("Bucket", "Label", "Members", "Qty", "Ceiling", "Score"):
                table.add_column(column)
            for bucket in export.buckets:
                table.add_row(
                    bucket.bucket_id,
                    bucket.label,
                    ", ".join(bucket.member_user_ids),
                    str(bucket.demand_quantity),
                    f"{bucket.price_ceiling} {bucket.currency}" if bucket.price_ceiling else "—",
                    f"{bucket.compatibility_score:.2f}",
                )
            self.console.print(table)
        else:
            for bucket in export.buckets:
                print(f"  {bucket.bucket_id} {bucket.label} [{', '.join(bucket.member_user_ids)}]")

    def offers(self, export: PipelineRunExport) -> None:
        if not export.offers:
            return
        self.rule("Simulated offer rounds")
        evaluations = {e.offer_id: e for e in export.offer_evaluations}
        suppliers = {s.supplier_id: s for s in export.suppliers}
        for offer in sorted(export.offers, key=lambda o: (o.negotiation_round, o.offer_id)):
            evaluation = evaluations.get(offer.offer_id)
            landed = Decimal(evaluation.landed_unit_cost) if evaluation else None
            supplier = suppliers.get(offer.supplier_id)
            self._print(
                f"  round {offer.negotiation_round} · "
                f"{supplier.name if supplier else offer.supplier_id:<32} "
                f"unit {offer.unit_price} {offer.currency} · landed {landed} · "
                f"{'qualifies' if evaluation and evaluation.qualifies else 'does not qualify'}"
            )
