"""Offline consistency checks for saved ordinary active-diagnosis executions.

These checks bind a complete census to retained observations, not a hostile-host
attestation or a new numerical experiment. A trusted input byte pin is separate.
"""

from __future__ import annotations

import math
import re
from itertools import product
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.diagnosis.active_native import PROBES
from aletheia_lab.diagnosis.active_policy import declared_catalogue
from aletheia_lab.diagnosis.active_study import study_design, summarize

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BAD_STOPS = {"selector_failure", "probe_failure", "invalid_model_action", "outside_declared_model"}
_STOPS = _BAD_STOPS | {
    "static",
    "call_cap",
    "catalogue_identified",
    "budget_or_capability_exhausted",
    "no_discriminating_probe",
    "model_stop",
    "selection_exhausted_budget",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _number(value: Any) -> float:
    _require(type(value) in {int, float} and math.isfinite(value) and value >= 0, "invalid timer")
    return float(value)


def _flag(value: Any) -> bool:
    _require(type(value) is bool, "invalid boolean")
    return bool(value)


def _digest(value: Any) -> None:
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None, "invalid digest")


def _proof(event: dict[str, Any], proof: dict[str, Any], reference: dict[str, Any]) -> None:
    _digest(event["evidence_sha256"])
    _require(canonical_sha256(proof) == event["evidence_sha256"], "proof digest differs")
    _require(
        proof["action"] == event["action"] and proof["value"] == event["value"],
        "proof projection differs",
    )
    action = event["action"]
    if action.startswith("read_"):
        site = {"read_input": "input", "read_model": "model", "read_training": "training"}[action]
        actual = reference["actual_state_sha256"][site]
        enrolled = reference["enrolled_state_sha256"][site]
        _require(
            proof["actual_sha256"] == actual and proof["enrolled_sha256"] == enrolled,
            "read state differs",
        )
        _require(
            proof["value"] == ("same" if actual == enrolled else "changed"), "read value differs"
        )
    else:
        restored = abs(proof["loss"] - proof["clean_loss"]) <= 1e-10
        _require(proof["clean_loss"] == reference["clean_loss"], "repair reference differs")
        _require(
            proof["value"] == ("restored" if restored else "not_restored"), "repair value differs"
        )
        _require(_flag(proof["functional_restored"]) == restored, "functional repair differs")
        _require(
            _flag(proof["state_restored"])
            == (proof["actual_state_sha256"] == proof["enrolled_state_sha256"]),
            "state repair differs",
        )
        _require(
            proof["enrolled_state_sha256"] == reference["enrolled_state_sha256"],
            "repair enrollment differs",
        )
    _require(
        _number(event["seconds"]) <= _number(event["attempt_seconds"]),
        "operation timer exceeds attempt",
    )


def _reference(reference: dict[str, Any]) -> None:
    expected = {
        "healthy": [],
        "input_sign": ["input"],
        "resident_sign": ["model"],
        "target_roll": ["model", "training"],
        "double_sign": ["input", "model"],
        "input_permutation": ["input"],
        "resident_permutation": ["model"],
        "intercept_offset": ["model"],
    }[reference["condition"]]
    _require(
        reference["scope"] == "controlled_snapshot_not_global_cause_uniqueness",
        "world scope differs",
    )
    for name in ("source_sha256", "split_sha256", "probability_sha256"):
        _digest(reference[name])
    for state in (reference["actual_state_sha256"], reference["enrolled_state_sha256"]):
        _require(set(state) == {"input", "model", "training"}, "world state census differs")
        for digest in state.values():
            _digest(digest)
    changes = {
        site: reference["actual_state_sha256"][site] != reference["enrolled_state_sha256"][site]
        for site in ("input", "model", "training")
    }
    _require(
        all(type(v) is bool for v in reference["actual_changes"].values())
        and reference["actual_changes"] == changes,
        "actual state changes differ",
    )
    sites = sorted(site for site, changed in changes.items() if changed)
    _require(
        reference["expected_sites"] == expected and reference["sites"] == sites == expected,
        "intervention sites differ",
    )
    _require(
        _flag(reference["fidelity_verified"])
        and _flag(reference["actual_applied"]) == bool(expected),
        "world qualification differs",
    )
    delta = _number(reference["fault_loss"]) - _number(reference["clean_loss"])
    _require(
        math.isclose(delta, reference["harmful_loss_delta"], rel_tol=0, abs_tol=1e-12),
        "world loss delta differs",
    )
    _require(_flag(reference["harmful"]) == (delta >= 0.01), "world harm differs")
    _require(
        all(
            type(reference[name]) is int and reference[name] > 0
            for name in ("train_rows", "evaluation_rows", "feature_count")
        ),
        "world dimensions differ",
    )


