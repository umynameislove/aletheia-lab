"""Offline contract boundaries for the conditional client comparator."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from aletheia_lab.evaluation.cache_lifecycle_native_control import (
    client_projection,
    infer_client_contract,
)


def row(start: int, end: int, x: int, y: int) -> dict[str, Any]:
    return {
        "route": "/infer",
        "body": {"x": x},
        "status": 200,
        "raw_response": f'{{"y": {y}}}',
        "offered_ns": start,
        "completion_ns": end,
    }


def client(arm: str = "input_key") -> dict[str, Any]:
    return {
        "arm": arm,
        "parameters": {
            "A": {"coefficient": 1, "intercept": 0},
            "B": {"coefficient": 2, "intercept": 0},
        },
        "rows": [
            row(0, 10, 0, 0),
            row(11, 19, 0, 0),
            row(20, 60, 1, 1),
            {
                "route": "/reload",
                "body": {"artifact": "B"},
                "status": 200,
                "raw_response": '{"loaded_generation": "B"}',
                "offered_ns": 30,
                "completion_ns": 40,
            },
            row(41, 45, 0, 0),
            row(61, 70, 1, 1),
        ],
    }


def test_projection_copies_only_client_fields_and_parameters() -> None:
    values = client()
    source = {
        "config": {"arm": values["arm"], "truth": "secret"},
        "events": "must not read",
        "artifacts": values["parameters"],
        "rows": [],
    }
    for original in values["rows"]:
        source["rows"].append(
            {
                **original,
                "elapsed_ns": original["completion_ns"] - original["offered_ns"],
                "selected_generation": "secret",
                "returned_producer": "secret",
                "token": "secret",
                "event_start": 123,
            }
        )
    projected = client_projection(source)
    assert projected == values
    assert "secret" not in str(projected)
    projected["rows"][0]["body"]["x"] = 100
    assert source["rows"][0]["body"]["x"] == 0


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "invalid", "interval"])
def test_malformed_client_remains_unknown(mutation: str) -> None:
    values = client()
    if mutation == "missing":
        values["rows"].pop(3)
    elif mutation == "duplicate":
        values["rows"].append(deepcopy(values["rows"][3]))
    elif mutation == "invalid":
        values["rows"][3]["raw_response"] = "invalid JSON"
    else:
        values["rows"][0]["completion_ns"] = -1
    assert set(infer_client_contract(values, cache_history=True, driver_barrier=True)) == {
        "unknown"
    }


def test_interval_overlap_and_zero_collision_remain_unknown() -> None:
    assert infer_client_contract(client()) == [
        "unknown",
        "unknown",
        "unknown",
        "unknown",
        "violation",
    ]


def test_cache_history_resolves_zero_without_resolving_overlap() -> None:
    assert infer_client_contract(client(), cache_history=True) == [
        "compliant",
        "compliant",
        "compliant",
        "violation",
        "violation",
    ]
    values = client("clear")
    assert infer_client_contract(values, cache_history=True)[3] == "compliant"


def test_cold_uncontended_key_returns_own_captured_producer_conditionally() -> None:
    values = client()
    assert infer_client_contract(values)[2] == "unknown"
    assert infer_client_contract(values, cache_history=True)[2] == "compliant"
    values["rows"][-1]["offered_ns"] = 50
    assert infer_client_contract(values, cache_history=True)[2] == "unknown"


def test_earlier_same_key_even_failed_call_blocks_cold_key_inference() -> None:
    values = client()
    prior = row(1, 5, 1, 1)
    prior["status"] = None
    values["rows"].insert(0, prior)
    assert infer_client_contract(values, cache_history=True)[3] == "unknown"


def test_driver_barrier_is_an_additional_conditional_premise() -> None:
    values = client()
    assert infer_client_contract(values, cache_history=True, driver_barrier=True)[2] == "compliant"
    values["rows"][2]["raw_response"] = '{"y": 2}'
    assert infer_client_contract(values, driver_barrier=True)[2] == "violation"
    values["rows"].append(row(21, 59, 1, 2))
    assert infer_client_contract(values, driver_barrier=True)[2] == "unknown"


def test_eviction_possibility_disables_zero_history_conclusion() -> None:
    values = client()
    values["rows"].extend(row(100 + i * 2, 101 + i * 2, i, i * 2) for i in range(3, 10))
    assert infer_client_contract(values, cache_history=True)[3] == "unknown"


def test_nonzero_affine_collision_is_not_mistaken_for_identity() -> None:
    values = client()
    values["parameters"]["B"]["intercept"] = -1
    assert infer_client_contract(values, cache_history=True)[-1] == "unknown"


@pytest.mark.parametrize("arm", ["isolated", "generation_key"])
def test_repair_source_semantics_support_conditional_compliance(arm: str) -> None:
    values = client(arm)
    values["rows"][-1]["raw_response"] = '{"y": 2}'
    assert set(infer_client_contract(values)) == {"compliant"}


@pytest.mark.parametrize("arm", ["isolated", "generation_key", "input_key"])
def test_source_invariant_cannot_make_contrary_body_compliant(arm: str) -> None:
    values = client(arm)
    values["rows"].append(row(80, 90, 2, 2))
    assert infer_client_contract(values, cache_history=True)[-1] == "conflict"


def test_missing_response_and_outside_catalogue_are_not_compliant() -> None:
    values = client()
    values["rows"][0]["status"] = None
    values["rows"][-1]["raw_response"] = '{"y": 99}'
    assert infer_client_contract(values)[0] == "unknown"
    assert infer_client_contract(values)[-1] == "conflict"


def test_source_history_cannot_override_contrary_numeric_evidence() -> None:
    values = client("clear")
    values["parameters"]["B"]["intercept"] = 1
    assert infer_client_contract(values, cache_history=True)[3] == "conflict"


def test_unexpected_successful_reload_and_malformed_operand_fail_closed() -> None:
    values = client()
    unexpected = deepcopy(values["rows"][3])
    unexpected["raw_response"] = '{"loaded_generation": "C"}'
    values["rows"].append(unexpected)
    assert set(infer_client_contract(values)) == {"unknown"}
    values = client()
    values["rows"][0]["body"] = None
    assert set(infer_client_contract(values, cache_history=True)) == {"unknown"}


def test_old_zero_fill_after_clear_cannot_be_ruled_out_by_current_nonoverlap() -> None:
    values = client("clear")
    values["rows"] = [row(20, 45, 0, 0), values["rows"][3], row(46, 50, 0, 0)]
    # The cold old call is compliant. Its completion after reload can refill A,
    # although it no longer overlaps the new request, whose zero body collides.
    assert infer_client_contract(values, cache_history=True) == ["compliant", "unknown"]


def test_zero_call_completed_before_reload_cannot_refill_cleared_cache() -> None:
    values = client("clear")
    values["rows"] = [row(20, 29, 0, 0), values["rows"][3], row(46, 50, 0, 0)]
    assert infer_client_contract(values, cache_history=True) == ["compliant", "compliant"]
