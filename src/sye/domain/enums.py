"""Enumerations shared by every stage.

Enum *values* are the wire format: the frontend never needs custom decoding.
"""

from enum import Enum


class DataOrigin(str, Enum):
    """Where a piece of business data came from. Never guess this value."""

    USER = "user"
    LLM_INFERRED = "llm_inferred"
    WEB_RESEARCH = "web_research"
    SUPPLIER = "supplier"
    SIMULATED = "simulated"
    SYSTEM = "system"


class ConstraintOperator(str, Enum):
    EQ = "eq"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    CONTAINS_ANY = "contains_any"
    CONTAINS_ALL = "contains_all"
    BOOLEAN = "boolean"


class Importance(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class EvaluationResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NEGOTIABLE = "negotiable"


class MatchClassification(str, Enum):
    QUALIFIED = "qualified"
    NEGOTIABLE_GAP = "negotiable_gap"
    REJECTED = "rejected"


class BucketStatus(str, Enum):
    OPEN = "open"
    NO_VIABLE_PRODUCT = "no_viable_product"
    NO_SUPPLIER = "no_supplier"
    NO_QUALIFYING_OFFER = "no_qualifying_offer"
    CAMPAIGN_CREATED = "campaign_created"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RunMode(str, Enum):
    DEMO = "demo"
    LIVE = "live"


class AuditStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    WARNING = "warning"
    FAILED = "failed"
