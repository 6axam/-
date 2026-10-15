"""Deterministic coupling for Anya 2.0 global and contact affect.

The functions are intentionally pure: storage and event ordering remain the
responsibility of the shared-state coordinator.  That keeps trajectories
seedable and prevents an appraisal from becoming a hidden LLM-side mutation.
"""
from __future__ import annotations

import math
from typing import Mapping

from app.emotions.affective import clamp


CONTACT_AFFECT_BASELINES = {
    "hurt": .02,
    "resentment": .03,
    "irritation": .05,
    "social_safety": .55,
    "willingness_to_talk": .55,
}

CONTACT_HALF_LIVES_HOURS = {
    "hurt": 42.0,
    "resentment": 120.0,
    "irritation": 10.0,
    "social_safety": 96.0,
    "willingness_to_talk": 36.0,
}


def advance_contact_affect(values: Mapping[str, float], elapsed_hours: float) -> dict[str, float]:
    """Move each channel toward its own baseline using elapsed real time."""
    result = {name: clamp(values.get(name, base)) for name, base in CONTACT_AFFECT_BASELINES.items()}
    if elapsed_hours <= 0:
        return result
    for name, baseline in CONTACT_AFFECT_BASELINES.items():
        factor = math.exp(-math.log(2) * elapsed_hours / CONTACT_HALF_LIVES_HOURS[name])
        result[name] = clamp(baseline + (result[name] - baseline) * factor)
    return result


def apply_contact_appraisal(values: Mapping[str, float], appraisal) -> dict[str, float]:
    """Apply a bounded interpersonal event without contaminating other contacts."""
    current = {name: clamp(values.get(name, base)) for name, base in CONTACT_AFFECT_BASELINES.items()}
    a = appraisal.values() if hasattr(appraisal, "values") else dict(appraisal or {})
    intensity = clamp(a.get("intensity") or .35)
    relevance = clamp(a.get("social_relevance") or 0)
    negative = max(float(a.get(key) or 0) for key in ("rejection", "dismissal", "betrayal", "replacement_threat", "other_blame"))
    repair = clamp(a.get("repair_attempt") or 0) * relevance
    warmth = max(float(a.get(key) or 0) for key in ("warmth", "care", "closeness")) * relevance
    pressure = negative * relevance * intensity
    current["hurt"] = clamp(current["hurt"] + .070 * pressure - .030 * repair)
    current["resentment"] = clamp(current["resentment"] + .052 * pressure - .022 * repair - .006 * warmth)
    current["irritation"] = clamp(current["irritation"] + .065 * (float(a.get("frustration") or 0) + .55 * negative) * relevance * intensity - .018 * repair)
    current["social_safety"] = clamp(current["social_safety"] - .075 * pressure + .035 * repair + .012 * warmth)
    # A person can remain loved yet currently be unwelcome; willingness is a
    # short/medium term state rather than a proxy for bond or love.
    current["willingness_to_talk"] = clamp(current["willingness_to_talk"] - .060 * pressure - .025 * float(a.get("threat") or 0) * intensity + .030 * repair + .010 * warmth)
    return current
