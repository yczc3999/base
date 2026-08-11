"""Canonical, file-backed WP-01C P1A semantic fixtures."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "p1a_semantics"


def load_fixture(name: str) -> dict:
    """Load one canonical JSON fixture; tests must not fall back to Python constants."""

    path = FIXTURE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing p1a fixture: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"p1a fixture must be an object: {path}")
    return value


BERNOULLI = load_fixture("bernoulli.json")
TIME_NESTED = load_fixture("time_nested.json")
MUTUALLY_EXCLUSIVE = load_fixture("mutually_exclusive.json")
CONDITIONAL = load_fixture("conditional.json")
VOID_PARTIAL = load_fixture("void_partial.json")

ALL_SCENARIOS = {
    scenario["name"]: scenario
    for scenario in (
        BERNOULLI,
        TIME_NESTED,
        MUTUALLY_EXCLUSIVE,
        CONDITIONAL,
        VOID_PARTIAL,
    )
}
