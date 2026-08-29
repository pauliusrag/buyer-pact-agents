"""Internet research.

All public-web access goes through this module — nothing else in the codebase
imports Linkup. Two implementations satisfy the same protocol:

* ``LinkupResearchClient`` — live Linkup Search (structured output + sources).
* ``FixtureResearchClient`` — offline catalogue, used only when the run is
  explicitly ``offline``. Its objects are marked ``data_origin="system"`` and
  carry a ``fixture://`` evidence URL, so fixtures can never be mistaken for
  web research.

Failures are surfaced, never papered over: the caller records a warning and the
run degrades to ``partial``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
from pathlib import Path
from typing import Any, Protocol

from sye.config import Settings
from sye.domain.enums import DataOrigin
from sye.domain.ids import stable_id, utcnow
from sye.domain.models import (
    EvidenceSource,
    ProductCandidate,
    ProductDiscovery,
    ProductDiscoveryList,
    SupplierCandidate,
    SupplierDiscoveryList,
)
from sye.domain.vocabulary import MONITOR_ATTRIBUTE_HINT, attribute_hint
from sye.observability.logging import get_logger

logger = get_logger("sye.linkup")


class ResearchError(RuntimeError):
    """A research call failed after retries, or the per-run budget is exhausted."""


class ResearchClient(Protocol):
    name: str
    calls: int

    async def search_products(
        self,
        *,
        query: str,
        category: str,
        market: str,
        max_results: int,
        run_id: str,
        bucket_id: str,
        constraints: list[Any] | None = None,
    ) -> list[ProductCandidate]: ...

    async def verify_product(
        self, product: ProductCandidate, *, run_id: str
    ) -> ProductCandidate: ...

    async def search_suppliers(
        self,
        *,
        product: ProductCandidate,
        market: str,
        max_results: int,
        run_id: str,
        bucket_id: str,
    ) -> list[SupplierCandidate]: ...


# --------------------------------------------------------------------------- #
# Helpers shared by both implementations
# --------------------------------------------------------------------------- #
def _build_product(
    discovery: ProductDiscovery,
    *,
    category: str,
    run_id: str,
    bucket_id: str,
    sources: list[EvidenceSource],
    origin: DataOrigin,
    currency: str,
) -> ProductCandidate:
    canonical = discovery.canonical_name or f"{discovery.brand} {discovery.model}".strip()
    return ProductCandidate(
        product_id=stable_id("prd", run_id, bucket_id, canonical.lower()),
        category=category,
        brand=discovery.brand or "Unknown",
        model=discovery.model or canonical,
        canonical_name=canonical or "Unknown product",
        attributes=dict(discovery.attributes or {}),
        normal_market_price=discovery.normal_market_price,
        currency=discovery.currency or currency,
        merchant_or_listing_name=discovery.merchant_or_listing_name,
        listing_url=discovery.listing_url,
        availability=discovery.availability,
        sources=sources,
        data_origin=origin,
        researched_at=utcnow(),
        bucket_id=bucket_id,
    )


ATTRIBUTE_HINT = MONITOR_ATTRIBUTE_HINT  # backwards-compatible alias

PRODUCT_SYSTEM_HINT = (
    "You are a product research assistant. Return real, currently sold computer "
    "monitors that match the request. Use the attribute keys exactly as named. "
    "Prices must be the normal retail price in the requested currency. Never invent "
    "a product that does not exist."
)


# --------------------------------------------------------------------------- #
# Live Linkup
# --------------------------------------------------------------------------- #
class LinkupResearchClient:
    """Thin, defensive wrapper around the official Linkup SDK."""

    name = "linkup"

    def __init__(
        self,
        *,
        api_key: str,
        depth: str = "standard",
        timeout: float = 45.0,
        max_calls: int = 20,
        max_retries: int = 2,
    ) -> None:
        from linkup import LinkupClient  # imported lazily so offline runs need no SDK

        self._client = LinkupClient(api_key=api_key)
        self.depth = depth
        self.timeout = timeout
        self.max_calls = max_calls
        self.max_retries = max_retries
        self.calls = 0
        self.failures = 0

    # -- plumbing ---------------------------------------------------------- #
    # Errors that retrying cannot fix.
    FATAL_ERRORS = (
        "AuthenticationError",
        "InsufficientCreditError",
        "InvalidRequestError",
        "BudgetLimitExceededError",
        "IpNotWhitelistedError",
        "PaymentRequiredError",
    )

    async def _search(
        self,
        *,
        query: str,
        schema: type[Any] | None,
        depth: str | None = None,
        max_results: int | None = None,
    ) -> Any:
        if self.calls >= self.max_calls:
            raise ResearchError(f"Linkup call budget exhausted ({self.max_calls} calls)")

        kwargs: dict[str, Any] = {
            "query": query,
            "depth": depth or self.depth,
            "output_type": "structured" if schema else "searchResults",
            "include_sources": True,
            "timeout": self.timeout,
        }
        if schema is not None:
            kwargs["structured_output_schema"] = json.dumps(schema.model_json_schema())
        if max_results is not None:
            kwargs["max_results"] = max_results

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            try:
                return await asyncio.wait_for(self._invoke(kwargs), timeout=self.timeout + 10)
            except Exception as exc:
                last_error = exc
                self.failures += 1
                name = type(exc).__name__
                logger.warning("Linkup call failed (attempt %s): %s: %s", attempt + 1, name, exc)
                if any(fatal in name for fatal in self.FATAL_ERRORS):
                    raise ResearchError(f"Linkup rejected the request ({name}): {exc}") from exc
                if "NoResult" in name:
                    raise ResearchError(f"Linkup found no results: {exc}") from exc
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2**attempt, 8) * 0.5)
        raise ResearchError(
            f"Linkup search failed after {self.max_retries + 1} attempts: {last_error}"
        )

    async def _invoke(self, kwargs: dict[str, Any]) -> Any:
        """Call whichever async/sync search entry point this SDK version exposes."""
        async_search = getattr(self._client, "async_search", None)
        target = async_search or self._client.search
        supported = self._supported_kwargs(target, kwargs)
        if async_search is not None:
            return await async_search(**supported)
        return await asyncio.to_thread(lambda: self._client.search(**supported))

    @staticmethod
    def _supported_kwargs(target: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            params = inspect.signature(target).parameters
        except (TypeError, ValueError):
            return kwargs
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return kwargs
        return {k: v for k, v in kwargs.items() if k in params}

    # -- response shaping -------------------------------------------------- #
    @staticmethod
    def _payload_and_sources(response: Any) -> tuple[Any, list[EvidenceSource]]:
        now = utcnow()
        payload = getattr(response, "structured_output", None)
        if payload is None:
            payload = getattr(response, "data", response)
        raw_sources = getattr(response, "sources", None) or []
        sources: list[EvidenceSource] = []
        for src in raw_sources:
            url = _attr(src, "url")
            if not url:
                continue
            sources.append(
                EvidenceSource(
                    title=_attr(src, "name") or _attr(src, "title"),
                    url=url,
                    snippet=(_attr(src, "snippet") or _attr(src, "content") or None),
                    retrieved_at=now,
                    provider="linkup",
                )
            )
        return payload, sources[:8]

    # -- public API -------------------------------------------------------- #
    async def search_products(
        self,
        *,
        query: str,
        category: str,
        market: str,
        max_results: int,
        run_id: str,
        bucket_id: str,
        constraints: list[Any] | None = None,
    ) -> list[ProductCandidate]:
        response = await self._search(
            query=query, schema=ProductDiscoveryList, max_results=max_results * 2
        )
        payload, sources = self._payload_and_sources(response)
        discoveries = _coerce_discoveries(payload, ProductDiscoveryList).products
        products = [
            _build_product(
                d,
                category=category,
                run_id=run_id,
                bucket_id=bucket_id,
                sources=sources,
                origin=DataOrigin.WEB_RESEARCH,
                currency="EUR",
            )
            for d in discoveries
            if (d.canonical_name or d.model or d.brand)
        ]
        return products[:max_results]

    async def verify_product(self, product: ProductCandidate, *, run_id: str) -> ProductCandidate:
        """Second pass: confirm the technical specs of a finalist from another source."""
        query = (
            f"Full technical specification and current retail price of the "
            f"{product.canonical_name}, including every specification a buyer would "
            f"compare. {attribute_hint(product.category)}."
        )
        response = await self._search(query=query, schema=ProductDiscoveryList, depth="deep")
        payload, sources = self._payload_and_sources(response)
        discoveries = _coerce_discoveries(payload, ProductDiscoveryList).products
        if not discoveries:
            return product
        best = discoveries[0]
        merged = dict(product.attributes)
        for key, value in (best.attributes or {}).items():
            if value is not None:
                merged.setdefault(key, value)
                if product.attributes.get(key) is None:
                    merged[key] = value
        return product.model_copy(
            update={
                "attributes": merged,
                "sources": product.sources + sources,
                "verified": True,
                "normal_market_price": product.normal_market_price or best.normal_market_price,
            }
        )

    async def search_suppliers(
        self,
        *,
        product: ProductCandidate,
        market: str,
        max_results: int,
        run_id: str,
        bucket_id: str,
    ) -> list[SupplierCandidate]:
        query = (
            f"Companies that could supply the {product.canonical_name} monitor in bulk in "
            f"market {market}: the manufacturer, official distributors, B2B resellers and "
            "large retailers. For each give the company name, type "
            "(manufacturer/distributor/retailer/marketplace_seller) and website."
        )
        response = await self._search(
            query=query, schema=SupplierDiscoveryList, max_results=max_results * 2
        )
        payload, sources = self._payload_and_sources(response)
        discoveries = _coerce_discoveries(payload, SupplierDiscoveryList).suppliers
        suppliers: list[SupplierCandidate] = []
        for d in discoveries[:max_results]:
            if not d.name:
                continue
            suppliers.append(
                SupplierCandidate(
                    supplier_id=stable_id("sup", run_id, d.name.lower()),
                    name=d.name,
                    supplier_type=d.supplier_type or "unknown",
                    website=d.website,
                    market=d.market or market,
                    evidence=sources,
                    data_origin=DataOrigin.WEB_RESEARCH,
                    product_ids=[product.product_id],
                    bucket_id=bucket_id,
                    authorization_claimed=False,
                )
            )
        return suppliers


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _coerce_discoveries(payload: Any, model: type[Any]) -> Any:
    """Linkup may return a pydantic object, a dict, a JSON string or a bare list."""
    if payload is None:
        return model()
    if isinstance(payload, model):
        return payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return model()
    if isinstance(payload, list):
        field = next(iter(model.model_fields))
        payload = {field: payload}
    if isinstance(payload, dict):
        try:
            return model.model_validate(payload)
        except Exception:
            logger.warning("could not validate Linkup payload against %s", model.__name__)
            return model()
    if hasattr(payload, "model_dump"):
        try:
            return model.model_validate(payload.model_dump())
        except Exception:
            return model()
    return model()


# --------------------------------------------------------------------------- #
# Offline fixtures
# --------------------------------------------------------------------------- #
class FixtureResearchClient:
    """Deterministic local catalogue for ``offline=true`` runs.

    Everything it returns is explicitly marked as fixture data. It is *never*
    used as a silent fallback for a failed live search.
    """

    name = "fixtures"

    def __init__(self, fixtures_dir: Path, *, seed: int = 42) -> None:
        self.fixtures_dir = Path(fixtures_dir)
        self.seed = seed
        self.calls = 0
        self._products = self._load_catalogues()
        self._suppliers = self._load("suppliers.json")

    def _load_catalogues(self) -> list[dict[str, Any]]:
        """Every product catalogue in the fixtures directory, one file per category."""
        rows: list[dict[str, Any]] = []
        catalogues = sorted(
            path.name for path in self.fixtures_dir.glob("*.json") if path.name != "suppliers.json"
        )
        if not catalogues:
            raise ResearchError(
                f"no offline catalogue found in {self.fixtures_dir}. Offline mode never "
                "fabricates research data — restore the fixtures or run with --live."
            )
        for filename in catalogues:
            rows.extend(self._load(filename))
        return rows

    def _load(self, filename: str) -> list[dict[str, Any]]:
        path = self.fixtures_dir / filename
        if not path.exists():
            raise ResearchError(
                f"offline catalogue missing: {path}. Offline mode never fabricates "
                "research data — restore the fixture file or run with --live."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _fixture_source(self, filename: str, name: str) -> EvidenceSource:
        return EvidenceSource(
            title=f"Local demo fixture ({filename})",
            url=f"fixture://{filename}#{name}",
            snippet="Offline demo catalogue entry — not live web research.",
            retrieved_at=utcnow(),
            provider="fixture",
        )

    @staticmethod
    def _relevance(row: dict[str, Any], constraints: list[Any] | None) -> float:
        """Rank like a search engine: match the headline numbers, ignore fine print.

        Boolean features (USB-C, FreeSync) are deliberately *not* filtered here — a
        real search returns products that later fail on the details, and the matching
        stage is what rejects them.
        """
        if not constraints:
            return 0.0
        from sye.services.constraints import comparable

        attributes = row.get("attributes", {})
        score = 0.0
        for constraint in constraints:
            key = getattr(constraint, "key", None)
            if key not in ("display.size_in", "display.resolution", "display.refresh_rate_hz"):
                continue
            observed = comparable(key, attributes.get(key))
            expected = comparable(key, constraint.value)
            if observed is None or expected is None:
                continue
            operator = getattr(constraint.operator, "value", constraint.operator)
            if operator == "lte":
                score += 1.0 if observed <= expected else 0.0
            else:
                score += 1.0 if observed >= expected else 0.0
        price = row.get("normal_market_price")
        ceiling = next(
            (
                comparable("price.unit_price", c.value)
                for c in constraints
                if getattr(c, "key", None) == "price.unit_price"
                and getattr(c.operator, "value", c.operator) == "lte"
            ),
            None,
        )
        if price is not None and ceiling:
            score += 0.5 if float(price) <= float(ceiling) * 1.3 else 0.0
        return score

    async def search_products(
        self,
        *,
        query: str,
        category: str,
        market: str,
        max_results: int,
        run_id: str,
        bucket_id: str,
        constraints: list[Any] | None = None,
    ) -> list[ProductCandidate]:
        self.calls += 1
        rows = [r for r in self._products if r.get("category", "monitor") == category]
        # Relevance first, then cheapest — the way a shopping search surfaces results.
        rows = sorted(
            rows,
            key=lambda r: (
                -self._relevance(r, constraints),
                float(r.get("normal_market_price") or 0),
                r.get("canonical_name", ""),
            ),
        )
        products = [
            _build_product(
                ProductDiscovery.model_validate({k: v for k, v in row.items() if k != "category"}),
                category=category,
                run_id=run_id,
                bucket_id=bucket_id,
                sources=[
                    self._fixture_source(
                        f"{row.get('category', 'catalogue')}s.json", row.get("canonical_name", "?")
                    )
                ],
                origin=DataOrigin.SYSTEM,
                currency="EUR",
            )
            for row in rows
        ]
        return products[:max_results]

    async def verify_product(self, product: ProductCandidate, *, run_id: str) -> ProductCandidate:
        self.calls += 1
        return product.model_copy(update={"verified": True})

    async def search_suppliers(
        self,
        *,
        product: ProductCandidate,
        market: str,
        max_results: int,
        run_id: str,
        bucket_id: str,
    ) -> list[SupplierCandidate]:
        self.calls += 1
        rng = random.Random(f"{self.seed}:{product.canonical_name.strip().lower()}")
        rows = sorted(self._suppliers, key=lambda r: r["name"])
        chosen = (
            rows[: max(max_results, 1)]
            if len(rows) <= max_results
            else rng.sample(rows, max_results)
        )
        chosen = sorted(chosen, key=lambda r: r["name"])
        return [
            SupplierCandidate(
                supplier_id=stable_id("sup", run_id, row["name"].lower()),
                name=row["name"],
                supplier_type=row.get("supplier_type", "unknown"),
                website=row.get("website"),
                market=row.get("market", market),
                evidence=[self._fixture_source("suppliers.json", row["name"])],
                data_origin=DataOrigin.SYSTEM,
                product_ids=[product.product_id],
                bucket_id=bucket_id,
                authorization_claimed=False,
            )
            for row in chosen
        ]


def build_research_client(
    settings: Settings, *, offline: bool, seed: int = 42, max_calls: int | None = None
) -> ResearchClient:
    """Offline → fixtures. Online → Linkup (a missing key is an error, not a fallback)."""
    if offline:
        return FixtureResearchClient(settings.fixtures_dir, seed=seed)
    if not settings.has_linkup_key:
        raise ResearchError(
            "LINKUP_API_KEY is not set. Run with --offline to use the local fixture "
            "catalogue instead (fixtures are never substituted silently)."
        )
    return LinkupResearchClient(
        api_key=settings.linkup_api_key or "",
        depth=settings.linkup_default_depth,
        timeout=settings.linkup_timeout_seconds,
        max_calls=max_calls or settings.linkup_max_calls_per_run,
        max_retries=settings.linkup_max_retries,
    )
