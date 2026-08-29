"""Lightweight normalisation vocabulary for the monitor category.

The architecture is category-generic: constraints are ``key/operator/value``
triples, and this module is the only monitor-specific piece. Adding phones or
appliances later means adding another vocabulary module, not new agents.
"""

from __future__ import annotations

import re
from typing import Any

CATEGORY_MONITOR = "monitor"
CATEGORY_WEARABLE = "wearable"

# Canonical attribute keys ---------------------------------------------------
ATTRIBUTE_KEYS: tuple[str, ...] = (
    "display.size_in",
    "display.resolution",
    "display.refresh_rate_hz",
    "display.panel_type",
    "display.curved",
    "display.hdr",
    "connectivity.hdmi",
    "connectivity.displayport",
    "connectivity.usb_c",
    "connectivity.usb_c_power_delivery",
    "connectivity.thunderbolt",
    "adaptive_sync.freesync",
    "adaptive_sync.gsync",
    "ergonomics.height_adjustable",
    "ergonomics.vesa",
    "usage.gaming",
    "usage.office",
    "usage.design",
    "price.unit_price",
)

WEARABLE_ATTRIBUTE_KEYS: tuple[str, ...] = (
    "wearable.form_factor",
    "wearable.battery_days",
    "wearable.subscription_required",
    "wearable.water_resistance_atm",
    "wearable.weight_g",
    "sensors.heart_rate",
    "sensors.spo2",
    "sensors.temperature",
    "sensors.ecg",
    "sensors.sleep_tracking",
    "sensors.activity_tracking",
    "sensors.gps",
    "connectivity.bluetooth",
    "connectivity.nfc",
    "compat.ios",
    "compat.android",
    "material.titanium",
    "price.unit_price",
)

FORM_FACTORS = ("ring", "watch", "band", "clip", "patch")

BOOLEAN_KEYS = {
    k
    for k in (*ATTRIBUTE_KEYS, *WEARABLE_ATTRIBUTE_KEYS)
    if k.startswith(
        (
            "connectivity.",
            "adaptive_sync.",
            "ergonomics.",
            "usage.",
            "sensors.",
            "compat.",
            "material.",
        )
    )
} | {
    "display.curved",
    "display.hdr",
    "wearable.subscription_required",
}

NUMERIC_KEYS = {
    "display.size_in",
    "display.refresh_rate_hz",
    "price.unit_price",
    "wearable.battery_days",
    "wearable.water_resistance_atm",
    "wearable.weight_g",
}

# Resolution normalisation ---------------------------------------------------
RESOLUTIONS: dict[str, str] = {
    "fhd": "1920x1080",
    "full hd": "1920x1080",
    "1080p": "1920x1080",
    "1920x1080": "1920x1080",
    "qhd": "2560x1440",
    "wqhd": "2560x1440",
    "1440p": "2560x1440",
    "2k": "2560x1440",
    "2560x1440": "2560x1440",
    "uhd": "3840x2160",
    "4k": "3840x2160",
    "2160p": "3840x2160",
    "3840x2160": "3840x2160",
    "5k": "5120x2880",
    "5120x2880": "5120x2880",
    "8k": "7680x4320",
    "7680x4320": "7680x4320",
}

RESOLUTION_LABELS: dict[str, str] = {
    "1920x1080": "FHD / 1080p",
    "2560x1440": "QHD / 1440p",
    "3840x2160": "UHD / 4K",
    "5120x2880": "5K",
    "7680x4320": "8K",
}

# Substitutions that satisfy a requirement even though the literal token differs.
SUBSTITUTIONS: dict[str, list[str]] = {
    "sensors.activity_tracking": ["sensors.heart_rate"],
    "connectivity.usb_c_power_delivery": ["connectivity.thunderbolt"],
    "connectivity.thunderbolt": [],
    "adaptive_sync.freesync": ["adaptive_sync.gsync"],
    "adaptive_sync.gsync": ["adaptive_sync.freesync"],
}

PANEL_TYPES = ("ips", "va", "tn", "oled", "qd-oled", "mini-led")

