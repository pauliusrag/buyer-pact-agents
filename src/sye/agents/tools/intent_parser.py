"""Intent Parser Agent: free text → validated :class:`UserIntent`."""

from __future__ import annotations

from sye.agents.tools import heuristics
from sye.domain.enums import DataOrigin
from sye.domain.ids import stable_id
from sye.domain.models import IntentExtraction, UserIntent, UserRequest
from sye.integrations.llm import LLMProvider, LLMUnavailable

SYSTEM_PROMPT = """You convert one shopper's free-text request into structured requirements.

Rules:
- Use these constraint keys exactly: display.size_in, display.resolution,
  display.refresh_rate_hz, display.panel_type, display.curved, display.hdr,
  connectivity.hdmi, connectivity.displayport, connectivity.usb_c,
  connectivity.usb_c_power_delivery, connectivity.thunderbolt,
  adaptive_sync.freesync, adaptive_sync.gsync, ergonomics.height_adjustable,
  ergonomics.vesa, usage.gaming, usage.office, usage.design.
- Resolutions are canonical strings: 1920x1080, 2560x1440, 3840x2160.
  QHD/1440p -> 2560x1440. UHD/4K -> 3840x2160. FHD/1080p -> 1920x1080.
- A stated spec is normally a minimum: use operator "gte" for size, resolution and
  refresh rate unless the user says "under"/"at most" (then "lte").
- Features are "boolean" constraints with value true.
- importance is "hard" only when the user states a requirement. Preference language
  ("ideally", "would be nice", "prefer") means "soft".
- "MacBook" alone does NOT make Thunderbolt or USB-C mandatory. "one cable" hints at
  connectivity.usb_c_power_delivery but with confidence below 0.6 and soft importance
  unless USB-C charging is stated explicitly.
- "gaming" alone is a soft usage preference, not a refresh-rate requirement.
- Put budgets in max_budget/target_budget, never in constraints.
- Copy the exact words that justify each constraint into source_text and set an honest
  confidence between 0 and 1.
- Never invent a requirement the user did not express."""


async def parse_intent(
    request: UserRequest, *, llm: LLMProvider | None = None
) -> tuple[UserIntent, str, list[str]]:
    """Return ``(intent, engine, warnings)``. Falls back to the rule engine."""
    warnings: list[str] = []
    extraction: IntentExtraction | None = None
    engine = "heuristic"

    if llm is not None:
        try:
            extraction = await llm.structured(
                schema=IntentExtraction,
                system=SYSTEM_PROMPT,
                user=(
                    f"Market: {request.market}. Currency: {request.currency}.\n"
                    f"Request: {request.prompt}"
                ),
                task="parse_intent",
            )
            engine = f"llm:{llm.name}"
        except LLMUnavailable as exc:
            warnings.append(f"intent parsing for {request.user_id} used heuristics: {exc}")

    if extraction is None:
        extraction = heuristics.parse_prompt(request.prompt, currency=request.currency)

    intent = build_intent(request, extraction, engine)
    return intent, engine, warnings


def build_intent(request: UserRequest, extraction: IntentExtraction, engine: str) -> UserIntent:
    constraints = [
        c.model_copy(update={"required_by_user_ids": [request.user_id]})
        for c in extraction.constraints
    ]
    return UserIntent(
        intent_id=stable_id("int", request.request_id, request.user_id),
        user_id=request.user_id,
        request_id=request.request_id,
        category=extraction.category or "monitor",
        category_confidence=extraction.category_confidence,
        constraints=constraints,
        max_budget=extraction.max_budget,
        target_budget=extraction.target_budget,
        currency=request.currency,
        purchase_timing=extraction.purchase_timing,
        quantity=max(1, extraction.quantity),
        named_products=extraction.named_products,
        named_brands=extraction.named_brands,
        excluded_brands=extraction.excluded_brands,
        freeform_preferences=extraction.freeform_preferences,
        clarification_needed=extraction.clarification_needed,
        clarification_questions=extraction.clarification_questions,
        extraction_summary=extraction.extraction_summary,
        extraction_confidence=extraction.extraction_confidence,
        extracted_by=engine,
        data_origin=DataOrigin.LLM_INFERRED if engine.startswith("llm") else DataOrigin.SYSTEM,
    )
