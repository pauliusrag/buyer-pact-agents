"""Shared primitives used by both the domain models and the audit events."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

CENTS = Decimal("0.01")


def money_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP))


Money = Annotated[
    Decimal,
    PlainSerializer(money_to_float, return_type=float, when_used="json"),
]
"""A Decimal that serialises as a plain JSON number rounded to 2 decimals."""


class SyeModel(BaseModel):
    """Base model: JSON-clean and frontend friendly."""

    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=False,
        ser_json_timedelta="iso8601",
    )


class EvidenceSource(SyeModel):
    """A citation for a fact learned outside our own system."""

    title: str | None = None
    url: str
    snippet: str | None = None
    retrieved_at: datetime
    provider: str = "linkup"
