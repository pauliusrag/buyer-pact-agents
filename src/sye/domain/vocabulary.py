"""Lightweight normalisation vocabulary for the monitor category.

The architecture is category-generic: constraints are ``key/operator/value``
triples, and this module is the only monitor-specific piece. Adding phones or
appliances later means adding another vocabulary module, not new agents.
"""

from __future__ import annotations

import re
from typing import Any

CATEGORY_MONITOR = "monitor"

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

BOOLEAN_KEYS = {
    k
    for k in ATTRIBUTE_KEYS
    if k.startswith(("connectivity.", "adaptive_sync.", "ergonomics.", "usage."))
} | {
    "display.curved",
    "display.hdr",
}

NUMERIC_KEYS = {"display.size_in", "display.refresh_rate_hz", "price.unit_price"}

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
    "connectivity.usb_c_power_delivery": ["connectivity.thunderbolt"],
    "connectivity.thunderbolt": [],
    "adaptive_sync.freesync": ["adaptive_sync.gsync"],
    "adaptive_sync.gsync": ["adaptive_sync.freesync"],
}

PANEL_TYPES = ("ips", "va", "tn", "oled", "qd-oled", "mini-led")

HUMAN_KEY_LABELS: dict[str, str] = {
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
