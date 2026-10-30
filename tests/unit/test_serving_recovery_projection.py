"""Observation projection must exclude hidden fields even below allowed objects."""

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "observed_trace", _ROOT / "scripts/serving_recovery/project_trace.py"
)
assert _SPEC is not None and _SPEC.loader is not None
projection = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(projection)


def response():
    observed = {
        "request_id": "example",
        "input": 3,
        "output": 13,
        "fitted_output": 7,
        "actualcomponents": {
            "coef": [2],
            "intercept": 1,
            "n_features_in": 1,
            "helper": 5,
            "runtime_offset": 1,
            "runtime_revision": "r1",
            "helper_revision": "h1",
            "training_revision": "A",
            "private_answer": "must disappear",
        },
        "sourcehashes": {
            "runtime": "a" * 64,
            "helper": "b" * 64,
            "training": "c" * 64,
            "private_answer": "must disappear",
        },
        "generation": "generation-a",
        "pid": 1,
        "instance_id": 2,
        "fitted_id": 3,
        "selected_ns": 10,
        "consumed_ns": 20,
        "completed_ns": 30,
        "evidence_origin": "authored instrumentation",
        "_reference": {"private_answer": "must disappear"},
    }
    return {
        **observed,
        "targetC": {"private_answer": "must disappear"},
        "reference": {"private_answer": "must disappear"},
        "http": {
            "status": 200,
            "method": "POST",
            "route": "/infer",
            "parent_start_ns": 1,
            "parent_end_ns": 31,
            "body": {"parameters": {"record": observed}},
            "raw_utf8": "must disappear",
        },
    }


def test_projection_excludes_nested_reference_and_raw_body():
    actual = projection.project([response()])
    assert "must disappear" not in json.dumps(actual)
    row = actual["records"][0]
    assert row["record"]["output"] == 13
    assert set(row) == {"id", "state", "record"}
    assert set(row["state"]["sourcehashes"]) == {"runtime", "helper", "training"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.update(output=99),
        lambda row: row["http"]["body"]["parameters"]["record"].update(request_id="other"),
        lambda row: row.update(http=None),
    ],
)
def test_response_binding_rejects_mismatched_or_unreceived_rows(mutate):
    row = response()
    mutate(row)
    with pytest.raises(ValueError):
        projection.project([row])


def test_duplicate_empty_and_nonfinite_input_rejected():
    with pytest.raises(ValueError):
        projection.project([response(), response()])
    with pytest.raises(ValueError):
        projection.project([])
    row = response()
    row["input"] = row["http"]["body"]["parameters"]["record"]["input"] = float("inf")
    with pytest.raises(ValueError):
        projection.project([row])


def test_http_failure_is_retained_without_reference():
    row = response()
    row["http"]["status"] = 503
    result = projection.project([row])["records"][0]
    assert result["state"] == {}
    assert result["record"]["response_unavailable"] is True
    assert result["record"]["http"]["status"] == 503


def test_nested_object_in_scalar_observation_rejected():
    row = response()
    row["evidence_origin"] = row["http"]["body"]["parameters"]["record"]["evidence_origin"] = {
        "private": "value"
    }
    with pytest.raises(ValueError):
        projection.project([row])
