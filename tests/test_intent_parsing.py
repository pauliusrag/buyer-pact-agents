"""Intent parsing: the rule engine and the LLM path (mocked structured output)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sye.agents.tools.heuristics import parse_prompt
from sye.agents.tools.intent_parser import parse_intent
from sye.domain.enums import ConstraintOperator, DataOrigin, Importance
from sye.domain.models import IntentExtraction, RequirementConstraint
from sye.integrations.llm import ScriptedProvider
from tests.conftest import make_request


def keys(extraction) -> dict[str, RequirementConstraint]:
    return {c.key: c for c in extraction.constraints}


def test_size_and_resolution_are_normalised():
    extraction = parse_prompt("I need a 27 inch monitor, 4k please, under €400")
    parsed = keys(extraction)
    assert parsed["display.size_in"].value == 27.0
    assert parsed["display.size_in"].operator == ConstraintOperator.GTE
    assert parsed["display.resolution"].value == "3840x2160"
    assert extraction.max_budget == Decimal("400")


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("I want 1440p", "2560x1440"),
        ("QHD is enough", "2560x1440"),
        ("must be UHD", "3840x2160"),
        ("1080p is fine", "1920x1080"),
        ("2560x1440 exactly", "2560x1440"),
    ],
)
def test_resolution_vocabulary(prompt, expected):
    assert keys(parse_prompt(prompt))["display.resolution"].value == expected


def test_refresh_rate_and_freesync():
    parsed = keys(parse_prompt("gaming monitor, 144hz minimum, FreeSync, max €450"))
    assert parsed["display.refresh_rate_hz"].value == 144
    assert parsed["display.refresh_rate_hz"].operator == ConstraintOperator.GTE
    assert parsed["adaptive_sync.freesync"].importance == Importance.HARD


def test_usb_c_charging_is_hard_when_explicit():
    parsed = keys(parse_prompt("27 inch monitor that charges my laptop over USB-C, max €350"))
    pd = parsed["connectivity.usb_c_power_delivery"]
    assert pd.importance == Importance.HARD
    assert pd.value is True
    assert "thunderbolt" in pd.acceptable_substitutions


def test_macbook_alone_does_not_require_thunderbolt():
    extraction = parse_prompt("A monitor for my MacBook, at least 27 inch, under €300")
    parsed = keys(extraction)
    assert "connectivity.thunderbolt" not in parsed
    assert "uses a MacBook" in extraction.freeform_preferences


def test_one_cable_hint_is_soft_and_low_confidence():
    parsed = keys(parse_prompt("I want one cable to my MacBook on a 27 inch screen, under €300"))
    pd = parsed["connectivity.usb_c_power_delivery"]
    assert pd.importance == Importance.SOFT
    assert pd.confidence < 0.6


def test_gaming_alone_is_a_soft_preference():
    parsed = keys(parse_prompt("A 27 inch monitor for gaming, around €300"))
    assert parsed["usage.gaming"].importance == Importance.SOFT
    assert "display.refresh_rate_hz" not in parsed


def test_budget_variants():
    assert parse_prompt("under 200 euros").max_budget == Decimal("200")
    assert parse_prompt("budget is €700").max_budget == Decimal("700")
    assert parse_prompt("max €320").max_budget == Decimal("320")
    around = parse_prompt("around €250 would be perfect")
    assert around.target_budget == Decimal("250")
    assert around.max_budget > around.target_budget  # conservative assumption, flagged
    assert around.clarification_questions


def test_screen_size_is_not_mistaken_for_a_budget():
    extraction = parse_prompt("something small, under 24 inch, max 75hz, budget 120 euro")
    assert extraction.max_budget == Decimal("120")
    parsed = keys(extraction)
    assert parsed["display.size_in"].operator == ConstraintOperator.LTE
    assert parsed["display.refresh_rate_hz"].operator == ConstraintOperator.LTE


def test_other_categories_are_detected():
    assert parse_prompt("I want a mechanical keyboard under €120").category == "keyboard"


def test_source_text_and_confidence_are_preserved():
    extraction = parse_prompt("At least 27 inches please, QHD, max €300")
    for c in extraction.constraints:
        assert c.source_text
        assert 0.0 < c.confidence <= 1.0


async def test_llm_path_is_used_when_available():
    """The LLM returns structured output; nothing is parsed from prose."""
    scripted = ScriptedProvider(
        {
            "parse_intent": IntentExtraction(
                category="monitor",
                category_confidence=0.99,
                constraints=[
                    RequirementConstraint(
                        key="display.size_in",
                        operator=ConstraintOperator.GTE,
                        value=32,
                        importance=Importance.HARD,
                        confidence=0.97,
                        source_text="32 inch",
                    )
                ],
                max_budget=Decimal("500"),
                extraction_summary="LLM summary",
                extraction_confidence=0.95,
            )
        }
    )
    request = make_request("user_llm", "32 inch monitor, up to 500")
    intent, engine, warnings = await parse_intent(request, llm=scripted)

    assert engine == "llm:scripted"
    assert warnings == []
    assert intent.data_origin == DataOrigin.LLM_INFERRED
    assert intent.user_id == "user_llm"
    assert intent.constraints[0].required_by_user_ids == ["user_llm"]
    assert intent.max_budget == Decimal("500")
    assert scripted.call_count == 1


async def test_llm_failure_falls_back_to_heuristics_with_a_warning():
    scripted = ScriptedProvider({})  # no scripted response -> LLMUnavailable
    request = make_request("user_fb", "27 inch 1440p monitor with USB-C charging, max €300")
    intent, engine, warnings = await parse_intent(request, llm=scripted)

    assert engine == "heuristic"
    assert warnings and "heuristics" in warnings[0]
    assert {c.key for c in intent.hard_constraints()} >= {
        "display.size_in",
        "display.resolution",
        "connectivity.usb_c_power_delivery",
    }


# --------------------------------------------------------------------------- #
# Wearables — a second category, to prove the architecture is category-generic
# --------------------------------------------------------------------------- #
def test_wearable_requests_are_detected_and_parsed():
    extraction = parse_prompt(
        "I want a smart ring that tracks my sleep and HRV. No monthly subscription. Under €300."
    )
    assert extraction.category == "wearable"
    parsed = keys(extraction)
    assert parsed["wearable.form_factor"].value == "ring"
    assert parsed["sensors.sleep_tracking"].value is True
    assert parsed["sensors.heart_rate"].value is True
    assert parsed["wearable.subscription_required"].value is False
    assert parsed["wearable.subscription_required"].importance == Importance.HARD
    assert extraction.max_budget == Decimal("300")


def test_battery_life_in_weeks_is_normalised_to_days():
    parsed = keys(parse_prompt("A sleep tracking ring with at least a week of battery, max €320"))
    assert parsed["wearable.battery_days"].value == 7
    assert parsed["wearable.battery_days"].operator == ConstraintOperator.GTE


def test_phone_compatibility_and_water_resistance():
    parsed = keys(
        parse_prompt("Fitness band with GPS and heart rate for my iPhone, waterproof, around €200")
    )
    assert parsed["compat.ios"].value is True
    assert parsed["sensors.gps"].value is True
    assert parsed["wearable.water_resistance_atm"].importance == Importance.SOFT


def test_monitor_requests_are_unaffected_by_the_wearable_vocabulary():
    extraction = parse_prompt("27 inch 1440p monitor with USB-C charging, under €320")
    assert extraction.category == "monitor"
    assert "wearable.form_factor" not in keys(extraction)
