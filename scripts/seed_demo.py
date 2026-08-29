#!/usr/bin/env python
"""Seed the local database by running every built-in scenario offline.

uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sye.config import get_settings  # noqa: E402
from sye.domain.ids import new_run_id  # noqa: E402
from sye.graph.main_graph import run_pipeline  # noqa: E402
from sye.observability.logging import setup_logging  # noqa: E402
from sye.services.scenarios import (  # noqa: E402
    BUILTIN_SCENARIOS,
    load_scenario_file,
    parse_scenario,
)


async def main() -> int:
    setup_logging("WARNING")
    settings = get_settings()
    for key, path in sorted(BUILTIN_SCENARIOS.items()):
        target = Path(path)
        if not target.exists():
            print(f"skipping {key}: {path} not found")
            continue
        run_id = new_run_id()
        payload = load_scenario_file(target)
        name, requests, config = parse_scenario(
            payload, base_config=settings.demo_config(offline=True), run_id=run_id
        )
        export, _ = await run_pipeline(
            requests, config=config, settings=settings, run_id=run_id, scenario_name=name
        )
        print(
            f"{key:<15} {export.run_id}  {export.status.value:<9} "
            f"{len(export.campaigns)} campaign(s), {len(export.buckets)} bucket(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
