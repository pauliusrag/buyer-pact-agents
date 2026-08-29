#!/usr/bin/env python
"""Run the market research & group bucketing agent on its own.

Ingest a scenario file, group compatible demand, research the web for candidates
and report the best item match for each group. No suppliers, no negotiation, no
campaign — just this one agent.

    uv run python scripts/run_market_research.py examples/users_named.json
    uv run python scripts/run_market_research.py examples/demo_easy.json --live
    uv run python scripts/run_market_research.py examples/users_named.json --live --verbose

Accepted input shapes (see ``sye.services.scenarios.normalize_users``):

    {"users": {"john doe": "natural language request", "jane doe": "..."}}
    {"users": [{"user_id": "john doe", "prompt": "..."}]}
    {"users": ["natural language request", "..."]}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sye.agents import IntentAgent, MarketResearchAgent  # noqa: E402
from sye.agents.base import AgentContext  # noqa: E402
from sye.config import get_settings  # noqa: E402
from sye.domain.ids import new_run_id  # noqa: E402
from sye.integrations.linkup_client import ResearchError, build_research_client  # noqa: E402
from sye.integrations.llm import NullProvider, build_llm_provider  # noqa: E402
from sye.observability.audit import AuditLogger  # noqa: E402
from sye.observability.logging import setup_logging  # noqa: E402
from sye.services.constraints import describe  # noqa: E402
from sye.services.rendering import DemoRenderer  # noqa: E402
from sye.services.scenarios import (  # noqa: E402
    ScenarioError,
    load_scenario_file,
    parse_scenario,
    resolve_builtin,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest requests, build demand buckets and research the market"
    )
    parser.add_argument("scenario_file", nargs="?", help="path to a scenario JSON file")
    parser.add_argument("--scenario", help="built-in scenario key (easy, edge-cases, scale, ...)")
    parser.add_argument(
        "--live",
        dest="offline",
        action="store_false",
        default=None,
        help="research the real web with Linkup (requires LINKUP_API_KEY)",
    )
    parser.add_argument(
        "--offline",
        dest="offline",
        action="store_true",
        help="use the local fixture catalogue instead of Linkup",
    )
    parser.add_argument("--seed", type=int, help="simulation seed (default 42)")
    parser.add_argument("--output", help="write the agent result JSON here")
    parser.add_argument("--verbose", action="store_true", help="show every audit event")
    parser.add_argument("--plain", action="store_true", help="disable Rich formatting")
    return parser


async def run(args: argparse.Namespace) -> int:
    setup_logging("INFO" if args.verbose else "WARNING")
    renderer = DemoRenderer(verbose=args.verbose, use_rich=not args.plain)

    if args.scenario_file:
        scenario_path = Path(args.scenario_file)
    elif args.scenario:
        scenario_path = resolve_builtin(args.scenario)
    else:
        scenario_path = Path("examples/users_named.json")

    settings = get_settings()
    run_id = new_run_id()
    base_config = settings.demo_config(seed=args.seed, offline=args.offline, verbose=args.verbose)

    try:
        payload = load_scenario_file(scenario_path)
        scenario_name, requests, config = parse_scenario(
            payload, base_config=base_config, run_id=run_id
        )
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        research = build_research_client(
            settings, offline=config.offline, seed=config.seed, max_calls=config.max_linkup_calls
        )
    except ResearchError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2

    llm = build_llm_provider(settings, offline=config.offline)
    audit = AuditLogger(run_id)
    audit.add_sink(renderer.event)
    ctx = AgentContext(
        run_id=run_id,
        config=config,
        audit=audit,
        llm=None if isinstance(llm, NullProvider) else llm,
        research=research,
    )

    renderer.rule(f"Market research & group bucketing · {scenario_name}")
    renderer._print(
        f"run_id={run_id}  research={'Linkup (live web)' if not config.offline else 'fixtures (offline)'}"
        f"  reasoning={ctx.engine}  seed={config.seed}  users={len(requests)}",
        style="cyan",
    )

    intent_result = await IntentAgent(ctx).run(requests)
    agent = MarketResearchAgent(ctx)
    result = await agent.run(intent_result.intents)

    # -- report ------------------------------------------------------------ #
    renderer.rule("Demand buckets")
    for bucket in result.buckets:
        renderer._print(f"\n{bucket.label}  ({bucket.bucket_id})", style="bold")
        renderer._print(f"  members: {', '.join(bucket.member_user_ids)}")
        renderer._print(
            "  binding requirements: "
            + (", ".join(describe(c) for c in bucket.shared_hard_constraints) or "none stated")
        )
        renderer._print(f"  {bucket.compatibility_explanation}", style="dim")
        for index, query in enumerate(result.queries.get(bucket.bucket_id, []), start=1):
            label = "search" if index == 1 else "broadened search"
            renderer._print(f"  {label} {index}: {query}", style="dim")

    renderer.rule("Researched candidates")
    products = {p.product_id: p for p in result.products}
    for bucket in result.buckets:
        renderer._print(f"\n{bucket.label}", style="bold")
        bucket_matches = [m for m in result.matches if m.bucket_id == bucket.bucket_id]
        if not bucket_matches:
            renderer._print("  no candidates returned", style="yellow")
            continue
        for match in bucket_matches:
            product = products.get(match.product_id)
            price = product.normal_market_price if product else None
            style = {
                "qualified": "green",
                "negotiable_gap": "cyan",
                "rejected": "yellow",
            }[match.classification.value]
            renderer._print(
                f"  [{match.classification.value:<15}] {match.product_name:<34} "
                f"{price} {product.currency if product else ''}  score {match.overall_score:.2f}",
                style=style,
            )
            if match.classification.value == "rejected" and match.rejection_reasons:
                renderer._print(f"      ↳ {match.rejection_reasons[0]}", style="dim")
            if args.verbose and product and product.sources:
                renderer._print(f"      ↳ source: {product.sources[0].url}", style="dim")

    renderer.rule("Best match per bucket")
    for bucket in result.buckets:
        best = result.best_match(bucket.bucket_id)
        if best is None:
            renderer._print(f"  {bucket.label}: no product satisfies this group", style="yellow")
            continue
        product = products.get(best.product_id)
        renderer._print(
            f"  {bucket.label}\n"
            f"    → {best.product_name} at {product.normal_market_price if product else '?'} "
            f"{product.currency if product else ''} "
            f"({best.classification.value}, score {best.overall_score:.2f})\n"
            f"    → {best.explanation}",
            style="green",
        )

    metrics = result.metrics
    renderer.rule("Metrics")
    renderer._print(
        "\n".join(f"  {key}: {value}" for key, value in metrics.items()),
    )
    if result.warnings:
        renderer.rule("Warnings")
        for warning in result.warnings:
            renderer._print(f"  ! {warning}", style="yellow")

    payload_out = {
        "run_id": run_id,
        "scenario_name": scenario_name,
        "research_provider": research.name,
        "reasoning_engine": ctx.engine,
        "intents": [json.loads(i.model_dump_json()) for i in intent_result.intents],
        "result": json.loads(result.model_dump_json()),
        "audit_events": [json.loads(e.model_dump_json()) for e in audit.ordered()],
    }
    target = (
        Path(args.output) if args.output else settings.runs_dir / run_id / "market_research.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload_out, indent=2, ensure_ascii=False), encoding="utf-8")
    renderer._print(f"\nAgent result: {target}")

    return 0 if result.campaign_ready_buckets() else 1


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
