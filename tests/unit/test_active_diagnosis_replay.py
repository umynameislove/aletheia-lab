"""Synthetic offline integrity checks, not new scientific study observations."""

from __future__ import annotations

import copy
from itertools import product

import pytest

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.diagnosis.active_replay import rebuild_saved_study
from aletheia_lab.diagnosis.active_study import study_design, summarize


def _saved():
    design = study_design()
    frame = design["development"]
    rows = [
        dict(
            zip(
                ("source", "seed", "condition", "capability", "envelope", "policy"),
                cell,
                strict=True,
            )
        )
        | {"status": "qualification_failure", "error_type": "OSError"}
        for cell in product(
            [frame["source"]],
            frame["seeds"],
            frame["conditions"],
            design["capabilities"],
            design["envelopes"],
            design["policies"],
        )
    ]
    return {
        "phase": "development",
        "design_sha256": canonical_sha256(design),
        "code_sha256": dict.fromkeys(("native", "policy", "study", "entrypoint"), "a" * 64),
        "calibration_sha256": "b" * 64,
        "historical_runs_executed": False,
        "study": {
            "phase": "development",
            "planned": 200,
            "rows": rows,
            "worlds": [],
            "summary": summarize(rows),
        },
    }


def _executed():
    saved = _saved()
    reference = {
        "source": "iris",
        "seed": 42001,
        "condition": "healthy",
        "clean_loss": 0.1,
        "fault_loss": 0.1,
        "harmful": False,
        "probability_sha256": "e" * 64,
        "actual_state_sha256": dict.fromkeys(("input", "model", "training"), "c" * 64),
        "enrolled_state_sha256": dict.fromkeys(("input", "model", "training"), "c" * 64),
        "actual_changes": dict.fromkeys(("input", "model", "training"), False),
        "sites": [],
        "expected_sites": [],
        "actual_applied": False,
        "fidelity_verified": True,
        "harmful_loss_delta": 0.0,
        "scope": "controlled_snapshot_not_global_cause_uniqueness",
        "source_sha256": "d" * 64,
        "split_sha256": "f" * 64,
        "train_rows": 10,
        "evaluation_rows": 4,
        "feature_count": 2,
    }
    saved["study"]["worlds"] = [{"reference": reference, "setup_seconds": 0.01}]
    for row in saved["study"]["rows"]:
        if (row["seed"], row["condition"]) != (42001, "healthy"):
            continue
        row.pop("error_type")
        row.update(
            status="executed",
            episode_seconds=0.0002,
            initial={
                "loss": 0.1,
                "clean_loss": 0.1,
                "harmful": False,
                "probability_sha256": "e" * 64,
            },
            result={
                "scope": "catalogue_relative_not_global_cause_uniqueness",
                "policy": row["policy"],
                "observations": {},
                "compatible": ["healthy", "double_sign"],
                "sites": ["input+resident", "none"],
                "identified": False,
                "stop": "static" if row["policy"] == "static" else "budget_or_capability_exhausted",
                "ledger": [],
                "repair": {"action": None, "basis": "unresolved", "status": "not_attempted"},
                "total_seconds": 0.0001,
                "probe_seconds": 0.0,
                "selection_seconds": 0.0,
                "budget_seconds": 1.0,
                "on_budget": True,
            },
            raw_probe_evidence=[],
            grade={
                "in_catalogue": True,
                "exact_mechanism_match": False,
                "catalogue_specificity_failure": False,
                "supported_catalogue_identification": False,
                "functional_recovery": False,
                "intended_state_recovery": False,
                "fulfilled_recovery": False,
            },
        )
    saved["study"]["summary"] = summarize(saved["study"]["rows"])
    return saved


def test_offline_replay_preserves_complete_failure_denominators_without_execution(monkeypatch):
    from aletheia_lab.diagnosis import active_study

    monkeypatch.setattr(
        active_study, "create_case", lambda *_: pytest.fail("replay executed native")
    )
    report = _saved()
    original = copy.deepcopy(report)
    summary = rebuild_saved_study(report)
    assert report == original
    assert sum(r["planned"] for r in summary) == 200
    assert sum(r["qualification_failure"] for r in summary) == 200


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "foreign",
        "planned",
        "world",
        "phase",
        "digest",
        "historical",
        "status",
    ],
)
def test_recomputed_aggregate_cannot_hide_changed_census(mutation):
    report = _saved()
    study = report["study"]
    if mutation == "missing":
        study["rows"].pop()
    elif mutation == "duplicate":
        study["rows"][-1] = copy.deepcopy(study["rows"][0])
    elif mutation == "foreign":
        study["rows"][0]["source"] = "other"
    elif mutation == "planned":
        study["planned"] = 199
    elif mutation == "world":
        study["rows"][0]["status"] = "executed"
    elif mutation == "phase":
        report["phase"] = "analyze"
    elif mutation == "digest":
        report["code_sha256"]["native"] = "not-a-digest"
    elif mutation == "historical":
        report["historical_runs_executed"] = 0
    else:
        study["rows"][0]["status"] = "not_executed"
    if mutation not in {"world", "status"}:
        study["summary"] = summarize(study["rows"])
    with pytest.raises((ValueError, KeyError)):
        rebuild_saved_study(report)


def test_qualified_world_and_non_recovery_are_valid_without_native_execution():
    report = _executed()
    summary = rebuild_saved_study(report)
    assert sum(r["executed"] for r in summary) == 20
    assert sum(r["functional_recovery"] for r in summary) == 0


@pytest.mark.parametrize(
    "mutation",
    ["grade", "boolean", "compatible", "observations", "repair", "budget", "clock", "extra_proof"],
)
def test_rehashed_summary_does_not_authorize_unobserved_success(mutation):
    report = _executed()
    row = next(r for r in report["study"]["rows"] if r["status"] == "executed")
    if mutation == "grade":
        row["grade"]["functional_recovery"] = True
    elif mutation == "boolean":
        row["result"]["identified"] = 0
    elif mutation == "compatible":
        row["result"]["compatible"] = ["healthy"]
    elif mutation == "observations":
        row["result"]["observations"] = {"read_input": "same"}
    elif mutation == "repair":
        row["result"]["repair"]["state_restored"] = True
    elif mutation == "budget":
        row["result"]["budget_seconds"] = 0
    elif mutation == "clock":
        row["result"]["probe_seconds"] = 1.0
    else:
        row["raw_probe_evidence"] = [{"action": "read_input"}]
    report["study"]["summary"] = summarize(report["study"]["rows"])
    with pytest.raises(ValueError):
        rebuild_saved_study(report)


@pytest.mark.parametrize(
    "failure,expected", [("selector_failure", 0), ("invalid_model_action", 0), ("probe_failure", 2)]
)
def test_native_attempt_counter_distinguishes_selection_and_failed_repair(failure, expected):
    report = _executed()
    row = next(r for r in report["study"]["rows"] if r["status"] == "executed")
    if failure != "probe_failure":
        row["result"]["ledger"] = [{"action": None, "status": failure}]
    else:
        row["result"]["ledger"] = [{"action": "read_input", "status": "observed"}]
        row["result"]["repair"] = {"action": "restore_input", "status": "probe_failure"}
    summary = summarize([row])
    assert summary[0]["probe_calls"] == expected