HUMAN_KEY_LABELS: dict[str, str] = {
    # wearables
    "wearable.form_factor": "form factor",
    "wearable.battery_days": "battery life",
    "wearable.subscription_required": "subscription",
    "wearable.water_resistance_atm": "water resistance",
    "wearable.weight_g": "weight",
    "sensors.heart_rate": "heart-rate sensor",
    "sensors.spo2": "blood-oxygen (SpO2) sensor",
    "sensors.temperature": "temperature sensor",
    "sensors.ecg": "ECG",
    "sensors.sleep_tracking": "sleep tracking",
    "sensors.activity_tracking": "activity tracking",
    "sensors.gps": "built-in GPS",
    "connectivity.bluetooth": "Bluetooth",
    "connectivity.nfc": "NFC payments",
    "compat.ios": "iPhone compatibility",
    "compat.android": "Android compatibility",
    "material.titanium": "titanium body",
    # monitors
    "display.size_in": "screen size",
    "display.resolution": "resolution",
    "display.refresh_rate_hz": "refresh rate",
    "display.panel_type": "panel type",
    "display.curved": "curved screen",
    "display.hdr": "HDR",
    "connectivity.hdmi": "HDMI",
    "connectivity.displayport": "DisplayPort",
    "connectivity.usb_c": "USB-C",
    "connectivity.usb_c_power_delivery": "USB-C charging (power delivery)",
    "connectivity.thunderbolt": "Thunderbolt",
    "adaptive_sync.freesync": "FreeSync",
    "adaptive_sync.gsync": "G-Sync",
    "ergonomics.height_adjustable": "height adjustable stand",
    "ergonomics.vesa": "VESA mount",
    "usage.gaming": "gaming use",
    "usage.office": "office/productivity use",
    "usage.design": "design/colour work",
    "price.unit_price": "unit price",
}


def normalize_resolution(raw: Any) -> str | None:
    """Map ``4K`` / ``1440p`` / ``2560 x 1440`` to a canonical ``WIDTHxHEIGHT``."""
    if raw is None:
        return None
    text = str(raw).strip().lower().replace(" ", "")
    text = text.replace("×", "x")
    if text in RESOLUTIONS:
        return RESOLUTIONS[text]
    match = re.fullmatch(r"(\d{3,5})x(\d{3,5})", text)
    if match:
        return f"{int(match.group(1))}x{int(match.group(2))}"
    for token, canonical in RESOLUTIONS.items():
        if token in text:
            return canonical
    return None


def resolution_pixels(value: Any) -> int | None:
    """Total pixel count, used to order resolutions deterministically."""
    canonical = normalize_resolution(value)
    if canonical is None:
        return None
    width, _, height = canonical.partition("x")
    try:
        return int(width) * int(height)
    except ValueError:
        return None


def resolution_label(value: Any) -> str:
    canonical = normalize_resolution(value)
    if canonical is None:
        return str(value)
    return RESOLUTION_LABELS.get(canonical, canonical)


def human_label(key: str) -> str:
    return HUMAN_KEY_LABELS.get(key, key.replace("_", " ").replace(".", " "))


# --------------------------------------------------------------------------- #
# Research hints — what to ask the web for, per category
# --------------------------------------------------------------------------- #
MONITOR_ATTRIBUTE_HINT = (
    "attributes keys to use when known: display.size_in (number), display.resolution "
    "('2560x1440'), display.refresh_rate_hz (number), display.panel_type ('IPS'), "
    "display.curved (bool), display.hdr (bool), connectivity.hdmi (bool), "
    "connectivity.displayport (bool), connectivity.usb_c (bool), "
    "connectivity.usb_c_power_delivery (bool), connectivity.thunderbolt (bool), "
    "adaptive_sync.freesync (bool), adaptive_sync.gsync (bool), "
    "ergonomics.height_adjustable (bool), ergonomics.vesa (bool)"
)

WEARABLE_ATTRIBUTE_HINT = (
    "attributes keys to use when known: wearable.form_factor ('ring'/'watch'/'band'), "
    "wearable.battery_days (number), wearable.subscription_required (bool - true when a "
    "paid membership is needed for the core features), wearable.water_resistance_atm "
    "(number), wearable.weight_g (number), sensors.heart_rate (bool), sensors.spo2 (bool), "
    "sensors.temperature (bool), sensors.ecg (bool), sensors.sleep_tracking (bool), "
    "sensors.activity_tracking (bool), sensors.gps (bool), connectivity.bluetooth (bool), "
    "connectivity.nfc (bool), compat.ios (bool), compat.android (bool), "
    "material.titanium (bool)"
)

CATEGORY_NOUNS = {
    CATEGORY_MONITOR: "computer monitor",
    CATEGORY_WEARABLE: "wearable device (smart ring, watch or fitness band)",
}


def attribute_hint(category: str) -> str:
    """The attribute keys a research query should ask for, for this category."""
    if category == CATEGORY_WEARABLE:
        return WEARABLE_ATTRIBUTE_HINT
    return MONITOR_ATTRIBUTE_HINT


def category_noun(category: str) -> str:
    """How to name the category in a search query."""
    return CATEGORY_NOUNS.get(category, category)
