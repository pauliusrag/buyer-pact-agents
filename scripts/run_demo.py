#!/usr/bin/env python
"""Run the SYE demo end to end.

uv run python scripts/run_demo.py examples/demo_easy.json
uv run python scripts/run_demo.py examples/demo_easy.json --verbose --seed 42
uv run python scripts/run_demo.py --scenario easy --live
uv run python scripts/run_demo.py --replay data/demo_runs/<run_id>/final.json
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

from sye.config import get_settings  # noqa: E402
from sye.domain.ids import new_run_id  # noqa: E402
from sye.domain.models import PipelineRunExport  # noqa: E402
from sye.graph.context import build_context  # noqa: E402
from sye.graph.main_graph import run_pipeline  # noqa: E402
from sye.integrations.linkup_client import ResearchError  # noqa: E402
from sye.observability.audit import AuditLogger  # noqa: E402
from sye.observability.logging import setup_logging  # noqa: E402
from sye.services.exports import lovable_payload, to_json  # noqa: E402
from sye.services.rendering import DemoRenderer  # noqa: E402
from sye.services.scenarios import (  # noqa: E402
    ScenarioError,
    load_scenario_file,
    parse_scenario,
    resolve_builtin,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SYE demand-aggregation demo")
    parser.add_argument("scenario_file", nargs="?", help="path to a scenario JSON file")
    parser.add_argument("--scenario", help="built-in scenario key (easy, edge-cases, scale, ...)")
    parser.add_argument("--replay", help="render a previously exported final.json (no API calls)")
    parser.add_argument("--seed", type=int, help="simulation seed (default 42)")
    parser.add_argument(
        "--offline",
        dest="offline",
        action="store_true",
        default=None,
        help="use the local fixture catalogue instead of Linkup",
    )
    parser.add_argument(
        "--live",
        dest="offline",
        action="store_false",
        help="use live Linkup research (requires LINKUP_API_KEY)",
    )
    parser.add_argument("--output", help="write the export JSON to this path as well")
    parser.add_argument("--verbose", action="store_true", help="show every audit event")
    parser.add_argument("--no-snapshots", action="store_true", help="skip per-stage snapshots")
    parser.add_argument("--no-persist", action="store_true", help="skip SQLite persistence")
    parser.add_argument("--plain", action="store_true", help="disable Rich formatting")
    return parser


def replay(path: str, renderer: DemoRenderer) -> int:
    """Render a finished run from its export. No LLM or Linkup calls are made."""
    target = Path(path)
    if not target.exists():
        print(f"replay file not found: {target}", file=sys.stderr)
        return 2
    export = PipelineRunExport.model_validate_json(target.read_text(encoding="utf-8"))
    renderer.rule(f"REPLAY {export.run_id} (no external calls)")
    for event in export.audit_events:
        renderer.event(event)
    renderer.buckets(export)
    renderer.offers(export)
    renderer.summary(export, output_path=str(target))
    return 0


async def run(args: argparse.Namespace) -> int:
    setup_logging("WARNING" if not args.verbose else "INFO")
    renderer = DemoRenderer(verbose=args.verbose, use_rich=not args.plain)

    if args.replay:
        return replay(args.replay, renderer)

    if args.scenario_file:
        scenario_path = Path(args.scenario_file)
    elif args.scenario:
        scenario_path = resolve_builtin(args.scenario)
    else:
        scenario_path = Path("examples/demo_easy.json")

    settings = get_settings()
    run_id = new_run_id()
    base_config = settings.demo_config(
        seed=args.seed,
        offline=args.offline,
        verbose=args.verbose,
        write_snapshots=not args.no_snapshots,
    )

    try:
        payload = load_scenario_file(scenario_path)
        scenario_name, requests, config = parse_scenario(
            payload, base_config=base_config, run_id=run_id
        )
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    renderer.rule(f"SYE demo · {scenario_name}")
    renderer._print(
        f"run_id={run_id}  mode={config.mode}  "
        f"research={'fixtures (offline)' if config.offline else 'Linkup (live)'}  "
        f"seed={config.seed}  users={len(requests)}",
        style="cyan",
    )

    audit = AuditLogger(run_id)
    audit.add_sink(renderer.event)
    try:
        ctx = build_context(run_id=run_id, config=config, settings=settings, audit=audit)
    except ResearchError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2

    export, ctx = await run_pipeline(
        requests,
        config=config,
        settings=settings,
        run_id=run_id,
        scenario_name=scenario_name,
        ctx=ctx,
        persist=not args.no_persist,
    )

    renderer.buckets(export)
    renderer.offers(export)

    snapshot_dir = ctx.snapshots.dir if ctx.snapshots.enabled else None
    output_path = str(snapshot_dir / "final.json") if snapshot_dir else None
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(to_json(export), indent=2), encoding="utf-8")
        lovable = target.with_name(target.stem + "_lovable.json")
        lovable.write_text(json.dumps(lovable_payload(export), indent=2), encoding="utf-8")
        output_path = str(target)

    renderer.summary(export, output_path=output_path)
    if snapshot_dir:
        renderer._print(
            f"Stage snapshots, report.md and lovable_payload.json: {snapshot_dir}", style="dim"
        )

    return 0 if export.status.value in ("completed", "partial") else 1


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
