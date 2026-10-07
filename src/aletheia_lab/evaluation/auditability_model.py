"""Finite counterhistory checks of established query determinacy.

The compatible-history criterion is prior art, not a new impossibility theorem.
These bounded enumerations test a declared footprint, not arbitrary deployments.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import product
from typing import Any


def histories() -> list[dict[str, Any]]:
    """Two requests, two allowed states, and explicit closure/availability."""
    return [
        {
            "requested": requested,
            "used": used,
            "input": operand,
            "output": operand * used,
            "closed": closed,
            "captured": captured,
            "retained": retained,
        }
        for requested, used, operand, closed, captured, retained in product(
            (1, 2), (1, 2), (0, 1), (False, True), (False, True), (False, True)
        )
    ]


def projection(history: dict[str, Any], boundary: str) -> tuple[Any, ...]:
    base = (history["requested"], history["input"], history["output"], history["closed"])
    if boundary == "behavior":
        return base
    if boundary != "linked_state":
        raise ValueError("unknown boundary")
    available = history["captured"] and history["retained"] and history["closed"]
    return (*base, history["used"] if available else None)


def determine(worlds: list[dict[str, Any]]) -> str:
    if not worlds:
        return "inconsistent"
    values = {h["requested"] == h["used"] for h in worlds}
    return "unknown" if len(values) != 1 else "compliant" if True in values else "violation"


def check_model() -> dict[str, Any]:
    worlds = histories()
    groups: dict[str, Any] = {}
    for boundary in ("behavior", "linked_state"):
        classes: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for history in worlds:
            classes[projection(history, boundary)].append(history)
        groups[boundary] = {
            "classes": len(classes),
            "ambiguous_classes": sum(determine(group) == "unknown" for group in classes.values()),
        }
    first = {
        "requested": 1,
        "used": 1,
        "input": 0,
        "output": 0,
        "closed": True,
        "captured": True,
        "retained": True,
    }
    second = {**first, "used": 2}
    assert projection(first, "behavior") == projection(second, "behavior")
    assert determine([first, second]) == "unknown"
    assert projection(first, "linked_state") != projection(second, "linked_state")
    # An actually unavailable observation cannot be repaired by a later pin.
    lost = {**first, "retained": False}
    assert projection(lost, "linked_state")[-1] is None
    assert determine([]) == "inconsistent"
    return {
        "history_count": len(worlds),
        "boundaries": groups,
        "equal_entire_behavior_projection_counterpair": [first, second],
        "empty_compatible_set": "inconsistent",
        "late_pin_resurrects": False,
        "scope": "finite footprint; honest capture premise; established determinacy",
    }
