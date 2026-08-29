#!/usr/bin/env python
"""Check which capabilities are configured, without ever printing a secret.

uv run python scripts/check_env.py            # what is configured
uv run python scripts/check_env.py --probe    # also make one real Linkup call
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sye.config import get_settings  # noqa: E402
from sye.integrations.linkup_client import ResearchError, build_research_client  # noqa: E402
from sye.observability.logging import setup_logging  # noqa: E402


def mask(value: str | None) -> str:
    """Never print a key: only its length and last four characters."""
    if not value:
        return "not set"
    if len(value) <= 8:
        return f"set ({len(value)} chars)"
    return f"set ({len(value)} chars, ends …{value[-4:]})"


async def probe(settings) -> int:
    """One real Linkup search, to prove the key works."""
    try:
        client = build_research_client(settings, offline=False, max_calls=2)
    except ResearchError as exc:
        print(f"\n✗ {exc}")
        return 2

    print("\nProbing Linkup with one search…")
    try:
        products = await client.search_products(
            query=(
                "Find 3 computer monitors sold in Sweden with a 27 inch screen and "
                "2560x1440 resolution under 350 EUR. Return brand, model, canonical_name, "
                "normal_market_price, currency, listing_url and attributes."
            ),
            category="monitor",
            market="SE",
            max_results=3,
            run_id="run_env_check",
            bucket_id="bkt_env_check",
        )
    except ResearchError as exc:
        print(f"✗ Linkup call failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Linkup call failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"✓ Linkup answered: {len(products)} candidate(s), {client.calls} call(s) used")
    for product in products:
        source = product.sources[0].url if product.sources else "no source"
        print(
            f"    {product.canonical_name} — {product.normal_market_price} "
            f"{product.currency or ''} — {source}"
        )
    if products and not any(p.sources for p in products):
        print("  ! no sources came back; check that include_sources is supported by your plan")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check SYE environment configuration")
    parser.add_argument(
        "--probe", action="store_true", help="make one real Linkup call to verify the key"
    )
    args = parser.parse_args()

    setup_logging("WARNING")
    settings = get_settings()

    env_file = ROOT / ".env"
    print(
        f".env file:            {'found' if env_file.exists() else 'MISSING (cp .env.example .env)'}"
    )
    print(f"working directory:    {Path.cwd()}")
    print()
    print(f"LINKUP_API_KEY:       {mask(settings.linkup_api_key)}")
    print(f"  default depth:      {settings.linkup_default_depth}")
    print(f"  max calls per run:  {settings.linkup_max_calls_per_run}")
    print()
    print(
        f"LLM provider:         {settings.llm_provider} ({settings.llm_model or 'default model'})"
    )
    print(f"ANTHROPIC_API_KEY:    {mask(settings.anthropic_api_key)}")
    print(f"OPENAI_API_KEY:       {mask(settings.openai_api_key)}")
    print()
    print(f"default research mode: {'offline fixtures' if settings.offline else 'live web'}")
    print(f"database:              {settings.db_url}")
    print(f"fixtures:              {settings.fixtures_dir}")
    print()
    print("Capabilities:")
    print(f"  live web research   {'YES' if settings.has_linkup_key else 'no — runs use fixtures'}")
    print(f"  LLM extraction      {'YES' if settings.has_llm_key else 'no — deterministic rules'}")

    if args.probe:
        return asyncio.run(probe(settings))
    if not settings.has_linkup_key:
        print("\nSet LINKUP_API_KEY in .env, then re-run with --probe to verify it.")
    else:
        print("\nRun with --probe to verify the key against the live API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