def _episode(row: dict[str, Any], reference: dict[str, Any], actions: list[str]) -> None:
    result, initial = row["result"], row["initial"]
    _require(isinstance(row["grade"], dict), "evaluator grade must be an object")
    _require(
        initial
        == {
            "loss": reference["fault_loss"],
            "clean_loss": reference["clean_loss"],
            "harmful": reference["harmful"],
            "probability_sha256": reference["probability_sha256"],
        },
        "initial observation differs",
    )
    _require(
        result["scope"] == "catalogue_relative_not_global_cause_uniqueness"
        and result["policy"] == row["policy"],
        "policy scope differs",
    )
    _require(result["stop"] in _STOPS, "invalid terminal stop")
    observations: dict[str, str] = {}
    observed: list[dict[str, Any]] = []
    attempts = 0.0
    failed = False
    for event in result["ledger"]:
        _require(not failed, "ledger continues after a terminal failure")
        status, action = event["status"], event["action"]
        if status in {"selector_failure", "invalid_model_action"}:
            _require(result["stop"] == status, "selector failure stop differs")
            failed = True
            continue
        _require(
            action in actions and action not in observations, "probe authority or duplicate differs"
        )
        _require(status in {"observed", "probe_failure"}, "unknown probe status")
        attempts += _number(event["attempt_seconds"])
        if status == "observed":
            observations[action] = event["value"]
            observed.append(event)
        else:
            _require(result["stop"] == "probe_failure", "probe failure stop differs")
            failed = True
    _require(
        result["stop"] not in {"selector_failure", "invalid_model_action", "probe_failure"}
        or failed,
        "failure stop lacks a retained attempt",
    )
    _require(
        (result["stop"] == "static") == (row["policy"] == "static"), "static policy stop differs"
    )
    _require(observations == result["observations"], "observations differ from ledger")
    hypotheses = [
        h
        for h in declared_catalogue()
        if h.harmful == _flag(initial["harmful"])
        and all(h.outcomes.get(a) == v for a, v in observations.items())
    ]
    names, sites = [h.name for h in hypotheses], sorted({h.site for h in hypotheses})
    _require(result["compatible"] == names and result["sites"] == sites, "compatibility differs")
    _require(result["stop"] != "static" or not result["ledger"], "static policy acquired evidence")
    _require(
        result["stop"] != "outside_declared_model" or not hypotheses,
        "outside-model stop has compatible hypotheses",
    )
    _require(
        result["stop"] != "catalogue_identified" or len(sites) == 1,
        "identified stop has multiple sites",
    )
    _require(
        result["stop"] != "call_cap" or len(result["ledger"]) == 6,
        "call-cap stop lacks six attempts",
    )
    identified = result["stop"] not in _BAD_STOPS and len(sites) == 1
    _require(_flag(result["identified"]) == identified, "identification differs")
    if not hypotheses:
        action, basis = None, "no_supported_recovery"
    elif len(sites) == 1:
        action, basis = (
            {"input": "restore_input", "resident": "restore_model", "training": "refit"}.get(
                sites[0]
            ),
            "diagnosis_selected",
        )
    else:
        common = [a for a in PROBES[3:] if all(h.outcomes[a] == "restored" for h in hypotheses)]
        action, basis = (
            (common[0], "common_functional_recovery") if common else (None, "unresolved")
        )
    repair = result["repair"]
    _require(repair["action"] == action and repair["basis"] == basis, "recovery decision differs")
    _require(
        repair["status"] in {"observed", "probe_failure", "not_attempted"},
        "invalid recovery status",
    )
    if repair["status"] != "not_attempted":
        _require(initial["harmful"] and action in actions, "recovery authority differs")
        attempts += _number(repair["attempt_seconds"])
    if repair["status"] == "observed":
        observed.append(repair)
    else:
        _require(
            not repair.get("functional_restored", False)
            and not repair.get("state_restored", False),
            "unobserved repair claims recovery",
        )
    _require(
        len(result["ledger"]) + int(repair["status"] != "not_attempted") <= 6,
        "episode call cap differs",
    )
    proofs = row["raw_probe_evidence"]
    _require(len(proofs) == len(observed), "raw proof census differs")
    for event, proof in zip(observed, proofs, strict=True):
        _proof(event, proof, reference)
    if repair["status"] == "observed":
        _require(
            _flag(repair["functional_restored"]) == proofs[-1]["functional_restored"]
            and _flag(repair["state_restored"]) == proofs[-1]["state_restored"],
            "recovery grade differs from proof",
        )
    total, budget = _number(result["total_seconds"]), _number(result["budget_seconds"])
    _require(_flag(result["on_budget"]) == (total <= budget), "deadline differs")
    _require(_number(row["episode_seconds"]) >= total, "episode timer is shorter")
    _require(
        math.isclose(_number(result["probe_seconds"]), attempts, rel_tol=0, abs_tol=1e-12),
        "attempt cost accounting differs",
    )
    _require(
        attempts + _number(result["selection_seconds"]) <= total + 1e-12,
        "nested costs exceed episode",
    )
    known = row["condition"] in {h.name for h in declared_catalogue()}
    exact = identified and names == [row["condition"]]
    expected_grade = {
        "in_catalogue": known,
        "exact_mechanism_match": exact,
        "catalogue_specificity_failure": identified and not exact,
        "supported_catalogue_identification": known and exact and result["on_budget"],
        "functional_recovery": repair.get("functional_restored", False),
        "intended_state_recovery": repair.get("state_restored", False),
        "fulfilled_recovery": repair.get("state_restored", False) and result["on_budget"],
    }
    _require(
        all(type(v) is bool for v in row["grade"].values()) and row["grade"] == expected_grade,
        "evaluator grade differs",
    )


