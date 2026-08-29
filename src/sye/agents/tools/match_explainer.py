"""Match Explainer.

The verdict is already decided deterministically. The LLM only turns the
evaluation table into one sentence a shopper can read; if it is unavailable the
deterministic sentence stands.
"""

from __future__ import annotations

from sye.domain.models import MatchExplanation, ProductCandidate, ProductMatch
from sye.integrations.llm import LLMProvider, LLMUnavailable

SYSTEM_PROMPT = """You explain, in one or two plain sentences, why a product does or does
not fit a group of buyers.

You are given a decision that has already been made deterministically. Never change the
verdict, never invent specifications, never add marketing language. Mention the specific
requirement that decided it."""


async def explain_match(
    match: ProductMatch,
    product: ProductCandidate,
    *,
    llm: LLMProvider | None,
    bucket_label: str,
) -> tuple[ProductMatch, list[str]]:
    if llm is None:
        return match, []

    rows = "\n".join(
        f"- {e.constraint_key}: {e.result.value} (expected {e.expected}, observed {e.observed})"
        for e in match.hard_constraint_results
    )
    user = (
        f"Buyer group: {bucket_label}\n"
        f"Product: {product.canonical_name} at {product.normal_market_price} "
        f"{product.currency}\n"
        f"Verdict: {match.classification.value}\n"
        f"Constraint results:\n{rows}\n"
        f"Deterministic summary: {match.explanation}"
    )
    try:
        copy = await llm.structured(
            schema=MatchExplanation, system=SYSTEM_PROMPT, user=user, task="match_explainer"
        )
    except LLMUnavailable as exc:
        return match, [
            f"match explanation for {product.canonical_name} stayed deterministic: {exc}"
        ]

    return (
        match.model_copy(
            update={"explanation": copy.explanation.strip(), "explained_by": f"llm:{llm.name}"}
        ),
        [],
    )
