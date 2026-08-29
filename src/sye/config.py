"""Runtime configuration.

``Settings`` = process/environment level (keys, URLs, CORS).
``DemoConfig`` = per-run knobs, overridable from the scenario JSON or the CLI.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sye.domain.primitives import SyeModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DemoConfig(SyeModel):
    """Per-run configuration. Deterministic given the same values + seed."""

    mode: Literal["demo", "live"] = "demo"
    market: str = "SE"
    currency: str = "EUR"

    seed: int = 42

    max_products_per_bucket: int = 6
    max_research_attempts: int = 2
    max_verifications_per_bucket: int = 3
    """How many thin candidates per bucket may be re-checked against a second source."""
    """How many times the market research agent may search for one bucket. The second
    attempt broadens the query when the first returns nothing that fits."""
    max_suppliers_per_product: int = 3
    max_negotiation_rounds: int = 2
    max_linkup_calls: int = 20

    campaign_duration_hours: int = 72
    min_buyers_ratio: float = 0.75

    # Bucketing
    bucket_merge_threshold: float = 0.55
    price_tier_ratio: float = 0.5
    materiality_threshold: float = 0.5
    """A merge is blocked when it would impose a requirement this much stricter than
    the market baseline on a member who never asked for it."""
    judge_gray_zone: tuple[float, float] = (0.40, 0.62)

    # Matching / offers
    price_negotiable_headroom: float = 0.35  # up to +35% over ceiling is negotiable
    offer_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "landed_cost": 0.60,
            "fulfillment": 0.20,
            "warranty": 0.10,
            "terms": 0.10,
        }
    )

    offline: bool = False
    write_snapshots: bool = True
    verbose: bool = False


class Settings(BaseSettings):
    """Environment-level settings (``.env`` is read automatically)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # LLM
    llm_provider: Literal["anthropic", "openai", "none"] = Field("anthropic", alias="LLM_PROVIDER")
    llm_model: str | None = Field(None, alias="LLM_MODEL")
    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    llm_timeout_seconds: float = Field(60.0, alias="LLM_TIMEOUT_SECONDS")

    # Linkup
    linkup_api_key: str | None = Field(None, alias="LINKUP_API_KEY")
    linkup_default_depth: Literal["fast", "standard", "deep"] = Field(
        "standard", alias="LINKUP_DEFAULT_DEPTH"
    )
    linkup_max_calls_per_run: int = Field(20, alias="LINKUP_MAX_CALLS_PER_RUN")
    linkup_timeout_seconds: float = Field(45.0, alias="LINKUP_TIMEOUT_SECONDS")
    linkup_max_retries: int = Field(2, alias="LINKUP_MAX_RETRIES")

    # Demo defaults
    mode: Literal["demo", "live"] = Field("demo", alias="SYE_MODE")
    offline: bool = Field(True, alias="SYE_OFFLINE")
    seed: int = Field(42, alias="SYE_SEED")
    market: str = Field("SE", alias="SYE_MARKET")
    currency: str = Field("EUR", alias="SYE_CURRENCY")
    data_dir: Path = Field(Path("data"), alias="SYE_DATA_DIR")
    fixtures_dir_override: Path | None = Field(None, alias="SYE_FIXTURES_DIR")
    db_url: str = Field("sqlite:///data/sye.db", alias="SYE_DB_URL")
    log_level: str = Field("INFO", alias="SYE_LOG_LEVEL")

    # API
    cors_origins: str = Field(
        "http://localhost:3000,http://localhost:5173", alias="SYE_CORS_ORIGINS"
    )

    # Tracing (optional, never required)
    langsmith_tracing: bool = Field(False, alias="LANGSMITH_TRACING")
    langsmith_project: str = Field("sye-demand-aggregation", alias="LANGSMITH_PROJECT")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_llm_key(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return False

    @property
    def has_linkup_key(self) -> bool:
        return bool(self.linkup_api_key)

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "demo_runs"

    @property
    def fixtures_dir(self) -> Path:
        """Offline catalogue location.

        Fixtures are a project asset, not run output: pointing ``SYE_DATA_DIR`` at a
        scratch directory (tests, containers) must not lose them.
        """
        if self.fixtures_dir_override is not None:
            return self.fixtures_dir_override
        local = self.data_dir / "fixtures"
        if local.exists():
            return local
        return PROJECT_ROOT / "data" / "fixtures"

    def demo_config(self, **overrides: object) -> DemoConfig:
        base = DemoConfig(
            mode=self.mode,
            market=self.market,
            currency=self.currency,
            seed=self.seed,
            offline=self.offline,
            max_linkup_calls=self.linkup_max_calls_per_run,
        )
        clean = {k: v for k, v in overrides.items() if v is not None}
        return base.model_copy(update=clean) if clean else base


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def configure_langsmith(settings: Settings) -> None:
    """Enable LangSmith tracing only when explicitly configured."""
    if settings.langsmith_tracing and os.getenv("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
