"""SQLite persistence.

We store the canonical export JSON plus indexed summary tables. That is enough
for the demo's real requirement: a run survives a process restart, exports are
stable, and audit events can be replayed.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Engine,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)

metadata = MetaData()

runs = Table(
    "runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("scenario_name", String),
    Column("mode", String),
    Column("status", String),
    Column("started_at", DateTime),
    Column("completed_at", DateTime),
    Column("campaigns", Integer),
    Column("warnings", Integer),
    Column("export", JSON),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("run_id", String, index=True),
    Column("sequence", Integer),
    Column("timestamp", DateTime),
    Column("node", String),
    Column("event_type", String),
    Column("status", String),
    Column("message", Text),
    Column("payload", JSON),
)

campaigns = Table(
    "campaigns",
    metadata,
    Column("campaign_id", String, primary_key=True),
    Column("run_id", String, index=True),
    Column("bucket_id", String),
    Column("title", String),
    Column("group_price", Float),
    Column("currency", String),
    Column("status", String),
    Column("payload", JSON),
)

_ENGINES: dict[str, Engine] = {}


def get_engine(db_url: str = "sqlite:///data/sye.db") -> Engine:
    """One engine per URL, with the parent directory created on demand."""
    if db_url not in _ENGINES:
        if db_url.startswith("sqlite:///"):
            path = Path(db_url.replace("sqlite:///", "", 1))
            if path.parent and str(path.parent) not in ("", "."):
                path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(db_url, future=True)
        metadata.create_all(engine)
        _ENGINES[db_url] = engine
    return _ENGINES[db_url]


def init_db(db_url: str = "sqlite:///data/sye.db") -> Engine:
    return get_engine(db_url)


def reset_engines() -> None:
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()
