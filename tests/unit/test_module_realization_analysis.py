"""Verifier rejects loss of an acknowledged record and credits native capabilities."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from aletheia_lab.evaluation.module_realization_analysis import _census, _frontier, _truth
from aletheia_lab.evaluation.module_realization_study import design, prepare

ROOT = Path(__file__).resolve().parents[2]


def test_fixed_design_has_complete_nested_census_and_distinct_slices() -> None:
    plan = design(ROOT)
    counts = {
        name: sum(row["slice"] == name for row in plan["cells"])
        for name in ("repair_cost", "transport", "crash")
    }
    assert counts == {"repair_cost": 36, "transport": 10, "crash": 8}
    assert len(plan["cells"]) * 37 == 1998
    assert plan["protocol"]["provider_calls"] == 0
    assert "not field" in plan["protocol"]["source_eligibility"]


def test_prepare_is_additive_and_refuses_overwrite(tmp_path: Path) -> None:
    directory = tmp_path / "new-study"
    first = prepare(ROOT, directory)
    before = (directory / "plan.json").read_bytes()
    assert first["cells"] == 54
    with pytest.raises(ValueError, match="fresh"):
        prepare(ROOT, directory)
    assert before == (directory / "plan.json").read_bytes()


def test_parent_frontier_is_not_reconstructed_from_surviving_database() -> None:
    client = [{"kind": "response", "response": {"collector": {"durable_ack_digests": {"1": "a"}}}}]
    assert _frontier(client) == {1: "a"}
    changed = copy.deepcopy(client[0])
    changed["response"]["collector"]["durable_ack_digests"]["1"] = "different"
    with pytest.raises(ValueError, match="conflicts"):
        _frontier([*client, changed])


def test_unserved_offer_is_not_accepted_as_no_native_execution() -> None:
    with pytest.raises(ValueError, match="unserved"):
        _census([{"kind": "offer"}, {"kind": "process_exit"}], {})


@pytest.mark.parametrize("x", [0, 1, 2])
def test_numeric_baseline_credited_but_zero_does_not_prove_origin(x: int) -> None:
    binding = {"coefficient": 1.0, "helper_sha256": "a"}
    loads = {"l": {"intended": 2.0, "expected_helper_sha256": "b"}}
    predictions = {"r": {"binding": binding, "load_id": "l", "x": x, "y": x}}
    rows = [
        {"route": "predict", "value": {"request_id": "r", "x": x}, "response": {"body": {"y": x}}}
    ]
    rows.insert(0, {"route": "load", "value": {"label": "B"}, "response": {"status": 200}})
    truth, numeric, history = _truth(
        rows,
        loads,
        predictions,
        {"order": ["A", "B", "A"], "variant": "collision", "repair": "none"},
    )
    assert truth == {"r": "violation"}
    assert numeric == {"unknown" if x == 0 else "correct": 1}
    assert history == {"correct": 1}


def test_source_history_rule_is_checked_against_actual_reference() -> None:
    rows = [
        {"route": "predict", "value": {"request_id": "r", "x": 1}, "response": {"body": {"y": 2}}}
    ]
    predictions = {
        "r": {"binding": {"coefficient": 2.0, "helper_sha256": "b"}, "load_id": "l", "x": 1, "y": 2}
    }
    loads = {"l": {"intended": 2.0, "expected_helper_sha256": "b"}}
    rows.insert(0, {"route": "load", "value": {"label": "B"}, "response": {"status": 200}})
    _, _, history = _truth(
        rows,
        loads,
        predictions,
        {"order": ["A", "B", "A"], "variant": "collision", "repair": "none"},
    )
    assert history == {"false": 1}