def rebuild_saved_study(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Check saved ordinary executions without fitting, measuring or proposing."""
    design = study_design()
    phase = report["phase"]
    _require(phase in {"development", "transfer"}, "invalid saved execution phase")
    _require(report["design_sha256"] == canonical_sha256(design), "saved design differs")
    _require(report["historical_runs_executed"] is False, "historical execution flag differs")
    _require(
        isinstance(report["code_sha256"], dict)
        and set(report["code_sha256"]) == {"native", "policy", "study", "entrypoint"},
        "code binding census differs",
    )
    for digest in report["code_sha256"].values():
        _digest(digest)
    _digest(report["calibration_sha256"])
    frame, study = design[phase], report["study"]
    _require(study["phase"] == phase, "study phase differs")
    expected = set(
        product(
            [frame["source"]],
            frame["seeds"],
            frame["conditions"],
            design["capabilities"],
            design["envelopes"],
            design["policies"],
        )
    )
    observed = [
        (r["source"], r["seed"], r["condition"], r["capability"], r["envelope"], r["policy"])
        for r in study["rows"]
    ]
    _require(
        type(study["planned"]) is int and study["planned"] == len(expected),
        "planned census differs",
    )
    _require(
        len(observed) == len(set(observed)) and set(observed) == expected,
        "opportunity census differs",
    )
    worlds: dict[tuple[str, int, str], dict[str, Any]] = {}
    for world in study["worlds"]:
        reference = world["reference"]
        key = reference["source"], reference["seed"], reference["condition"]
        _require(
            key not in worlds and key in {cell[:3] for cell in expected}, "world census differs"
        )
        _reference(reference)
        _number(world["setup_seconds"])
        worlds[key] = reference
    used: set[tuple[str, int, str]] = set()
    failed: set[tuple[str, int, str]] = set()
    budgets: dict[str, float] = {}
    for row in study["rows"]:
        _require(type(row["seed"]) is int, "invalid seed")
        key = row["source"], row["seed"], row["condition"]
        if row["status"] == "qualification_failure":
            _require(
                key not in worlds
                and set(row)
                == {
                    "source",
                    "seed",
                    "condition",
                    "capability",
                    "envelope",
                    "policy",
                    "status",
                    "error_type",
                },
                "failed world contains execution",
            )
            failed.add(key)
        else:
            _require(row["status"] == "executed" and key in worlds, "unqualified execution")
            budget = _number(row["result"]["budget_seconds"])
            envelope = row["envelope"]
            _require(
                budget == budgets.setdefault(envelope, budget),
                "envelope budget differs across rows",
            )
            _require(
                budget == design["relaxed_seconds"] if envelope == "relaxed" else budget >= 0.001,
                "recorded budget differs from design envelope",
            )
            _episode(row, worlds[key], design["capabilities"][row["capability"]])
            used.add(key)
    _require(
        used == set(worlds) and used | failed == {cell[:3] for cell in expected},
        "world coverage differs",
    )
    summary = summarize(study["rows"])
    _require(summary == study["summary"], "stored aggregate differs from rebuilt rows")
    return summary
