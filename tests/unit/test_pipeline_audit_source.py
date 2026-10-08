"""Development-only Pipeline transfer, alternate evidence and native controls."""

from __future__ import annotations

import copy
from unittest.mock import patch

import pytest

from aletheia_lab.evaluation import calibration_audit_source, pipeline_audit_source
from aletheia_lab.evaluation.pipeline_audit_source import (
    ARMS,
    QUERIES,
    SCHEMA,
    arithmetic,
    assess,
    execute,
    historical_membership,
    history_reference,
    predict,
    setup,
    state,
)


@pytest.fixture(scope="module", autouse=True)
def qualified_runtime():
    sklearn = pytest.importorskip("sklearn")
    pytest.importorskip("sklearn.frozen")
    if sklearn.__version__ != "1.9.0":
        pytest.skip("Pipeline native controls require pinned sklearn 1.9.0")


@pytest.fixture(scope="module")
def cases():
    return {arm: execute(arm, 4101) for arm in ARMS}


@pytest.mark.parametrize("arm", ARMS)
def test_forecasts_and_strong_native_history(cases, arm):
    row = cases[arm]
    assert row["forecast_supported"]
    assert row["ordinary_complete_history"] == history_reference(row)
    assert row["native_top_level_predictions"] == 2
    assert row["captured_packet"]["schema"] == SCHEMA
    assert historical_membership(row) == ("violation" if arm == "overlap" else "compliant")


def test_preprocessing_approval_equal_output_and_repairs(cases):
    for arm in ("changed_preprocessing_equal_output", "ineffective_frozen_fit"):
        row = cases[arm]
        assert row["same_entire_probability_output"]
        assert row["captured_answers"] == {
            "authorized_state": "violation",
            "disjoint_membership": "compliant",
            "sigmoid_arithmetic": "compliant",
        }
        before, after = row["native_packets"]
        assert (
            before["call"]["actual_state"]["classifier"]
            == after["call"]["actual_state"]["classifier"]
        )
        assert all(
            features[-1] == before["approved_state"]["preprocessing"]["mean"][-1]
            for features in before["call"]["input"]
        )
    assert not cases["preprocessing_mutation"]["same_entire_probability_output"]
    assert not cases["classifier_only_repair"]["same_entire_probability_output"]
    assert cases["classifier_only_repair"]["captured_answers"]["authorized_state"] == "violation"
    assert cases["restore_state"]["same_entire_probability_output"]
    assert set(cases["restore_state"]["captured_answers"].values()) == {"compliant"}


def test_membership_repair_preserves_overlapping_history(cases):
    row = cases["repair_membership"]
    before, after = row["native_packets"]
    assert before["membership"] == cases["overlap"]["native_packets"][0]["membership"]
    assert before["call"]["output"] == cases["overlap"]["native_packets"][0]["call"]["output"]
    assert assess(before)["disjoint_membership"] == "violation"
    assert assess(after) == dict.fromkeys(QUERIES, "compliant")
    assert before["call"]["actual_state"] == after["call"]["actual_state"]
    assert after["call"]["output"] == cases["original"]["native_packets"][1]["call"]["output"]
    assert not row["same_entire_probability_output"]
    fits = [item for item in row["fit_call_ledger"] if item["api"] == "calibrator.fit"]
    assert [item["members"] for item in fits] == [list(range(40, 120)), list(range(80, 160))]


def test_actual_transform_and_query_relative_omission(cases):
    assert cases["missing_preprocessing"]["captured_answers"] == {
        "authorized_state": "unknown",
        "disjoint_membership": "compliant",
        "sigmoid_arithmetic": "unknown",
    }
    row = cases["missing_preprocessing_with_transformed"]
    assert row["captured_answers"] == {
        "authorized_state": "unknown",
        "disjoint_membership": "compliant",
        "sigmoid_arithmetic": "compliant",
    }
    call = row["captured_packet"]["call"]
    assert call["transform_return"]["input"] == call["input"]
    assert call["transform_return"]["token"] == call["token"]
    for expected, observed in zip(arithmetic(call), call["output"], strict=True):
        assert expected == pytest.approx(observed, rel=1e-10, abs=1e-12)
    assert cases["missing_transformed"]["captured_answers"]["sigmoid_arithmetic"] == "compliant"


