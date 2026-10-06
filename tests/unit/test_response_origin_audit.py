"""Finite correspondence falsifiers, not native-serving scientific outcomes."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aletheia_lab.evaluation import response_origin_audit as audit
from aletheia_lab.evaluation.request_model_audit import digest


def frame(cache: bool = True) -> dict[str, Any]:
    x, y = digest([0.0]), digest([0.0])
    nodes = {
        "compute": {
            "kind": "compute",
            "model": "model",
            "version": "1",
            "generation": "old",
            "input": x,
            "output": y,
            "fingerprint": digest({"coef": 1}),
            "closed": True,
        },
    }
    if cache:
        nodes["entry"] = {
            "kind": "cache",
            "producer": "compute",
            "input": x,
            "output": y,
            "closed": True,
        }
    return {
        "request": {
            "token": "request",
            "model": "model",
            "version": "1",
            "generation": "old",
            "contract": "selected_generation",
            "input": x,
            "output": y,
            "closed": True,
            "failed": False,
            "origin": "entry" if cache else "compute",
        },
        "nodes": nodes,
    }


@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("contract", ["route", "selected_generation"])
def test_legitimate_cache_or_predict_matches_same_capture_ordinary_join(cached, contract):
    value = frame(cached)
    value["request"]["contract"] = contract
    assert audit.resolve(value) == audit.explicit_join(value) == "compliant"
    assert audit.direct_only(value) == ("unknown" if cached else "compliant")


@pytest.mark.parametrize("changed", ["model", "version", "generation"])
def test_actual_origin_not_latest_registry_or_equal_output_decides(changed):
    value = frame()
    value["request"][changed] = "new"
    assert audit.resolve(value) == audit.explicit_join(value) == "violation"
    if changed == "generation":
        value["request"]["contract"] = "route"
        assert audit.resolve(value) == "compliant"


@pytest.mark.parametrize("field", ["closed", "failed", "output", "generation", "origin"])
def test_missing_authority_closure_or_failed_response_abstains(field):
    value = frame()
    value["request"][field] = {"closed": False, "failed": True}.get(field)
    assert audit.resolve(value) == audit.explicit_join(value) == "unknown"


def test_missing_producer_and_open_entry_abstain_cycle_and_operands_conflict():
    value = frame()
    del value["nodes"]["compute"]
    assert audit.resolve(value) == audit.explicit_join(value) == "unknown"
    value = frame()
    value["nodes"]["entry"]["closed"] = False
    assert audit.resolve(value) == "unknown"
    value = frame()
    value["nodes"]["entry"]["producer"] = "entry"
    assert audit.resolve(value) == audit.explicit_join(value) == "conflict"
    value = frame()
    value["nodes"]["compute"]["input"] = digest([4.0])
    assert audit.resolve(value) == audit.explicit_join(value) == "conflict"


@pytest.mark.parametrize("change", ["extra", "oversize", "hash", "boolean", "identity"])
def test_strict_bounded_admission(change):
    value = frame()
    if change == "extra":
        value["request"]["prose"] = "not evidence"
    elif change == "oversize":
        value["nodes"] = {str(i): copy.deepcopy(value["nodes"]["compute"]) for i in range(129)}
    elif change == "hash":
        value["request"]["input"] = "x" * 64
    elif change == "boolean":
        value["request"]["closed"] = 1
    else:
        value["request"]["token"] = "x" * 129
    with pytest.raises(ValueError):
        audit.resolve(value)


def test_native_body_does_not_hide_visible_route_violation_in_generation_contract():
    row = {
        "status": 200,
        "contract": "selected_generation",
        "model": "model",
        "version": "1",
        "response": {"model_name": "model", "model_version": "2"},
        "response_headers": {"ce-modelid": "model", "ce-modelversion": "1"},
    }
    assert audit.native_identity(row) == "violation"
    assert audit.native_identity(row, header=True) == "unknown"
    row["contract"] = "route"
    assert audit.native_identity(row, header=True) == "compliant"
    row["response"] = None
    assert audit.native_identity(row) == "unknown"


def test_finite_collision_and_complete_safety_summary():
    assert audit.collisions([[0], [0]], ["compliant", "violation"])["opposite_status_groups"] == [
        [0, 1]
    ]
    result = audit.summary(
        ["compliant", "violation", "unknown"], ["compliant", "unknown", "violation"]
    )
    assert (
        result["correct_conclusive"],
        result["false_conclusive"],
        result["missing_conclusive"],
    ) == (1, 1, 1)
    with pytest.raises(ValueError):
        audit.summary(["compliant"], [])
