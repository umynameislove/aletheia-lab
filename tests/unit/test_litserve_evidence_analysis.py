"""Independent interval, arithmetic, live-receipt and fixed-forecast contracts."""

from __future__ import annotations

import copy
import json

import pytest

from aletheia_lab.evaluation.litserve_evidence_analysis import (
    actual_use,
    analyze,
    forecasts,
    native_uid,
    reference,
    validate_events,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256


def fixture(*, terminal=40, status=200, computed=True, entered=True):
    events = []

    def emit(kind, pid=1, time_ns=0, **values):
        events.append(
            {
                "kind": kind,
                "pid": pid,
                "sequence": sum(e["pid"] == pid for e in events),
                "time_ns": time_ns,
                "endpoint": "/a",
                **values,
            }
        )

    raw = encode({"format": "owned-affine-json/v1", "coefficient": 2.0, "intercept": 1.0}).encode()
    emit(
        "setup",
        coefficient=2.0,
        intercept=1.0,
        object_id=10,
        artifact_hex=raw.hex(),
        artifact_sha256=content_sha256(raw),
    )
    emit("submit", pid=2, time_ns=10, token="t", uid="u", payload={"x": 7.0})
    if entered:
        emit(
            "predict_enter",
            time_ns=20,
            token="t",
            object_id=10,
            coefficient=2.0,
            intercept=1.0,
            x=7.0,
            slot=0,
            batch="b",
        )
    if computed:
        emit(
            "computed",
            time_ns=terminal - 1,
            token="t",
            object_id=10,
            output=15.0,
            x=7.0,
            batch="b",
            slot=0,
        )
    if entered:
        emit(
            "terminal",
            time_ns=terminal,
            token="t",
            object_id=10,
            outcome="success",
            batch="b",
            slot=0,
        )
    data = (
        {"output": 15.0} if status == 200 else {"detail": "Request timed out", "status_code": 504}
    )
    emit(
        "transport",
        time_ns=terminal + 1,
        uid="u",
        response_data=data,
        status="OK" if status == 200 else "ERROR",
    )
    row = {
        "token": "t",
        "endpoint": "/a",
        "x": 7.0,
        "status": status,
        "body": {"output": 15.0} if status == 200 else {"detail": "Request timed out"},
        "end_ns": 50,
        "error": None,
        "family": "ordered_endpoint_batches",
    }
    return row, events


def test_success_independent_arithmetic_and_strong_native_correspondence():
    row, events = fixture()
    validate_events(events)
    assert reference(row, events)["origin"] == "compliant"
    assert native_uid(row, events) == {"origin": "compliant", "closure": "closed"}
    assert actual_use(row, events) == {"origin": "compliant", "closure": "closed"}


def test_client_timeout_and_late_terminal_do_not_retroactively_close_at_cut():
    row, events = fixture(terminal=100, status=None)
    row["error"] = "ReadTimeout"
    truth = reference(row, events)
    assert truth["origin"] == "unavailable"
    assert truth["closure_at_client_cut"] == "open"
    assert truth["eventual"] == "closed"
    assert native_uid(row, events)["closure"] == "unknown"
    assert actual_use(row, events)["closure"] == "open"


def test_missing_terminal_is_unknown_not_proof_of_pending_execution():
    row, events = fixture(terminal=100, status=None)
    events = [e for e in events if e["kind"] not in {"terminal", "transport"}]
    assert reference(row, events)["closure_at_client_cut"] == "unknown"
    assert actual_use(row, events)["closure"] == "unknown"


def test_native_queue_expiry_is_no_prediction_not_a_model_violation():
    row, events = fixture(status=504, computed=False, entered=False)
    truth = reference(row, events)
    assert truth["entered"] is False
    assert truth["eventual"] == "not_started"
    assert truth["closure_at_client_cut"] == "closed"
    assert truth["origin"] == "unavailable"


def test_transport_delay_changes_first_query_not_raw_truth_or_final_join():
    row, events = fixture()
    fast = [{"event": e, "received_ns": e["time_ns"] + 1} for e in events]
    delayed = [{"event": e, "received_ns": e["time_ns"] + 100} for e in events]
    left, right = analyze([row], events, fast), analyze([row], events, delayed)
    assert left["rows"][0]["truth"] == right["rows"][0]["truth"]
    assert left["rows"][0]["first_query"]["native_uid"]["origin"] == "compliant"
    assert right["rows"][0]["first_query"]["native_uid"]["origin"] == "unknown"
    assert left["rows"][0]["retrospective"] == right["rows"][0]["retrospective"]
    assert right["collector_complete"] is True


@pytest.mark.parametrize("change", ["artifact", "arithmetic", "object", "sequence", "clock"])
def test_raw_reference_rejects_consequential_corruption(change):
    row, events = fixture()
    if change == "artifact":
        events[0]["artifact_hex"] = b"changed".hex()
    elif change == "arithmetic":
        next(e for e in events if e["kind"] == "computed")["output"] = 99
    elif change == "object":
        next(e for e in events if e["kind"] == "computed")["object_id"] = 11
    elif change == "sequence":
        events[0]["sequence"] = 3
    else:
        events[0]["time_ns"] = 1000
    with pytest.raises(ValueError):
        validate_events(events)
        reference(row, events)


@pytest.mark.parametrize("change", ["duplicate", "foreign", "preproducer"])
def test_receipt_census_not_trusted_from_an_aggregate(change):
    row, events = fixture()
    receipts = [{"event": e, "received_ns": e["time_ns"] + 1} for e in events]
    if change == "duplicate":
        receipts.append(receipts[0])
    elif change == "foreign":
        receipts = copy.deepcopy(receipts)
        receipts[0]["event"]["object_id"] = 999
    else:
        receipts[1]["received_ns"] = 0
    with pytest.raises(ValueError):
        analyze([row], events, receipts)


def test_actual_mispairing_is_a_counted_outcome_not_an_eligibility_filter():
    row, events = fixture()
    row["body"]["output"] = 999
    receipts = [{"event": e, "received_ns": e["time_ns"] + 1} for e in events]
    result = analyze([row], events, receipts)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["truth"]["origin"] == "violation"
    assert result["rows"][0]["retrospective"]["native_uid"]["origin"] == "conflict"


def test_forecast_contradiction_is_reported_and_not_relabelled_a_failure():
    row, events = fixture()
    result = analyze([row], events, [{"event": e, "received_ns": e["time_ns"] + 1} for e in events])
    expected = forecasts("native", 0.0, result)
    assert expected["checks"]["native_uid_origin_after_drain"] is True
    assert expected["checks"]["queued_rejection_before_prediction"] is False
    assert expected["contradicted"] == 2
    assert result["rows"][0]["truth"]["origin"] == "compliant"


def test_owned_safe_json_parameter_binding_is_not_pickle_deserialization():
    _, events = fixture()
    assert json.loads(bytes.fromhex(events[0]["artifact_hex"])) == {
        "format": "owned-affine-json/v1",
        "coefficient": 2.0,
        "intercept": 1.0,
    }
