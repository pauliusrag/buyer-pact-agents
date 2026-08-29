#!/usr/bin/env python
"""Generate a realistic mixed customer list for the demo.

Real demand is messy: different categories, different phrasings, different levels of
detail, and budgets that do not line up. This produces that, deterministically, so a
demo can be rehearsed and still look like the real thing.

    uv run python scripts/make_sample_customers.py --count 500
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

FIRST = [
    "anna",
    "ben",
    "cara",
    "dmitri",
    "eva",
    "felix",
    "gita",
    "hugo",
    "ines",
    "jonas",
    "kata",
    "lars",
    "mira",
    "nils",
    "olga",
    "pavel",
    "rita",
    "sam",
    "tova",
    "umar",
    "vera",
    "wilma",
    "xenia",
    "yara",
    "zack",
    "alice",
    "bruno",
    "chiara",
    "david",
    "elin",
    "frank",
    "greta",
    "henrik",
    "iris",
    "jakob",
    "klara",
    "linus",
    "maja",
    "noah",
    "oscar",
    "petra",
    "quinn",
    "rasmus",
    "sofia",
    "tomas",
    "ulla",
    "viktor",
    "wanda",
    "yusuf",
    "zoe",
    "amir",
    "bea",
    "cato",
    "dina",
    "emil",
    "fiona",
    "gustav",
    "hanna",
    "ivan",
    "julia",
]
LAST = [
    "andersson",
    "berg",
    "carlsson",
    "dahl",
    "ekstrom",
    "fors",
    "gustafsson",
    "holm",
    "iversen",
    "jansson",
    "karlsson",
    "lund",
    "moberg",
    "nyberg",
    "olsen",
    "persson",
    "quist",
    "rehn",
    "sandberg",
    "thorne",
    "ulven",
    "vik",
    "westin",
    "ohman",
    "novak",
    "kovacs",
    "horvath",
    "varga",
    "toth",
    "szabo",
    "nagy",
    "meyer",
    "fischer",
    "weber",
    "wagner",
    "becker",
    "schulz",
]

# Each template is (prompt, weight). Placeholders are filled from the option lists.
MONITOR = [
    "I need a {size} inch monitor for work, {res} at least. Under €{budget}.",
    'Looking for a {size}" {res} display for my home office. Around €{budget}.',
    "My laptop only has USB-C, so I need a monitor that charges over USB-C. {size} inch, {res}, max €{budget}.",
    "Want a {size} inch screen for coding. {res} minimum, brand doesn't matter. €{budget} tops.",
    "{size} inch {res} monitor, height adjustable stand would be nice. Up to €{budget}.",
    "Gaming monitor please: {size} inch, {res}, {hz}Hz or better, FreeSync. Max €{budget}.",
    'I want a fast {size}" {res} gaming display, at least {hz}Hz with adaptive sync. Budget €{budget}.',
    "Something for photo editing — {size} inch, {res}, accurate colours. I can spend €{budget}.",
    "Just need a bigger screen than my laptop. {size} inch is fine, under €{budget}.",
    "Two monitors for my desk, {size} inch {res} each, around €{budget} each.",
    "Curved {size} inch {res} monitor for spreadsheets all day, up to €{budget}.",
    "{size} inch monitor with USB-C charging and a decent stand, {res}, €{budget} max.",
]
WEARABLE = [
    "I want a smart ring that tracks my sleep and HRV. No monthly subscription. Under €{budget}.",
    "Looking for a sleep tracking ring, works with my iPhone, at least a week of battery. Max €{budget}.",
    "A ring for sleep and recovery, I refuse to pay a subscription. Around €{budget}.",
    "Smart ring with temperature and blood oxygen tracking, up to €{budget}.",
    "Fitness band with GPS and heart rate for running, waterproof, around €{budget}.",
    "I want a fitness tracker that counts steps and tracks sleep, budget €{budget}.",
    "Smartwatch for running with built-in GPS, at least {days} days battery. Max €{budget}.",
    "Activity tracker for the gym, heart rate and workout tracking, under €{budget}.",
    "A titanium smart ring, sleep tracking, no membership fee, up to €{budget}.",
    "Something to wear at night that tracks my sleep quality. Around €{budget}.",
    "Fitness band with SpO2 and sleep tracking that works with Android, €{budget} max.",
    "Smart ring for HRV and recovery, iPhone, at least {days} days of battery, €{budget}.",
]
OTHER = [
    "I want a mechanical keyboard, ideally wireless, under €{budget}.",
    "Noise cancelling headphones for the office, around €{budget}.",
    "A laptop stand and a keyboard, together under €{budget}.",
    "Wireless earbuds with decent battery, max €{budget}.",
]

SIZES = ["24", "27", "27", "27", "32", "34"]
RES = ["1080p", "1440p", "1440p", "QHD", "4K"]
HZ = ["120", "144", "165", "165", "180", "240"]
DAYS = ["5", "6", "7", "10"]


def _round_to(value: float, step: int, rng: random.Random) -> int:
    """People quote round numbers, and they under- and over-shoot."""
    jitter = rng.choice([-1, 0, 0, 1]) * step
    return max(step, int(round(value / step) * step + jitter))


def monitor_budget(size: str, res: str, hz: str, template: str, rng: random.Random) -> int:
    """What someone asking for these specs would plausibly expect to pay.

    Random specs paired with random budgets produce requests nobody would ever make —
    a 34-inch 4K panel for 220 euro — and the demo then fails for the wrong reason.
    """
    base = 150.0
    base += {"24": 0, "27": 60, "32": 170, "34": 260}.get(size, 60)
    base += {"1080p": 0, "1440p": 70, "QHD": 70, "4K": 190}.get(res, 60)
    if "Hz" in template:
        base += {"120": 30, "144": 60, "165": 90, "180": 110, "240": 190}.get(hz, 60)
    if "USB-C" in template or "charges over USB-C" in template:
        base += 50
    if "photo editing" in template or "colours" in template:
        base += 80
    return _round_to(base * rng.uniform(0.92, 1.18), 10, rng)


def wearable_budget(template: str, days: str, rng: random.Random) -> int:
    base = 120.0
    if "ring" in template.lower():
        base += 130
    if "titanium" in template:
        base += 60
    if "temperature" in template or "blood oxygen" in template or "SpO2" in template:
        base += 40
    if "GPS" in template:
        base += 60
    if "smartwatch" in template.lower():
        base += 90
    base += {"5": 0, "6": 10, "7": 20, "10": 40}.get(days, 10)
    return _round_to(base * rng.uniform(0.9, 1.2), 10, rng)


def build(count: int, seed: int) -> dict[str, str]:
    rng = random.Random(seed)
    people: dict[str, str] = {}
    used: set[str] = set()

    while len(people) < count:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{first}.{last}@example.com"
        if email in used:
            email = f"{first}.{last}{len(people)}@example.com"
        used.add(email)

        roll = rng.random()
        size, res, hz, days = (
            rng.choice(SIZES),
            rng.choice(RES),
            rng.choice(HZ),
            rng.choice(DAYS),
        )

        if roll < 0.44:
            template = rng.choice(MONITOR)
            budget = monitor_budget(size, res, hz, template, rng)
        elif roll < 0.88:
            template = rng.choice(WEARABLE)
            budget = wearable_budget(template, days, rng)
        else:
            template = rng.choice(OTHER)
            budget = rng.choice([80, 100, 120, 150, 200, 250])

        people[email] = template.format(size=size, res=res, hz=hz, days=days, budget=budget)
    return people


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate demo customers")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="src/sye/web/sample-customers.json")
    args = parser.parse_args()

    people = build(args.count, args.seed)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(people, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(people)} customers → {target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
