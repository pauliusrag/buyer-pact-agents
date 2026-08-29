"""Constraint algebra.

Everything here is deterministic Python: no LLM decides whether 27 >= 24.

A constraint is a ``key / operator / value`` triple, which keeps the pipeline
category-generic — only :mod:`sye.domain.vocabulary` knows what a monitor is.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sye.domain.enums import ConstraintOperator, EvaluationResult
from sye.domain.models import ConstraintEvaluation, RequirementConstraint
from sye.domain.vocabulary import (
    NUMERIC_KEYS,
    SUBSTITUTIONS,
    human_label,
    resolution_label,
    resolution_pixels,
)

ORDERED_KEYS = NUMERIC_KEYS | {"display.resolution"}


class ConstraintConflict(ValueError):
    """Two hard constraints cannot both be satisfied by a single product."""


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def comparable(key: str, value: Any) -> Any:
    """Project a raw value into something orderable/comparable for ``key``."""
    if value is None:
        return None
    if key == "display.resolution":
        return resolution_pixels(value)
    if key in NUMERIC_KEYS:
        try:
            return float(Decimal(str(value)))
        except Exception:
            return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower()
    return value


def display_value(key: str, value: Any) -> str:
    if key == "display.resolution":
        return resolution_label(value)
    if key == "display.size_in":
        return f'{value}"'
    if key == "display.refresh_rate_hz":
        return f"{value} Hz"
    if key == "price.unit_price":
        return f"{value}"
    if isinstance(value, bool):
        return "required" if value else "not wanted"
    return str(value)


def describe(constraint: RequirementConstraint) -> str:
    """Short human phrase, e.g. ``screen size >= 27"``."""
    label = human_label(constraint.key)
    op = constraint.operator
    value = display_value(constraint.key, constraint.value)
    if op == ConstraintOperator.BOOLEAN:
        return f"{label} {'required' if constraint.value else 'excluded'}"
    if op == ConstraintOperator.GTE:
        return f"{label} ≥ {value}"
    if op == ConstraintOperator.LTE:
        return f"{label} ≤ {value}"
    if op == ConstraintOperator.EQ:
        return f"{label} = {value}"
    if op == ConstraintOperator.IN:
        return f"{label} one of {value}"
    if op == ConstraintOperator.CONTAINS_ALL:
        return f"{label} includes all of {value}"
    return f"{label} includes {value}"


# --------------------------------------------------------------------------- #
# Merging (bucket feasibility)
# --------------------------------------------------------------------------- #
def _merge_pair(a: RequirementConstraint, b: RequirementConstraint) -> RequirementConstraint:
    """Merge two hard constraints that occupy the same slot of the same key.

    Raises :class:`ConstraintConflict` when no product could satisfy both.
    """
    key = a.key
    av, bv = comparable(key, a.value), comparable(key, b.value)
    ops = {a.operator, b.operator}

    users = sorted(set(a.required_by_user_ids) | set(b.required_by_user_ids))
    subs = list(dict.fromkeys([*a.acceptable_substitutions, *b.acceptable_substitutions]))

    def tighter(winner: RequirementConstraint) -> RequirementConstraint:
        return winner.model_copy(
            update={
                "required_by_user_ids": users,
                "acceptable_substitutions": subs,
                "confidence": min(a.confidence, b.confidence),
                "weight": max(a.weight, b.weight),
            }
        )

    if ConstraintOperator.BOOLEAN in ops:
        if bool(a.value) != bool(b.value):
            raise ConstraintConflict(
                f"{human_label(key)}: one member requires it, another excludes it"
            )
        return tighter(a)

    if av is None or bv is None:
        if a.value != b.value:
            raise ConstraintConflict(f"{human_label(key)}: incomparable values")
        return tighter(a)

    if ops == {ConstraintOperator.GTE}:
        return tighter(a if av >= bv else b)
    if ops == {ConstraintOperator.LTE}:
        return tighter(a if av <= bv else b)
    if ops == {ConstraintOperator.EQ}:
        if av != bv:
            raise ConstraintConflict(
                f"{human_label(key)}: {display_value(key, a.value)} vs "
                f"{display_value(key, b.value)} cannot both hold"
            )
        return tighter(a)

    # Set operators: the union of what members ask for.
    merged_value = list(dict.fromkeys([*_as_list(a.value), *_as_list(b.value)]))
    return tighter(a).model_copy(
        update={"operator": ConstraintOperator.CONTAINS_ALL, "value": merged_value}
    )


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def _slot(operator: ConstraintOperator) -> str:
    """``gte`` and ``lte`` live in different slots, so a range stays a range."""
    if operator == ConstraintOperator.GTE:
        return "min"
    if operator == ConstraintOperator.LTE:
        return "max"
    return "value"


