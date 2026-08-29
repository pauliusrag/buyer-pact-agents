"""Per-stage snapshots.

Writing every stage to ``data/demo_runs/<run_id>/`` means a failed demo can be
inspected — or re-rendered — without re-running research or negotiation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

STAGE_FILES = {
    "input": "01_input.json",
    "intents": "02_intents.json",
    "buckets": "03_buckets.json",
    "products": "04_products.json",
    "matches": "05_matches.json",
    "suppliers": "06_suppliers.json",
    "rfqs": "07_rfqs.json",
    "offers_round_1": "08_offers_round_1.json",
    "offers_final": "09_offers_final.json",
    "campaigns": "10_campaigns.json",
    "final": "final.json",
    "audit": "audit.json",
    "lovable": "lovable_payload.json",
    "report": "report.md",
}


def _encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return json.loads(value.model_dump_json())
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


class SnapshotWriter:
    """Writes stage snapshots; disabled instances are silent no-ops."""

    def __init__(self, root: Path, run_id: str, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.dir = Path(root) / run_id
        self.written: list[str] = []
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, stage: str, payload: Any) -> Path | None:
        if not self.enabled:
            return None
        filename = STAGE_FILES.get(stage, f"{stage}.json")
        path = self.dir / filename
        if filename.endswith(".md"):
            path.write_text(str(payload), encoding="utf-8")
        else:
            path.write_text(
                json.dumps(_encode(payload), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        self.written.append(filename)
        return path
