"""Deterministic identifier helpers.

IDs are UUID-based strings. They are derived from stable natural keys so that two
runs of the same scenario with the same ``run_id`` produce identical identifiers,
which makes snapshots, replays and diffing useful.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

_NAMESPACE = uuid.UUID("6f9d3f2a-6a1e-5c7e-9a4c-1d0f6e2b7c31")


def stable_id(prefix: str, *parts: object) -> str:
    """Return ``<prefix>_<12 hex chars>`` derived deterministically from ``parts``."""
    key = "|".join(str(p) for p in parts)
    return f"{prefix}_{uuid.uuid5(_NAMESPACE, key).hex[:12]}"


def new_run_id() -> str:
    """A fresh run identifier: sortable by time, unique per process."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


def utcnow() -> datetime:
    return datetime.now(UTC)
