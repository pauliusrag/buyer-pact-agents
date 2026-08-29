"""Deterministic natural-language extraction for the monitor category.

This is the fallback (and offline) implementation of the intent parser. It is a
rule engine, not a model: it is fast, free, fully reproducible, and it keeps the
demo runnable without any API key. Each constraint it produces carries the source
text it came from and a calibrated confidence.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sye.domain.enums import ConstraintOperator, Importance
from sye.domain.models import IntentExtraction, RequirementConstraint
from sye.domain.vocabulary import RESOLUTIONS, normalize_resolution

BRANDS = (
    "dell",
    "lg",
    "samsung",
    "benq",
    "aoc",
    "asus",
    "acer",
    "hp",
    "lenovo",
    "philips",
    "msi",
    "gigabyte",
    "iiyama",
    "viewsonic",
    "apple",
    "eizo",
    "xiaomi",
    "huawei",
    "koorui",
    "hisense",
)

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "monitor": ("monitor", "display", "screen", "curved screen"),
    "laptop": ("laptop", "notebook", "macbook air", "macbook pro"),
    "keyboard": ("keyboard",),
    "headphones": ("headphones", "headset", "earbuds"),
    "phone": ("smartphone", "iphone", "android phone"),
    "chair": ("chair",),
}

MIN_MARKERS = (
    "at least",
    "minimum",
    "min ",
    "or larger",
    "or bigger",
    "or more",
    "or better",
    "and up",
    "plus",
    "over",
    "larger than",
    "bigger than",
    "more than",
    "from ",
)
MAX_MARKERS = (
    "under",
    "less than",
    "smaller than",
    "at most",
    "up to",
    "no more than",
    "below",
    "max",
)
FUZZY_MARKERS = ("-ish", "around", "about", "roughly", "approximately", "or so")
"""Hedged numbers: still a requirement, just less precisely stated."""

PREFERENCE_MARKERS = (
    "would be ideal",
    "would be nice",
    "would like",
    "would love",
    "prefer",
    "preferably",
    "ideally",
    "bonus",
    "nice to have",
    "if possible",
    "doesn't have to",
    "not essential",
)
"""Language that turns a requirement into a preference."""

_AMOUNT = (
    r"(\d{2,5}(?:[.,]\d{3})?(?:[.,]\d{1,2})?)"
    r'(?!\s*(?:inch|inches|"|”|hz|hertz|cm|mm|kg|bit|k\b|p\b))'
)
"""A number that is not immediately a screen size, refresh rate or resolution."""

BUDGET_MAX_PATTERNS = (
    rf"(?:under|below|less than|no more than|max(?:imum)?|at most|up to|cap(?:ped)? at|"
    rf"not more than|(?:hard )?limit(?: of| is)?)\s*(?:€|eur\s?|euros?\s?)?\s?{_AMOUNT}",
    rf"(?:budget(?: is| of)?|spend|pay)\s*(?:a )?(?:max(?:imum)?|up to|of|around)?\s*"
    rf"(?:€|eur\s?|euros?\s?)?\s?{_AMOUNT}",
    rf"(?:€|eur\s?)\s?{_AMOUNT}\s*(?:max(?:imum)?|or less|tops)",
    rf"{_AMOUNT}\s*(?:€|eur|euros?)\s*(?:max(?:imum)?|or less|tops)",
)
BUDGET_TARGET_PATTERNS = (
    rf"(?:around|about|roughly|approximately|circa|~|ideally|somewhere around)\s*"
    rf"(?:€|eur\s?|euros?\s?)?\s?{_AMOUNT}",
    rf"{_AMOUNT}\s*(?:€|eur|euros?)?\s*(?:would be perfect|is perfect|ish)",
)
TARGET_TO_MAX_FACTOR = Decimal("1.15")


def _to_decimal(raw: str) -> Decimal | None:
    cleaned = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}[.,]\d{3}", cleaned):  # 1.200 / 1,200 → thousands
        cleaned = cleaned.replace(".", "").replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _window(text: str, index: int, before: int = 40, after: int = 25) -> str:
    return text[max(0, index - before) : index + after]


def _importance_for(window: str, default: Importance = Importance.HARD) -> Importance:
    """Preference language demotes a requirement; hedged numbers do not."""
    if any(marker in window for marker in PREFERENCE_MARKERS):
        return Importance.SOFT
    return default


def _is_hedged(window: str) -> bool:
    return any(marker in window for marker in FUZZY_MARKERS)


AFTER_MIN_MARKERS = ("or better", "or more", "or higher", "or above", "+", "and up", "or larger")
AFTER_MAX_MARKERS = ("or less", "or lower", "or below", "or smaller", "max")


def _operator_for(text: str, start: int, end: int) -> ConstraintOperator:
    """Decide gte/lte from the words immediately around the number.

    Scoped tightly on purpose: in "165hz or better, FreeSync, max 450" the ``max``
    belongs to the budget, not to the refresh rate.
    """
    before = text[max(0, start - 22) : start]
    after = text[end : end + 12]
    if any(marker in before for marker in MIN_MARKERS) or any(
        marker in after for marker in AFTER_MIN_MARKERS
    ):
        return ConstraintOperator.GTE
    if any(marker in before for marker in MAX_MARKERS) or any(
        marker in after for marker in AFTER_MAX_MARKERS
    ):
        return ConstraintOperator.LTE
    return ConstraintOperator.GTE


MONITOR_SIGNALS = re.compile(
    r"\bmonitor\b|\bdisplay\b|\bscreen\b|\d{2}\s*(?:-|\s)?(?:inch|inches|\")"
    r"|1440p|1080p|2160p|\b4k\b|\bqhd\b|\buhd\b|\bfhd\b|\d{2,3}\s*hz"
)
"""Evidence that the request is about a display, whatever else it mentions."""


def detect_category(text: str) -> tuple[str, float]:
    """Detect the product category.

    "I work from a laptop and want 27 inches and 1440p" is a *monitor* request:
    display signals outrank context words like laptop or MacBook.
    """
    scores: dict[str, int] = {}
    for category, hints in CATEGORY_HINTS.items():
        hits = sum(1 for hint in hints if hint in text)
        if hits:
            scores[category] = hits

    monitor_signals = len(MONITOR_SIGNALS.findall(text))
    if monitor_signals:
        return "monitor", min(0.99, 0.7 + 0.07 * monitor_signals)
    if not scores:
        return "monitor", 0.4
    if "monitor" in scores:
        return "monitor", min(0.99, 0.75 + 0.08 * scores["monitor"])
    best = max(scores.items(), key=lambda kv: (kv[1], kv[0]))
    return best[0], min(0.9, 0.6 + 0.1 * best[1])


def _constraint(
    key: str,
    operator: ConstraintOperator,
    value: Any,
    *,
    importance: Importance,
    source_text: str,
    confidence: float,
    unit: str | None = None,
    substitutions: list[Any] | None = None,
    weight: float = 1.0,
) -> RequirementConstraint:
    return RequirementConstraint(
        key=key,
        operator=operator,
        value=value,
        unit=unit,
        importance=importance,
        weight=weight,
        acceptable_substitutions=substitutions or [],
        source_text=source_text.strip(),
        confidence=confidence,
    )


def parse_prompt(prompt: str, *, currency: str = "EUR") -> IntentExtraction:
    """Extract a structured intent from a free-text request."""
    text = prompt.lower()
    constraints: list[RequirementConstraint] = []
    preferences: list[str] = []
    questions: list[str] = []

    category, category_confidence = detect_category(text)

    # -- size ------------------------------------------------------------- #
    seen_size_operators: set[ConstraintOperator] = set()
    for match in re.finditer(r'(\d{2}(?:[.,]\d)?)\s*(?:-|\s)?(?:inch|inches|"|”|in\b|-ish)', text):
        value = _to_decimal(match.group(1))
        if value is None or not (10 <= value <= 60):
            continue
        window = _window(text, match.start(), before=20, after=30)
        operator = _operator_for(text, match.start(), match.end())
        if operator in seen_size_operators:
            continue
        seen_size_operators.add(operator)
        importance = _importance_for(window)
        confidence = 0.72 if _is_hedged(window) else 0.95
        constraints.append(
            _constraint(
                "display.size_in",
                operator,
                float(value),
                importance=importance,
                source_text=window,
                confidence=confidence,
                unit="inch",
            )
        )

    # -- resolution -------------------------------------------------------- #
    for token in sorted(RESOLUTIONS, key=len, reverse=True):
        pattern = rf"\b{re.escape(token)}\b" if token[0].isalpha() else re.escape(token)
        match = re.search(pattern, text)
        if not match:
            continue
        canonical = normalize_resolution(token)
        window = _window(text, match.start(), before=20, after=30)
        constraints.append(
            _constraint(
                "display.resolution",
                ConstraintOperator.GTE,
                canonical,
                importance=_importance_for(window),
                source_text=window,
                confidence=0.93,
            )
        )
        break

    # -- refresh rate ------------------------------------------------------ #
    match = re.search(r"(\d{2,3})\s*hz", text)
    if match:
        value = int(match.group(1))
        window = _window(text, match.start(), before=20, after=30)
        operator = _operator_for(text, match.start(), match.end())
        constraints.append(
            _constraint(
                "display.refresh_rate_hz",
                operator,
                value,
                importance=_importance_for(window),
                source_text=window,
                confidence=0.94,
                unit="Hz",
            )
        )

    # -- connectivity ------------------------------------------------------ #
    usb_c_matches = list(re.finditer(r"usb[- ]?c|type[- ]?c", text))
    usb_c = usb_c_matches[0] if usb_c_matches else None
    one_cable = re.search(
        r"(one|single) cable|charge[s]? (my|the) (laptop|macbook)|powers? my", text
    )
    thunderbolt = re.search(r"thunderbolt", text)

    if usb_c:
        windows = [_window(text, m.start(), before=25, after=35) for m in usb_c_matches]
        window = next(
            (w for w in windows if re.search(r"charg|power delivery|\bpd\b|powers", w)),
            windows[0],
        )
        charging = bool(
            any(
                re.search(r"charg|power delivery|\bpd\b|one cable|single cable|powers", w)
                for w in windows
            )
            or one_cable
        )
        key = "connectivity.usb_c_power_delivery" if charging else "connectivity.usb_c"
        constraints.append(
            _constraint(
                key,
                ConstraintOperator.BOOLEAN,
                True,
                importance=_importance_for(window),
                source_text=window,
                confidence=0.9 if charging else 0.85,
                substitutions=["thunderbolt"] if charging else [],
            )
        )
    elif one_cable:
        # "one cable for my MacBook" suggests USB-C power delivery, but only weakly.
        window = _window(text, one_cable.start(), before=20, after=30)
        constraints.append(
            _constraint(
                "connectivity.usb_c_power_delivery",
                ConstraintOperator.BOOLEAN,
                True,
                importance=Importance.SOFT,
                source_text=window,
                confidence=0.55,
                substitutions=["thunderbolt"],
            )
        )
        questions.append("Is single-cable USB-C charging a hard requirement, or just preferred?")

    if thunderbolt:
        window = _window(text, thunderbolt.start(), before=20, after=30)
        constraints.append(
            _constraint(
                "connectivity.thunderbolt",
                ConstraintOperator.BOOLEAN,
                True,
                importance=_importance_for(window),
                source_text=window,
                confidence=0.88,
            )
        )

    # -- adaptive sync ----------------------------------------------------- #
    for pattern, key in (
        (r"freesync", "adaptive_sync.freesync"),
        (r"g-?sync", "adaptive_sync.gsync"),
    ):
        match = re.search(pattern, text)
        if match:
            window = _window(text, match.start(), before=20, after=30)
            constraints.append(
                _constraint(
                    key,
                    ConstraintOperator.BOOLEAN,
                    True,
                    importance=_importance_for(window),
                    source_text=window,
                    confidence=0.9,
                    substitutions=["adaptive sync"],
                )
            )

    # -- soft attributes --------------------------------------------------- #
    soft_flags = (
        (r"\bhdr\b", "display.hdr", 0.85),
        (r"curved", "display.curved", 0.85),
        (r"height[- ]adjust|adjustable stand|ergonom", "ergonomics.height_adjustable", 0.8),
        (r"vesa", "ergonomics.vesa", 0.85),
    )
    for pattern, key, confidence in soft_flags:
        match = re.search(pattern, text)
        if match:
            window = _window(text, match.start(), before=20, after=30)
            constraints.append(
                _constraint(
                    key,
                    ConstraintOperator.BOOLEAN,
                    True,
                    importance=_importance_for(window, default=Importance.SOFT),
                    source_text=window,
                    confidence=confidence,
                    weight=0.6,
                )
            )

    match = re.search(r"\b(ips|va|oled|qd-oled|mini-?led|tn)\b panel|\b(ips|oled|va)\b", text)
    if match:
        panel = (match.group(1) or match.group(2) or "").lower()
        if panel:
            constraints.append(
                _constraint(
                    "display.panel_type",
                    ConstraintOperator.EQ,
                    panel,
                    importance=Importance.SOFT,
                    source_text=_window(text, match.start()),
                    confidence=0.75,
                    weight=0.5,
                )
            )

    # -- usage ------------------------------------------------------------- #
    usage_map = (
        (r"gaming|gamer|fps|esports|play games", "usage.gaming"),
        (
            r"work|office|coding|programming|spreadsheet|documents|productivity|home office",
            "usage.office",
        ),
        (r"design|photo|video edit|colou?r accurate|creative", "usage.design"),
    )
    for pattern, key in usage_map:
        match = re.search(pattern, text)
        if match:
            constraints.append(
                _constraint(
                    key,
                    ConstraintOperator.BOOLEAN,
                    True,
                    importance=Importance.SOFT,
                    source_text=_window(text, match.start()),
                    confidence=0.8,
                    weight=0.5,
                )
            )

    # -- budget ------------------------------------------------------------ #
    max_budget: Decimal | None = None
    target_budget: Decimal | None = None
    budget_source = ""
    for pattern in BUDGET_MAX_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = _to_decimal(match.group(1))
            if value and value >= 20:
                max_budget = value
                budget_source = _window(text, match.start())
                break
    for pattern in BUDGET_TARGET_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = _to_decimal(match.group(1))
            if value and value >= 20:
                target_budget = value
                budget_source = budget_source or _window(text, match.start())
                break

    if max_budget is None and target_budget is not None:
        max_budget = (target_budget * TARGET_TO_MAX_FACTOR).quantize(Decimal("1"))
        questions.append(
            f"Is {max_budget} {currency} an acceptable maximum, or is {target_budget} "
            f"{currency} a hard limit?"
        )
    if max_budget is not None and target_budget is None:
        target_budget = (max_budget * Decimal("0.9")).quantize(Decimal("0.01"))

    # -- brands ------------------------------------------------------------ #
    named_brands: list[str] = []
    excluded_brands: list[str] = []
    for brand in BRANDS:
        match = re.search(rf"\b{brand}\b", text)
        if not match:
            continue
        window = _window(text, match.start(), before=30, after=10)
        if re.search(r"\b(no|not|avoid|except|anything but)\b", window):
            excluded_brands.append(brand.upper() if len(brand) <= 3 else brand.title())
        elif brand in ("apple",) and "macbook" in text:
            continue  # "MacBook" is context, not a brand requirement
        else:
            named_brands.append(brand.upper() if len(brand) <= 3 else brand.title())

    if re.search(r"brand does ?n[o']?t matter|any brand|no brand preference", text):
        named_brands = []
        preferences.append("brand-agnostic")

    # -- misc -------------------------------------------------------------- #
    quantity = 1
    match = re.search(r"\b(two|three|four|2|3|4)\s+(monitors|displays|screens)\b", text)
    if match:
        quantity = {"two": 2, "three": 3, "four": 4}.get(match.group(1), 0) or int(match.group(1))

    timing = None
    for pattern, label in (
        (r"asap|urgent|right away|this week", "asap"),
        (r"this month|within a month|next few weeks", "within_a_month"),
        (r"no rush|whenever|not urgent", "flexible"),
    ):
        if re.search(pattern, text):
            timing = label
            break

    for pattern, note in (
        (r"macbook", "uses a MacBook"),
        (r"laptop", "connects a laptop"),
        (r"desk", "desk setup"),
        (r"dual|two screens side", "may add a second screen"),
    ):
        if re.search(pattern, text):
            preferences.append(note)

    hard_count = sum(1 for c in constraints if c.importance == Importance.HARD)
    clarification_needed = bool(questions) or hard_count == 0
    if hard_count == 0:
        questions.append("Which specifications are non-negotiable for you?")

    confidence = round(
        min(
            0.95,
            0.45
            + 0.08 * hard_count
            + (0.1 if max_budget is not None else 0.0)
            + (0.05 if category == "monitor" else 0.0),
        ),
        3,
    )

    summary = _summarize(category, constraints, max_budget, target_budget, currency)

    return IntentExtraction(
        category=category,
        category_confidence=round(category_confidence, 3),
        constraints=constraints,
        max_budget=max_budget,
        target_budget=target_budget,
        purchase_timing=timing,
        quantity=quantity,
        named_products=[],
        named_brands=sorted(set(named_brands)),
        excluded_brands=sorted(set(excluded_brands)),
        freeform_preferences=sorted(set(preferences)),
        clarification_needed=clarification_needed,
        clarification_questions=questions,
        extraction_summary=summary,
        extraction_confidence=confidence,
    )


def _summarize(
    category: str,
    constraints: list[RequirementConstraint],
    max_budget: Decimal | None,
    target_budget: Decimal | None,
    currency: str,
) -> str:
    from sye.services.constraints import describe  # local import avoids a cycle

    hard = [describe(c) for c in constraints if c.importance == Importance.HARD]
    soft = [describe(c) for c in constraints if c.importance == Importance.SOFT]
    parts = [f"Wants a {category}."]
    if hard:
        parts.append("Must have: " + ", ".join(hard) + ".")
    if soft:
        parts.append("Prefers: " + ", ".join(soft) + ".")
    if max_budget is not None:
        target = f" (target {target_budget})" if target_budget is not None else ""
        parts.append(f"Budget up to {max_budget} {currency}{target}.")
    return " ".join(parts)
