"""Synthetic saved-proof contracts; no native fits or new study observations."""

from __future__ import annotations

import copy
import runpy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.diagnosis.active_replay import rebuild_saved_study
from aletheia_lab.diagnosis.active_study import summarize


def _fixtures() -> dict[str, Any]:
    # The sibling owns the evolving qualification metadata; neither helper runs
    # native operations. Reuse its shape rather than duplicate a full census.
    return runpy.run_path(str(Path(__file__).with_name("test_active_diagnosis_replay.py")))


@pytest.fixture(autouse=True)
def _no_native_or_provider_execution(monkeypatch):
    from aletheia_lab.diagnosis import active_local, active_native, active_study

    def forbidden(*_, **__):
        pytest.fail("saved-proof replay reached native or provider execution")

    for module, name in (
        (active_native, "create_case"),
        (active_native, "_fit"),
        (active_native.NativeCase, "probe"),
        (active_study, "create_case"),
        (active_study, "run_study"),
        (active_study, "calibrate_costs"),
        (active_local.LocalActionSelector, "__call__"),
    ):
        monkeypatch.setattr(module, name, forbidden)


def _row(report: dict[str, Any], policy: str = "ec2") -> dict[str, Any]:
    return next(
        row
        for row in report["study"]["rows"]
        if row["status"] == "executed"
        and row["capability"] == "all"
        and row["envelope"] == "relaxed"
        and row["policy"] == policy
    )


def _refresh_summary(report: dict[str, Any]) -> None:
    report["study"]["summary"] = summarize(report["study"]["rows"])


def _receipt(proof: dict[str, Any], attempt_seconds: float) -> dict[str, Any]:
    return {
        "action": proof["action"],
        "status": "observed",
        "value": proof["value"],
        "seconds": attempt_seconds / 2,
        "attempt_seconds": attempt_seconds,
        "evidence_sha256": canonical_sha256(proof),
    }


def _qualified_input_fault() -> dict[str, Any]:
    fixtures = _fixtures()
    report = fixtures["_saved"]()
    healthy = fixtures["_executed"]()
    reference = copy.deepcopy(healthy["study"]["worlds"][0]["reference"])
    reference.update(
        condition="input_sign",
        fault_loss=0.2,
        harmful=True,
        harmful_loss_delta=0.1,
        sites=["input"],
        expected_sites=["input"],
        actual_changes={"input": True, "model": False, "training": False},
        actual_applied=True,
        fidelity_verified=True,
    )
    reference["actual_state_sha256"]["input"] = "a" * 64
    report["study"]["worlds"] = [{"reference": reference, "setup_seconds": 0.01}]
    template = _row(healthy)
    for row in report["study"]["rows"]:
        if (row["seed"], row["condition"]) != (42001, "input_sign"):
            continue
        # Keep the complete planned identities, changing only the saved episode.
        identities = {
            key: row[key]
            for key in ("source", "seed", "condition", "capability", "envelope", "policy")
        }
        row.clear()
        row.update(copy.deepcopy(template), **identities)
        row["initial"].update(loss=0.2, harmful=True)
        row["result"].update(
            policy=row["policy"],
            compatible=["input_sign", "resident_sign", "target_roll"],
            sites=["input", "resident", "training"],
            stop="static" if row["policy"] == "static" else "budget_or_capability_exhausted",
        )
    _refresh_summary(report)
    return report


def _observed_input_and_repair() -> dict[str, Any]:
    report = _qualified_input_fault()
    reference = report["study"]["worlds"][0]["reference"]
    row = _row(report)
    # Explicit synthetic proof fields used by the saved-evidence contract. The
    # digests bind these authored observations; they do not authenticate a host.
    read_proof = {
        "action": "read_input",
        "actual_sha256": reference["actual_state_sha256"]["input"],
        "enrolled_sha256": reference["enrolled_state_sha256"]["input"],
        "value": "changed",
    }
    repair_proof = {
        "action": "restore_input",
        "value": "restored",
        "loss": 0.1,
        "clean_loss": 0.1,
        "functional_restored": True,
        "state_restored": True,
        "actual_state_sha256": copy.deepcopy(reference["enrolled_state_sha256"]),
        "enrolled_state_sha256": copy.deepcopy(reference["enrolled_state_sha256"]),
    }
    row["raw_probe_evidence"] = [read_proof, repair_proof]
    row["episode_seconds"] = 0.01
    row["result"].update(
        observations={"read_input": "changed"},
        compatible=["input_sign"],
        sites=["input"],
        identified=True,
        stop="catalogue_identified",
        ledger=[_receipt(read_proof, 0.002)],
        repair={
            **_receipt(repair_proof, 0.005),
            "basis": "diagnosis_selected",
            "functional_restored": True,
            "state_restored": True,
        },
        total_seconds=0.009,
        probe_seconds=0.007,
        selection_seconds=0.001,
    )
    row["grade"].update(
        exact_mechanism_match=True,
        supported_catalogue_identification=True,
        functional_recovery=True,
        intended_state_recovery=True,
        fulfilled_recovery=True,
    )
    _refresh_summary(report)
    return report


