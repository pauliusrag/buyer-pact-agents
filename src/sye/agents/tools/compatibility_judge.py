"""Compatibility Judge.

Consulted only for the narrow score band where the deterministic bucketing rule
is genuinely ambiguous. It can never override a hard contradiction — infeasible
groups are rejected before the judge is asked.
"""

from __future__ import annotations

from sye.domain.primitives import SyeModel
from sye.integrations.llm import LLMProvider, LLMUnavailable
from sye.services.bucketing import BucketDraft, JoinAssessment
from sye.services.constraints import describe

SYSTEM_PROMPT = """You decide whether one shopper can be served by the same product as an
existing group of shoppers.

You are given requirements that are already known to be logically compatible. Judge
whether merging is *sensible*: would the group end up buying a product that is wrong or
needlessly expensive for some member? Answer conservatively — say no when the buyers
clearly want different kinds of product."""


class CompatibilityVerdict(SyeModel):
    compatible: bool
    reason: str
    confidence: float = 0.6


class CompatibilityJudge:
    """Adapter matching :data:`sye.services.bucketing.JudgeFn`."""

    def __init__(self, llm: LLMProvider | None) -> None:
        self.llm = llm
        self.calls = 0
        self.verdicts: list[dict[str, object]] = []
        self.warnings: list[str] = []

    async def __call__(self, draft: BucketDraft, intent, assessment: JoinAssessment) -> bool | None:
        if self.llm is None:
            return None
        group = "; ".join(describe(c) for c in draft.hard_constraints())
        candidate = "; ".join(describe(c) for c in intent.hard_constraints())
        user = (
            f"Existing group ({len(draft.intents)} buyers) requires: {group or 'nothing specific'}.\n"
            f"Their price ceiling: {draft.price_ceiling()}.\n"
            f"Candidate buyer {intent.user_id} requires: {candidate or 'nothing specific'}.\n"
            f"Their price ceiling: {intent.max_budget}.\n"
            f"Deterministic compatibility score: {assessment.score:.2f} "
            f"(components: {assessment.components})."
        )
        try:
            self.calls += 1
            verdict = await self.llm.structured(
                schema=CompatibilityVerdict,
                system=SYSTEM_PROMPT,
                user=user,
                task="compatibility_judge",
            )
        except LLMUnavailable as exc:
            self.warnings.append(f"compatibility judge unavailable, kept deterministic: {exc}")
            return None
        self.verdicts.append(
            {
                "user_id": intent.user_id,
                "compatible": verdict.compatible,
                "reason": verdict.reason,
                "confidence": verdict.confidence,
            }
        )
        return verdict.compatible
