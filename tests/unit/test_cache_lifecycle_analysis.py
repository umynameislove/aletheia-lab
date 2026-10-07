"""Audit service dependencies and full-census failure accounting, offline."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from aletheia_lab.evaluation.cache_lifecycle_analysis import aggregate, audit_answers


def records(selected: str = "B", produced: str = "A") -> list[dict[str, Any]]:
    value = {"cid": "c", "generation": produced, "digest": "a" * 64, "x": 0, "y": 0}
    return [
        {"kind": "load_return", "generation": produced, "digest": "a" * 64, "sequence": 0},
        {"kind": "compute_return", **value, "sequence": 2},
        {
            "kind": "wrapper_return",
            "token": "r",
            "selected_generation": selected,
            "returned": value,
            "sequence": 4,
        },
        {"kind": "handler_terminal", "token": "r", "returned_producer": value, "sequence": 5},
    ]


@pytest.mark.parametrize(
    "selected,produced,verdict",
    [("A", "A", "compliant"), ("B", "A", "violation"), ("B", "B", "compliant")],
)
def test_same_numbers_do_not_identify_generation(
    selected: str, produced: str, verdict: str
) -> None:
    assert audit_answers(records(selected, produced))["r"] == {
        "verdict": verdict,
        "producer": "c",
        "closure": "closed",
    }


@pytest.mark.parametrize("missing", ["load_return", "compute_return", "handler_terminal"])
def test_missing_dependency_does_not_become_conclusive(missing: str) -> None:
    values = [r for r in records() if r["kind"] != missing]
    assert audit_answers(values)["r"]["verdict"] == "unknown"


@pytest.mark.parametrize(
    "duplicated", ["load_return", "compute_return", "wrapper_return", "handler_terminal"]
)
def test_duplicate_identity_is_not_silently_overwritten(duplicated: str) -> None:
    values = records()
    values.append(deepcopy(next(r for r in values if r["kind"] == duplicated)))
    assert audit_answers(values)["r"]["verdict"] == "conflict"


def test_output_or_binding_corruption_is_conflict() -> None:
    values = records()
    values[1]["digest"] = "b" * 64
    assert audit_answers(values)["r"]["verdict"] == "conflict"


def test_client_completion_without_handler_witness_is_not_terminal() -> None:
    values = records()[:-1] + [{"kind": "response", "token": "r", "status": 200}]
    assert audit_answers(values)["r"]["closure"] == "unknown"


def test_empty_reference_mode_does_not_supply_an_oracle() -> None:
    assert audit_answers([]) == {}


def test_missing_cells_keep_all_offered_denominators() -> None:
    executions = [{"config": {"evidence": "sufficient"}}, {"config": {"evidence": "none"}}]
    plan = {"cells": [{}, {}], "planned_inferences": 144, "planned_reload_operations": 4}
    result = aggregate(plan, executions, [None, {"verification": "fail"}])
    assert result["failed_or_incomplete_cells"] == [0, 1]
    assert result["by_evidence"]["sufficient"]["unknown_or_unserved"] == 72
    assert result["by_evidence"]["none"]["unknown_or_unserved"] == 72
    assert result["disposition"] == "narrow_or_incomplete"


def test_analysis_census_cannot_drop_a_failed_cell() -> None:
    with pytest.raises(ValueError, match="all planned"):
        aggregate({"cells": [{}, {}]}, [{}], [None])


def test_failed_planned_replica_cannot_make_survivor_sufficient() -> None:
    config = {"arm": "isolated", "evidence": "sufficient"}
    finding = {
        "verification": "pass",
        "complete": True,
        "offered_inferences": 72,
        "truth_counts": {"compliant": 72},
        "service": {"correct": 72, "false": 0, "unknown_or_unserved": 0},
        "forecasts": {"violation_count": True},
        "steady_latency_median_ns": 100,
        "physical_bytes_live": 100,
        "physical_bytes_closed": 50,
        "logical_bytes": 25,
        "measurements": {
            key: 1
            for key in (
                "capture_ns",
                "persist_ns",
                "query_ns",
                "hash_ns",
                "process_cpu_ns",
                "sign_verify_ns",
            )
        },
    }
    executions = [{"config": config, "returncode": 0}, {"config": config, "returncode": 1}]
    plan = {"cells": [config, config], "planned_inferences": 144, "planned_reload_operations": 4}
    result = aggregate(plan, executions, [finding, None])
    candidate = result["bounded_cost_candidates"][0]
    assert candidate["planned_cells"] == 2 and candidate["cells"] == 1
    assert candidate["complete_same_service"] is False
    assert result["by_evidence"]["sufficient"]["unknown_or_unserved"] == 72


def test_partial_cell_without_steady_phase_is_reported_not_crashed() -> None:
    config = {"arm": "clear", "evidence": "none"}
    finding = {
        "verification": "pass",
        "complete": False,
        "offered_inferences": 2,
        "truth_counts": {"compliant": 2},
        "service": {"correct": 0, "false": 0, "unknown_or_unserved": 2},
        "forecasts": {"violation_count": False},
        "steady_latency_median_ns": None,
        "physical_bytes_live": 0,
        "physical_bytes_closed": 0,
        "logical_bytes": 0,
        "measurements": {
            key: 0
            for key in (
                "capture_ns",
                "persist_ns",
                "query_ns",
                "hash_ns",
                "process_cpu_ns",
                "sign_verify_ns",
            )
        },
    }
    result = aggregate(
        {"cells": [config], "planned_inferences": 72, "planned_reload_operations": 2},
        [{"config": config, "returncode": 1}],
        [finding],
    )
    assert result["bounded_cost_candidates"][0]["steady_latency_range_ns"] is None
    assert result["by_evidence"]["none"]["unknown_or_unserved"] == 72
