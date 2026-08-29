#!/usr/bin/env python
"""Re-export a stored run: canonical JSON, Lovable payload and report.md.

uv run python scripts/export_demo.py <run_id> --out data/demo_runs
uv run python scripts/export_demo.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sye.config import get_settings  # noqa: E402
from sye.persistence.repositories import RunRepository  # noqa: E402
from sye.services.exports import lovable_payload, to_json  # noqa: E402
from sye.services.report import render_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a stored SYE run")
    parser.add_argument("run_id", nargs="?", help="run to export (default: latest)")
    parser.add_argument("--list", action="store_true", help="list stored runs")
    parser.add_argument("--out", default=None, help="output directory")
    args = parser.parse_args()

    settings = get_settings()
    repository = RunRepository(settings.db_url)

    if args.list:
        for row in repository.list_runs():
            print(
                f"{row['run_id']}  {row['status']:<9} {row['campaigns']} campaign(s)  "
                f"{row['scenario_name']}"
            )
        return 0

    run_id = args.run_id
    if not run_id:
        rows = repository.list_runs(limit=1)
        if not rows:
            print("no runs stored yet; run scripts/run_demo.py first", file=sys.stderr)
            return 2
        run_id = rows[0]["run_id"]

    export = repository.get_run(run_id)
    if export is None:
        print(f"run not found: {run_id}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else settings.runs_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "final.json").write_text(json.dumps(to_json(export), indent=2), encoding="utf-8")
    (out_dir / "lovable_payload.json").write_text(
        json.dumps(lovable_payload(export), indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_report(export), encoding="utf-8")
    print(f"exported {run_id} to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