def test_observed_read_and_repair_replay_without_execution_or_input_mutation():
    report = _observed_input_and_repair()
    before = copy.deepcopy(report)
    summary = rebuild_saved_study(report)
    assert report == before
    assert sum(group["planned"] for group in summary) == 200
    assert sum(group["executed"] for group in summary) == 20
    assert sum(group["supported_catalogue_identification"] for group in summary) == 1
    assert sum(group["functional_recovery"] for group in summary) == 1
    assert sum(group["intended_state_recovery"] for group in summary) == 1
    assert sum(group["fulfilled_recovery"] for group in summary) == 1
    assert sum(group["probe_calls"] for group in summary) == 2


@pytest.mark.parametrize(
    "mutation", ["projection", "read_state", "repair_loss", "repair_state", "repair_boolean"]
)
def test_rehashed_proof_cannot_change_the_observed_read_or_repair(mutation):
    report = _observed_input_and_repair()
    row = _row(report)
    index = 0 if mutation in {"projection", "read_state"} else 1
    proof = row["raw_probe_evidence"][index]
    if mutation == "projection":
        proof["value"] = "same"
    elif mutation == "read_state":
        proof["actual_sha256"] = proof["enrolled_sha256"]
    elif mutation == "repair_loss":
        proof["loss"] = 0.2
    elif mutation == "repair_state":
        proof["actual_state_sha256"]["input"] = "a" * 64
    else:
        proof["functional_restored"] = False
    event = row["result"]["ledger"][0] if index == 0 else row["result"]["repair"]
    event["evidence_sha256"] = canonical_sha256(proof)
    _refresh_summary(report)
    with pytest.raises(ValueError):
        rebuild_saved_study(report)


def test_static_policy_cannot_acquire_a_valid_observed_proof():
    report = _observed_input_and_repair()
    static = _row(report, policy="static")
    observed = _row(report)
    static["raw_probe_evidence"] = [copy.deepcopy(observed["raw_probe_evidence"][0])]
    static["result"].update(
        observations={"read_input": "changed"},
        compatible=["input_sign"],
        sites=["input"],
        identified=True,
        ledger=[copy.deepcopy(observed["result"]["ledger"][0])],
        repair={
            "action": "restore_input",
            "basis": "diagnosis_selected",
            "status": "not_attempted",
        },
        total_seconds=0.003,
        probe_seconds=0.002,
    )
    static["episode_seconds"] = 0.004
    static["grade"].update(exact_mechanism_match=True, supported_catalogue_identification=True)
    _refresh_summary(report)
    with pytest.raises(ValueError, match="static"):
        rebuild_saved_study(report)


@pytest.mark.parametrize("mutation", ["actual_changes", "expected_sites", "fidelity_verified"])
def test_reference_fidelity_cannot_contradict_the_saved_state_hashes(mutation):
    report = _fixtures()["_executed"]()
    reference = report["study"]["worlds"][0]["reference"]
    if mutation == "actual_changes":
        reference["actual_changes"]["input"] = True
    elif mutation == "expected_sites":
        reference["expected_sites"] = ["input"]
    else:
        reference["fidelity_verified"] = False
    _refresh_summary(report)
    with pytest.raises(ValueError):
        rebuild_saved_study(report)


def test_outside_model_stop_requires_an_empty_compatible_catalogue():
    report = _fixtures()["_executed"]()
    _row(report)["result"]["stop"] = "outside_declared_model"
    _refresh_summary(report)
    with pytest.raises(ValueError, match="outside-model"):
        rebuild_saved_study(report)


def _failed_repair() -> dict[str, Any]:
    report = _observed_input_and_repair()
    row = _row(report)
    row["raw_probe_evidence"].pop()
    row["result"]["repair"] = {
        "action": "restore_input",
        "basis": "diagnosis_selected",
        "status": "probe_failure",
        "error_type": "OSError",
        "attempt_seconds": 0.005,
    }
    row["grade"].update(
        functional_recovery=False, intended_state_recovery=False, fulfilled_recovery=False
    )
    _refresh_summary(report)
    return report


def test_failed_repair_preserves_its_attempt_cost_without_claiming_recovery():
    report = _failed_repair()
    summary = rebuild_saved_study(report)
    assert sum(group["probe_calls"] for group in summary) == 2
    assert sum(group["supported_catalogue_identification"] for group in summary) == 1
    assert sum(group["functional_recovery"] for group in summary) == 0
    assert sum(group["intended_state_recovery"] for group in summary) == 0
    assert sum(group["fulfilled_recovery"] for group in summary) == 0
    assert _row(report)["result"]["probe_seconds"] == 0.007


def test_failed_repair_cannot_omit_its_cost_from_the_saved_probe_total():
    report = _failed_repair()
    _row(report)["result"]["probe_seconds"] = 0.002
    _refresh_summary(report)
    with pytest.raises(ValueError, match="attempt cost"):
        rebuild_saved_study(report)
