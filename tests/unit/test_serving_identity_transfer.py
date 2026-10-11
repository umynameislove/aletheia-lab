"""Synthetic analysis checks, not outcomes of the selected native comparison."""

from copy import deepcopy
from typing import Any

import pytest

from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.evaluation.serving_identity_transfer import (
    EXPECTED,
    FORECAST,
    analyze,
    captured_frame,
    reference,
)


def inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    models = {
        label: {
            "owned_state": {"coef": coefficients, "intercept": 0.0, "features": 2},
            "pickle_sha256": digest(label),
            "closure": {"closure_sha256": digest([label, "closure"])},
        }
        for label, coefficients in (("A", [1.0, 0.0]), ("B", [1.0, 2.0]))
    }
    fixtures = {"models": models, "tag": "state_model:immutable"}
    workers = {}
    for version, identities in FORECAST.items():
        rows = []
        for index, label in enumerate(identities):
            state = models[label]["owned_state"]
            rows.append(
                {
                    "slot": f"t{index}",
                    "token": f"identity-t{index}",
                    "load_generation": index + 1,
                    "load_object_id": index + 10,
                    "caller_object_id": index + 10,
                    "actual_use_object_id": index + 10,
                    "caller_backend_state": state,
                    "caller_serialized_state": state,
                    "captured_load_state": state,
                    "captured_use_state": state,
                    "selected_model_sha256": models[label]["pickle_sha256"],
                    "input": [[3.0, 0.0]],
                    "output": [3.0],
                    "closed": True,
                    "exception": None,
                }
            )
        workers[version] = {"requests": rows, "operations": []}
    return workers, fixtures, {label: model["closure"] for label, model in models.items()}


def test_complete_ordinary_baselines_tie_and_projection_is_discriminating() -> None:
    workers, fixtures, signatures = inputs()
    result = analyze(workers, fixtures, signatures)
    assert result["complete_correct_counts"] == {
        "candidate": 16,
        "native_history": 16,
        "signing_integrated": 16,
    }
    assert result["counterpair"]["projection_equal"] is True
    assert result["counterpair"]["actual_use_verdicts"] == {
        "affected": "violation",
        "fixed": "compliant",
    }
    assert result["view_counts"]["missing_use"]["candidate"] == {"unknown": 16}
    assert result["view_counts"]["missing_load_closure"]["native_history"] == {"unknown": 16}
    assert result["view_counts"]["conflicting_use"]["signing_integrated"] == {"conflict": 16}


def test_reference_does_not_use_output_capture_or_requested_identity() -> None:
    workers, fixtures, _ = inputs()
    row = deepcopy(workers["affected"]["requests"][1])
    row["output"] = [999]
    row["captured_use_state"] = fixtures["models"]["A"]["owned_state"]
    assert reference(row, fixtures) == {"identity": "B", "verdict": "violation"}
    frame = captured_frame(row, fixtures)
    assert frame["requested"] == "aaa" and frame["loads"]["2"]["model"] == "bbb"


def test_load_association_or_ambiguous_state_cannot_qualify_reference() -> None:
    workers, fixtures, _ = inputs()
    row = deepcopy(workers["fixed"]["requests"][1])
    row["load_object_id"] = -1
    assert reference(row, fixtures)["verdict"] == "unknown"
    row["load_object_id"] = row["caller_object_id"]
    fixtures["models"]["B"] = fixtures["models"]["A"]
    assert reference(row, fixtures)["verdict"] == "unknown"


def test_wrong_prediction_is_retained_not_excluded() -> None:
    workers, fixtures, signatures = inputs()
    workers["fixed"]["requests"][6] = deepcopy(workers["affected"]["requests"][6])
    result = analyze(workers, fixtures, signatures)
    assert result["identity_prediction_counts"] == {"supported": 15, "contradicted": 1}
    assert result["completed_requests"] == 16
    assert result["forecasts"]["F3"] != "supported"


def test_failed_native_prefix_preserves_planned_denominator() -> None:
    workers, fixtures, signatures = inputs()
    workers["fixed"]["requests"] = workers["fixed"]["requests"][:2]
    result = analyze(workers, fixtures, signatures)
    assert result["planned_requests"] == 16
    assert result["completed_requests"] == 10
    assert result["unattempted_requests"] == 6
    assert all(value != "supported" for value in result["forecasts"].values())


def test_changed_signed_closure_and_shuffled_prefix_rejected() -> None:
    workers, fixtures, signatures = inputs()
    signatures["A"] = {"closure_sha256": digest("not the fixture")}
    with pytest.raises(ValueError, match="signed closure"):
        analyze(workers, fixtures, signatures)
    signatures["A"] = fixtures["models"]["A"]["closure"]
    workers["fixed"]["requests"].reverse()
    with pytest.raises(ValueError, match="ordered native prefix"):
        analyze(workers, fixtures, signatures)
    assert EXPECTED[0] == EXPECTED[7] == "B"


def test_entered_native_failure_is_not_counted_as_unattempted() -> None:
    workers, fixtures, signatures = inputs()
    worker = workers["fixed"]
    worker["requests"] = worker["requests"][:3]
    worker.update(
        entered_slots=["t0", "t1", "t2", "t3"],
        active_slot="t3",
        unattempted_slots=["t4", "t5", "t6", "t7"],
        terminal="failed",
        terminal_error={"type": "RuntimeError"},
    )
    result = analyze(workers, fixtures, signatures)
    assert result["unfinished_requests"] == 5
    assert result["attempted_failed_requests"] == 1
    assert result["unattempted_requests"] == 4
    assert result["versions"]["fixed"]["terminal_error"] == {"type": "RuntimeError"}