def test_instance_transform_hook_restores_and_does_not_recompute():
    session = setup(4101)
    scaler = session["base"].named_steps["scale"]
    native_transform = scaler.transform
    returned = []

    def observed_transform(*args, **kwargs):
        result = native_transform(*args, **kwargs)
        returned.append(result.tolist())
        return result

    with patch.object(scaler, "transform", observed_transform):
        packet = predict(session, "actual")
        assert scaler.transform is observed_transform
    assert "transform" not in scaler.__dict__
    assert len(returned) == 1
    assert packet["call"]["transform_return"]["output"] == returned[0]


def test_source_rejects_cache_and_nondense_finite_footprint():
    session = setup(4101)
    base = session["base"]
    base.memory = "owned-cache"
    with pytest.raises(ValueError, match="memory=None"):
        state(base)
    base.memory = None
    base.named_steps["scale"].scale_[0] = float("nan")
    with pytest.raises(ValueError, match="preprocessing"):
        state(base)


@pytest.mark.parametrize(
    "key,value", [("token", "foreign"), ("input", [[0] * 6]), ("held_scaler_is_enrolled", False)]
)
def test_transform_misassociation_remains_conflict(cases, key, value):
    packet = copy.deepcopy(cases["original"]["captured_packet"])
    packet["call"]["transform_return"][key] = value
    assert assess(packet)["sigmoid_arithmetic"] == "conflict"


def test_contrary_transform_and_state_not_silently_selected(cases):
    packet = copy.deepcopy(cases["original"]["captured_packet"])
    packet["call"]["transform_return"]["output"][0][0] += 1
    assert assess(packet)["sigmoid_arithmetic"] == "conflict"
    assert assess(packet)["authorized_state"] == "compliant"


def test_known_classifier_violation_survives_missing_preprocessing(cases):
    packet = copy.deepcopy(cases["original"]["captured_packet"])
    packet["call"]["actual_state"]["preprocessing"] = None
    packet["call"]["actual_state"]["classifier"]["coef"][0][0] += 1
    assert assess(packet)["authorized_state"] == "violation"


def test_scalar_reference_is_independent_and_binds_fit_arrays(cases, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("candidate evaluation is not a history reference")

    monkeypatch.setattr(pipeline_audit_source, "assess", forbidden)
    monkeypatch.setattr(pipeline_audit_source, "arithmetic", forbidden)
    row = cases["original"]
    assert history_reference(row) == dict.fromkeys(QUERIES, "compliant")
    rebound = copy.deepcopy(row)
    rebound["fit_call_ledger"][0]["input"][0] = rebound["fit_call_ledger"][0]["input"][1]
    with pytest.raises(ValueError, match="fit history"):
        history_reference(rebound)


def test_failure_membership_and_old_schema_contract(cases):
    failed = cases["native_failure"]
    assert set(failed["captured_answers"].values()) == {"unknown"}
    assert failed["native_failed_predictions"] == 1
    assert historical_membership(failed) == "compliant"
    assert (
        cases["wrong_probability_report"]["ordinary_complete_history"]["sigmoid_arithmetic"]
        == "violation"
    )
    with pytest.raises(ValueError, match="unsupported calibration"):
        calibration_audit_source.assess(cases["original"]["captured_packet"])
    old = copy.deepcopy(cases["original"]["captured_packet"])
    old["schema"] = "calibration-native-evidence/v1"
    with pytest.raises(ValueError, match="unsupported Pipeline"):
        assess(old)