def _range_conflicts(merged: dict[tuple[str, str], RequirementConstraint]) -> list[str]:
    """Cross-slot feasibility: min <= max, and an exact value inside the range."""
    conflicts: list[str] = []
    keys = {key for key, _ in merged}
    for key in sorted(keys):
        low = merged.get((key, "min"))
        high = merged.get((key, "max"))
        exact = merged.get((key, "value"))
        low_c = comparable(key, low.value) if low else None
        high_c = comparable(key, high.value) if high else None
        exact_c = (
            comparable(key, exact.value)
            if exact and exact.operator == ConstraintOperator.EQ
            else None
        )
        if low_c is not None and high_c is not None and low_c > high_c:
            conflicts.append(
                f"{human_label(key)}: minimum {display_value(key, low.value)} exceeds "
                f"maximum {display_value(key, high.value)}"
            )
        if exact_c is not None and low_c is not None and exact_c < low_c:
            conflicts.append(
                f"{human_label(key)}: exactly {display_value(key, exact.value)} is below the "
                f"required minimum {display_value(key, low.value)}"
            )
        if exact_c is not None and high_c is not None and exact_c > high_c:
            conflicts.append(
                f"{human_label(key)}: exactly {display_value(key, exact.value)} exceeds the "
                f"required maximum {display_value(key, high.value)}"
            )
    return conflicts


def merge_hard_constraints(
    constraints: list[RequirementConstraint],
) -> tuple[list[RequirementConstraint], list[str]]:
    """Merge hard constraints, returning ``(merged, conflicts)``.

    The merged list is what the *whole* bucket must satisfy: a requirement held by
    a single member still binds every candidate product. A non-empty ``conflicts``
    list means the group is infeasible — no single product could satisfy everyone.
    """
    merged: dict[tuple[str, str], RequirementConstraint] = {}
    conflicts: list[str] = []

    for constraint in constraints:
        slot_key = (constraint.key, _slot(constraint.operator))
        existing = merged.get(slot_key)
        if existing is None:
            merged[slot_key] = constraint.model_copy(deep=True)
            continue
        try:
            merged[slot_key] = _merge_pair(existing, constraint)
        except ConstraintConflict as conflict:
            conflicts.append(str(conflict))

    conflicts.extend(_range_conflicts(merged))
    ordered = sorted(merged.values(), key=lambda c: (c.key, c.operator.value))
    return ordered, conflicts


# --------------------------------------------------------------------------- #
# Evaluation against a product
# --------------------------------------------------------------------------- #
def lookup_attribute(attributes: dict[str, Any], key: str) -> Any:
    """Attribute lookup that understands acceptable substitutions."""
    if key in attributes and attributes[key] is not None:
        return attributes[key]
    for alternative in SUBSTITUTIONS.get(key, []):
        value = attributes.get(alternative)
        if value:
            return value
    return None


def evaluate(constraint: RequirementConstraint, attributes: dict[str, Any]) -> ConstraintEvaluation:
    """Deterministically evaluate one constraint against a product's attributes."""
    observed_raw = lookup_attribute(attributes, constraint.key)
    label = human_label(constraint.key)

    if observed_raw is None:
        return ConstraintEvaluation(
            constraint_key=constraint.key,
            result=EvaluationResult.UNKNOWN,
            expected=_expected(constraint),
            observed=None,
            explanation=f"No verified value for {label}; unknown specs do not pass.",
            importance=constraint.importance,
            required_by_user_ids=constraint.required_by_user_ids,
        )

    expected_c = comparable(constraint.key, constraint.value)
    observed_c = comparable(constraint.key, observed_raw)
    observed_display = observed_label(constraint.key, observed_raw)

    ok: bool
    if constraint.operator == ConstraintOperator.BOOLEAN:
        ok = bool(observed_raw) == bool(constraint.value)
    elif observed_c is None:
        return ConstraintEvaluation(
            constraint_key=constraint.key,
            result=EvaluationResult.UNKNOWN,
            expected=_expected(constraint),
            observed=str(observed_raw),
            explanation=f"{label} value {observed_raw!r} could not be interpreted.",
            importance=constraint.importance,
            required_by_user_ids=constraint.required_by_user_ids,
        )
    elif constraint.operator == ConstraintOperator.GTE:
        ok = observed_c >= expected_c
    elif constraint.operator == ConstraintOperator.LTE:
        ok = observed_c <= expected_c
    elif constraint.operator == ConstraintOperator.EQ:
        ok = observed_c == expected_c
    elif constraint.operator == ConstraintOperator.IN:
        ok = observed_c in [comparable(constraint.key, v) for v in _as_list(constraint.value)]
    elif constraint.operator == ConstraintOperator.CONTAINS_ALL:
        have = {comparable(constraint.key, v) for v in _as_list(observed_raw)}
        ok = all(comparable(constraint.key, v) in have for v in _as_list(constraint.value))
    else:  # CONTAINS_ANY
        have = {comparable(constraint.key, v) for v in _as_list(observed_raw)}
        ok = any(comparable(constraint.key, v) in have for v in _as_list(constraint.value))

    verb = "meets" if ok else "does not meet"
    return ConstraintEvaluation(
        constraint_key=constraint.key,
        result=EvaluationResult.PASS if ok else EvaluationResult.FAIL,
        expected=_expected(constraint),
        observed=observed_display,
        explanation=f"{label} {observed_display} {verb} {describe(constraint)}.",
        importance=constraint.importance,
        required_by_user_ids=constraint.required_by_user_ids,
    )


def _expected(constraint: RequirementConstraint) -> Any:
    return describe(constraint)


def observed_label(key: str, value: Any) -> str:
    """How an observed product attribute reads in an explanation."""
    if isinstance(value, bool):
        return "present" if value else "absent"
    if key == "display.resolution":
        return resolution_label(value)
    return display_value(key, value)
