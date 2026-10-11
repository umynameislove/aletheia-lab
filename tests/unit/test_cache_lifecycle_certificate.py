"""Independent malformed-tape cases, lawful reuse and query-specific projection."""

from copy import deepcopy

import pytest

from aletheia_lab.evaluation.cache_lifecycle_certificate import (
    certificate_answers,
    lifecycle_answers,
    project_records,
)


def tape():
    value = {"cid": "c", "generation": "A", "digest": "a" * 64, "x": 0, "y": 0}
    return [
        {
            "kind": "load_return",
            "sequence": 0,
            "time_ns": 0,
            "token": "load",
            "generation": "A",
            "object_id": 1,
            "digest": "a" * 64,
        },
        {
            "kind": "load_return",
            "sequence": 1,
            "time_ns": 1,
            "token": "loadB",
            "generation": "B",
            "object_id": 2,
            "digest": "b" * 64,
        },
        {
            "kind": "compute_return",
            "sequence": 2,
            "time_ns": 2,
            "token": "earlier-request",
            "object_id": 1,
            **value,
        },
        {
            "kind": "wrapper_return",
            "sequence": 3,
            "time_ns": 3,
            "token": "r",
            "selected_generation": "B",
            "selected_object_id": 2,
            "returned": value,
        },
        {
            "kind": "handler_terminal",
            "sequence": 4,
            "time_ns": 4,
            "token": "r",
            "selected_generation": "B",
            "selected_object_id": 2,
            "returned_producer": value,
            "status": 200,
            "raw_response": '{"y":0}',
        },
        {
            "kind": "response",
            "sequence": 5,
            "time_ns": 5,
            "token": "r",
            "route": "/infer",
            "body": {"x": 0},
            "status": 200,
            "raw_response": '{"y":0}',
        },
    ]


def test_actual_producer_not_request_token_with_identical_zero_outputs():
    assert certificate_answers(tape())["r"] == {
        "verdict": "violation",
        "producer": "c",
        "closure": "closed",
        "delivery": "observed",
    }


def test_wrapper_is_redundant_when_terminal_carries_actual_return():
    assert certificate_answers(project_records(tape())) == certificate_answers(tape())


@pytest.mark.parametrize(
    "index,key,value",
    [
        (2, "object_id", 999),
        (2, "sequence", 6),
        (4, "selected_object_id", 999),
        (4, "status", 500),
        (4, "raw_response", '{"y":7}'),
        (3, "selected_generation", "A"),
    ],
)
def test_foreign_object_future_computation_and_mismatched_terminal_are_conflicts(index, key, value):
    rows = deepcopy(tape())
    rows[index][key] = value
    assert certificate_answers(rows)["r"]["verdict"] == "conflict"


def test_delivered_response_is_separate_from_handler_closure():
    rows = tape()
    rows[-1]["raw_response"] = '{"y":7}'
    answer = certificate_answers(rows)["r"]
    assert answer["verdict"] == "violation" and answer["closure"] == "closed"
    assert answer["delivery"] == "conflict"
    assert certificate_answers(rows[:-1])["r"]["delivery"] == "unknown"


@pytest.mark.parametrize("kind", ["compute_return", "handler_terminal", "load_return"])
def test_missing_witness_not_conclusive(kind):
    rows = [r for r in tape() if r["kind"] != kind]
    assert certificate_answers(rows).get("r", {}).get("verdict", "unknown") == "unknown"


def test_duplicates_are_not_discarded_by_projection():
    rows = tape()
    rows.append(deepcopy(rows[2]))
    assert certificate_answers(project_records(rows))["r"]["verdict"] == "conflict"


def test_malformed_shape_is_not_an_asserted_certificate():
    assert certificate_answers([{"kind": "handler_terminal", "token": "r"}]) == {}


def reload_tape():
    rows = tape()[:2]
    rows.extend(
        [
            {
                "kind": "publish",
                "token": "loadB",
                "previous_generation": "A",
                "generation": "B",
                "object_id": 2,
                "digest": "b" * 64,
                "clear": True,
            },
            {
                "kind": "response",
                "token": "loadB",
                "route": "/reload",
                "body": {"artifact": "B"},
                "status": 200,
                "raw_response": '{"loaded_generation":"B"}',
            },
            {
                "kind": "load_failure",
                "token": "bad",
                "artifact": "C",
                "error_type": "JSONDecodeError",
                "preserved_generation": "B",
            },
            {
                "kind": "response",
                "token": "bad",
                "route": "/reload",
                "body": {"artifact": "C"},
                "status": 400,
                "raw_response": '{"error":"bad"}',
            },
        ]
    )
    return [{**row, "sequence": i, "time_ns": i} for i, row in enumerate(rows)]


def test_projection_keeps_actual_reload_and_failed_resident_service():
    rows = reload_tape()
    assert (
        lifecycle_answers(project_records(rows))
        == lifecycle_answers(rows)
        == {
            "loadB": {"candidate": "B", "status": 200, "resident": "B"},
            "bad": {"candidate": "C", "status": 400, "resident": "B"},
        }
    )


@pytest.mark.parametrize(
    "index,key,value", [(2, "previous_generation", "B"), (4, "preserved_generation", "A")]
)
def test_reload_ledger_rejects_wrong_publication_or_rollback_state(index, key, value):
    rows = reload_tape()
    rows[index][key] = value
    with pytest.raises(ValueError):
        lifecycle_answers(rows)


def test_reload_ledger_cannot_accept_duplicate_loaded_generation():
    rows = reload_tape()
    rows.append(deepcopy(rows[1]))
    with pytest.raises(ValueError, match="unique startup"):
        lifecycle_answers(rows)
