"""Repositories for runs, audit events and campaigns."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, insert, select, update

from sye.domain.events import AuditEvent
from sye.domain.models import PipelineRunExport
from sye.persistence.db import audit_events, campaigns, get_engine, runs


class RunRepository:
    def __init__(self, db_url: str = "sqlite:///data/sye.db") -> None:
        self.engine = get_engine(db_url)

    # -- runs -------------------------------------------------------------- #
    def save_run(self, export: PipelineRunExport) -> None:
        payload = json.loads(export.model_dump_json())
        row = {
            "run_id": export.run_id,
            "scenario_name": export.scenario_name,
            "mode": export.mode.value,
            "status": export.status.value,
            "started_at": export.started_at.replace(tzinfo=None),
            "completed_at": export.completed_at.replace(tzinfo=None)
            if export.completed_at
            else None,
            "campaigns": len(export.campaigns),
            "warnings": len(export.warnings),
            "export": payload,
        }
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(runs.c.run_id).where(runs.c.run_id == export.run_id)
            ).first()
            if exists:
                conn.execute(update(runs).where(runs.c.run_id == export.run_id).values(**row))
            else:
                conn.execute(insert(runs).values(**row))

            conn.execute(delete(campaigns).where(campaigns.c.run_id == export.run_id))
            for campaign in export.campaigns:
                data = json.loads(campaign.model_dump_json())
                conn.execute(
                    insert(campaigns).values(
                        campaign_id=campaign.campaign_id,
                        run_id=export.run_id,
                        bucket_id=campaign.bucket_id,
                        title=campaign.title,
                        group_price=float(campaign.group_price),
                        currency=campaign.currency,
                        status=campaign.status,
                        payload=data,
                    )
                )

    def get_run(self, run_id: str) -> PipelineRunExport | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(runs.c.export).where(runs.c.run_id == run_id)).first()
        if row is None or row[0] is None:
            return None
        return PipelineRunExport.model_validate(row[0])

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(
                    runs.c.run_id,
                    runs.c.scenario_name,
                    runs.c.mode,
                    runs.c.status,
                    runs.c.started_at,
                    runs.c.completed_at,
                    runs.c.campaigns,
                    runs.c.warnings,
                )
                .order_by(runs.c.started_at.desc())
                .limit(limit)
            ).all()
        return [dict(row._mapping) for row in rows]

    # -- audit events ------------------------------------------------------ #
    def append_event(self, event: AuditEvent) -> None:
        payload = json.loads(event.model_dump_json())
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(audit_events.c.event_id).where(audit_events.c.event_id == event.event_id)
            ).first()
            if exists:
                return
            conn.execute(
                insert(audit_events).values(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    sequence=event.sequence,
                    timestamp=event.timestamp.replace(tzinfo=None),
                    node=event.node,
                    event_type=event.event_type,
                    status=event.status.value,
                    message=event.message,
                    payload=payload,
                )
            )

    def get_events(self, run_id: str) -> list[AuditEvent]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(audit_events.c.payload)
                .where(audit_events.c.run_id == run_id)
                .order_by(audit_events.c.sequence)
            ).all()
        return [AuditEvent.model_validate(row[0]) for row in rows]

    # -- campaigns --------------------------------------------------------- #
    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(campaigns.c.payload, campaigns.c.run_id).where(
                    campaigns.c.campaign_id == campaign_id
                )
            ).first()
        if row is None:
            return None
        payload = dict(row[0])
        payload["run_id"] = payload.get("run_id") or row[1]
        return payload

    def list_campaigns(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(campaigns.c.payload, campaigns.c.run_id).limit(limit)).all()
        out = []
        for payload, run_id in rows:
            data = dict(payload)
            data["run_id"] = data.get("run_id") or run_id
            out.append(data)
        return out
