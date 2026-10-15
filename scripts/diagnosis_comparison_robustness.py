#!/usr/bin/env python3
"""Reproduce secondary measurement robustness without provider or judge execution."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from diagnosis_main_measurement_impact import _build_summary as audit_summary
from diagnosis_main_measurement_impact import _sha

from aletheia_lab.diagnosis.study_artifacts import checked_artifact_path, checked_output
from aletheia_lab.diagnosis.study_artifacts import parse_artifact as _json
from aletheia_lab.diagnosis.study_artifacts import read_artifact_bytes as _read
from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_comparison_robustness import (
    affine_weight_profile,
    automatic_label_stress,
    conditional_label_bounds,
    metric_semantics,
    pilot_reference_scenarios,
)
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import (
    classify_output,
    decompose_locked_runs,
)
from aletheia_lab.evaluation.diagnosis_main_measurement_impact import (
    ImpactFrame,
    _build_frame,
    _read_human_audit,
)
from aletheia_lab.evaluation.diagnosis_main_paired_pilot import (
    _estimate,
    _read_submission,
    analyze_pilot,
    select_families,
)


def _paths(repo: Path, memory: Path, first: Path, second: Path) -> dict[str, Path]:
    pilot = memory / "p5-family-paired-impact-pilot-v1"
    human = memory / "p5-domain-human-audit-v1"
    final = pilot / "analysis-final"
    return {
        "plan": repo / "configs/evaluation/diagnosis_main_analysis_plan_v3.json",
        "census": memory / "diagnosis-main-p4-census-v1/private-census-packet.json",
        "original_input": memory / "diagnosis-main-registered-run-v1/analysis-input.json",
        "original_report": memory / "diagnosis-main-registered-run-v1/analysis-report.json",
        "recovery_input": memory / "diagnosis-main-recovery-scoring-v1/analysis-input.json",
        "recovery_report": memory / "diagnosis-main-recovery-analysis-v1/analysis-report.json",
        "scoring_receipt": memory / "diagnosis-main-recovery-scoring-v1/receipt.json",
        "audit_receipt": human / "adjudication-v1/analysis/analysis-receipt.json",
        "audit_rows": human / "adjudication-v1/analysis/private-item-level.json",
        "audit_map": human / "output/coordinator-only/sample-map.json",
        "audit_report": human / "adjudication-v1/analysis/private-report.json",
        "pilot_receipt": pilot / "receipt.json",
        "coordinator": pilot / "coordinator-only.json",
        "preliminary": pilot / "analysis-pre-adjudication/preliminary-analysis.json",
        "adjudication_packet": pilot / "analysis-pre-adjudication/adjudication-24-claims.zip",
        "final_summary": final / "summary.json",
        "final_labels": final / "human-final-labels.json",
        "adjudication": final / "received-adjudication.json",
        "first_submission": first,
        "second_submission": second,
    }


def _verify_pilot_bytes(raw: dict[str, bytes], data: dict[str, Any]) -> None:
    coord, pre, final = (data[key] for key in ("coordinator", "preliminary", "final_summary"))
    coordinator_hash = _sha(raw["coordinator"])
    if not (
        coordinator_hash
        == data["pilot_receipt"]["coordinator_sha256"]
        == pre["coordinator_byte_sha256"]
        == final["input_byte_sha256"]["coordinator"]
        and _sha(raw["adjudication_packet"])
        == pre["adjudication_packet_sha256"]
        == final["adjudication_packet_sha256"]
        and _sha(raw["final_labels"]) == final["human_final_labels_byte_sha256"]
        and _sha(raw["adjudication"]) == final["input_byte_sha256"]["blind_adjudication"]
    ):
        raise ValueError("pilot inputs differ from locked receipts")
    for slot, name in (("rater_1", "first_submission"), ("rater_2", "second_submission")):
        if not (
            _sha(raw[name])
            == pre["raw_submission_byte_sha256"][slot]
            == final["input_byte_sha256"][name]
        ):
            raise ValueError("human submission differs from locked receipts")
    for source, name in (
        ("analysis_input_byte_sha256", "recovery_input"),
        ("analysis_report_byte_sha256", "recovery_report"),
        ("private_census_byte_sha256", "census"),
        ("scoring_receipt_byte_sha256", "scoring_receipt"),
    ):
        if not (
            _sha(raw[name])
            == coord["source_byte_sha256"][source]
            == pre["source_byte_sha256"][source]
            == final["source_byte_sha256"][source]
        ):
            raise ValueError("pilot source differs from locked recovery")


def _verify_one_output(frame: ImpactFrame, output: dict[str, Any], category: str) -> None:
    cell = output["family_id"], output["condition"], output["arm"]
    request = frame.requests_by_cell[cell]
    claims = frame.output_claims[request] if category == "claim_scored" else set()
    if (
        output["category"] != category
        or output["request_id"] != request
        or set(output["claim_ids"]) != claims
        or len(output["claim_ids"]) != len(claims)
    ):
        raise ValueError("pilot claim source changed")
    for weight, name in ((0.0, "automatic_primary_loss"), (0.5, "automatic_partial_half_loss")):
        if not math.isclose(
            output[name], frame.output_losses[cell][weight], rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("pilot cached loss differs from frozen output")
    if claims and not all(frame.units[c].scored_output for c in claims):
        raise ValueError("pilot scoreability differs from frozen output")


def _verify_pilot_outputs(
    frame: ImpactFrame,
    coordinator: dict[str, Any],
    categories: dict[str, str],
) -> None:
    selected = select_families(
        frame.family_ids, bytes.fromhex(coordinator["sampling_seed_hex_coordinator_only"])
    )
    if (
        tuple(coordinator["selected_family_ids"]) != selected
        or tuple(coordinator["population_family_ids"]) != frame.family_ids
        or coordinator["family_selection"] != "equal_probability_srswor_without_replacement"
    ):
        raise ValueError("pilot selection differs from its frozen probability draw")
    expected = {key for key in frame.output_losses if key[0] in selected}
    observed: set[tuple[str, str, str]] = set()
    for output in coordinator["outputs"]:
        cell = output["family_id"], output["condition"], output["arm"]
        if cell not in expected or cell in observed:
            raise ValueError("pilot output census changed")
        observed.add(cell)
        _verify_one_output(frame, output, categories[frame.requests_by_cell[cell]])
    if observed != expected:
        raise ValueError("pilot output census is incomplete")
    actual = {row["claim_id"]: row for row in coordinator["claims"]}
    expected_claims = {c for output in coordinator["outputs"] for c in output["claim_ids"]}
    if len(actual) != len(coordinator["claims"]) or set(actual) != expected_claims:
        raise ValueError("pilot automatic claim census changed")
    for claim, row in actual.items():
        unit = frame.units[claim]
        if row["automatic_label"] != unit.auto_label or row["request_id"] != unit.request_id:
            raise ValueError("pilot automatic label differs from frozen source")


def _pilot_labels(
    data: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, Any]]:
    coord = data["coordinator"]
    result = analyze_pilot(
        coord, data["first_submission"], data["second_submission"], data["adjudication"]
    )
    first = _read_submission(data["first_submission"], coord, "rater_1")
    second = _read_submission(data["second_submission"], coord, "rater_2")
    mapping = {
        row["blind_claim_id"]: row["source_claim_id"]
        for row in coord["mapping"]
        if row["slot"] == "rater_1"
    }
    resolved = {
        mapping[row["blind_claim_id"]]: row["support_label"]
        for row in data["adjudication"]["decisions"]
    }
    final = {claim: resolved.get(claim, label) for claim, label in first.items()}
    stored = {
        row["source_claim_id"]: row["human_final_label"] for row in data["final_labels"]["rows"]
    }
    if len(stored) != len(data["final_labels"]["rows"]) or stored != final:
        raise ValueError("final labels differ from returned agreement and adjudication")
    for name in ("primary", "partial_half_sensitivity"):
        for key, value in result[name].items():
            if data["final_summary"][name][key] != value:
                raise ValueError("recomputed pilot estimate differs from locked analysis")
    return first, second, final, result


def _endpoints(coordinator: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    primary, half = (_estimate(coordinator, labels, weight) for weight in (0.0, 0.5))
    return {
        "primary": primary,
        "partial_half": half,
        "partial_weight_profile": affine_weight_profile(
            primary["pilot_estimated_human_relabel_effect"],
            half["pilot_estimated_human_relabel_effect"],
        ),
    }


def _analysis(
    frame: ImpactFrame,
    audit_labels: dict[str, str],
    data: dict[str, Any],
    categories: dict[str, str],
) -> dict[str, Any]:
    coord = data["coordinator"]
    _verify_pilot_outputs(frame, coord, categories)
    automatic = metric_semantics(frame)["automatic_mean_claim_harm_contrast"]
    half = math.fsum(
        (1 if arm == "B1" else -1) * losses[0.5]
        for (_, _, arm), losses in frame.output_losses.items()
    ) / (len(frame.family_ids) * 3)
    for name, value in (
        ("frozen_primary_B1_minus_A3", automatic),
        ("frozen_partial_half_B1_minus_A3", half),
    ):
        if not math.isclose(coord[name], value, rel_tol=0, abs_tol=1e-12):
            raise ValueError("pilot baseline differs from full frozen family frame")
    first, second, final, pilot = _pilot_labels(data)
    overlap = audit_labels.keys() & final.keys()
    result: dict[str, Any] = {
        "schema_version": "diagnosis-comparison-robustness/v1",
        "status": "secondary_offline_analysis_complete",
        "provider_calls": 0,
        "frozen_outcomes_mutated": False,
        "new_human_labels_created": False,
        "finite_family_count": len(frame.family_ids),
        "pilot_references": {
            name: _endpoints(coord, labels)
            for name, labels in (("rater_1", first), ("rater_2", second), ("locked_final", final))
        },
        "pilot_disagreement_count": pilot["disagreement_count"],
        "reference_overlap": {
            "returned_audit_claims": len(audit_labels),
            "returned_pilot_claims": len(final),
            "shared_claims": len(overlap),
            "unique_claims": len(audit_labels.keys() | final.keys()),
            "label_conflicts": sum(audit_labels[c] != final[c] for c in overlap),
            "conflicts_adjudicated_by_this_analysis": False,
            "sampling_designs_pooled": False,
        },
        "automatic_partial_weight_profile": affine_weight_profile(
            coord["frozen_primary_B1_minus_A3"], coord["frozen_partial_half_B1_minus_A3"]
        ),
        "supplementary_metric_semantics": metric_semantics(frame),
        "interpretation": "Fixed finite-cache evidence-warrant measurement, not causal diagnosis correctness, new primary or confirmatory inference.",
    }
    result["conditional_regions"] = {
        str(weight): {
            name: conditional_label_bounds(frame, refs, weight)
            for name, refs in (
                ("historical_audit", (audit_labels,)),
                ("paired_pilot", (final,)),
                ("reference_union", (audit_labels, final)),
            )
        }
        for weight in (0.0, 0.5)
    }
    result["pilot_original_rater_scenarios"] = {
        str(weight): pilot_reference_scenarios(
            frame, coord["selected_family_ids"], first, second, weight
        )
        for weight in (0.0, 0.5)
    }
    result["hypothetical_automatic_label_stress"] = {
        str(weight): {
            str(target): automatic_label_stress(frame, weight, target, strict=target == 0)
            for target in (0.0, 0.05)
        }
        for weight in (0.0, 0.5)
    }
    return result


def build_summary(repo: Path, memory: Path, first: Path, second: Path) -> dict[str, Any]:
    paths = _paths(repo, memory, first, second)
    raw = {name: _read(path) for name, path in paths.items()}
    data = {name: _json(body) for name, body in raw.items() if name != "adjudication_packet"}
    _verify_pilot_bytes(raw, data)
    historical = audit_summary(repo, memory)
    plan = DiagnosisMainAnalysisPlan.model_validate_json(raw["plan"])
    census = DiagnosisMainAnalysisCensus.model_validate_json(
        json.dumps(data["census"]["analysis_census"])
    )
    recovery = DiagnosisMainAnalysisInput.model_validate_json(raw["recovery_input"])
    recovery_report = DiagnosisMainAnalysisReport.model_validate_json(raw["recovery_report"])
    frame = _build_frame(plan, census, recovery, recovery_report)
    audit = _read_human_audit(
        frame, data["audit_rows"]["rows"], data["audit_map"], data["audit_report"]
    )
    coord = data["coordinator"]
    if (
        coord["analysis_plan_sha256"] != plan.plan_sha256
        or coord["census_sha256"] != census.census_sha256
        or coord["recovery_input_sha256"] != recovery.input_sha256
        or coord["recovery_report_sha256"] != recovery_report.report_sha256
    ):
        raise ValueError("pilot contract identities differ from recovery")
    audit_labels = {claim: row["human_final_label"] for claim, row in audit.known.items()}
    records = {record.request_id: record for record in recovery.records}
    categories = {
        request: classify_output(records[request], condition, plan.partial_support_primary_weight)[
            0
        ]
        for (_, condition, _), request in frame.requests_by_cell.items()
    }
    result = _analysis(frame, audit_labels, data, categories)
    result["historical_probability_audit"] = {
        name: historical[name]
        for name in ("sample", "primary_B1_minus_A3", "prespecified_partial_half_B1_minus_A3")
    }
    result["unchanged_run_decomposition"] = decompose_locked_runs(
        plan=plan,
        census=census,
        original_input=DiagnosisMainAnalysisInput.model_validate_json(raw["original_input"]),
        original_report=DiagnosisMainAnalysisReport.model_validate_json(raw["original_report"]),
        recovery_input=recovery,
        recovery_report=recovery_report,
    )
    if any(_read(paths[name]) != body for name, body in raw.items()):
        raise ValueError("an immutable input changed during analysis")
    result["source_byte_sha256"] = {name: _sha(body) for name, body in raw.items()}
    result["inputs_unchanged_after_analysis"] = True
    return result


def write_private_summary(repo: Path, output: Path, summary: dict[str, Any]) -> str:
    output = checked_output(output, repo)
    parent = checked_artifact_path(output.parent).resolve(strict=True)
    encoded = (json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(parent / output.name, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return _sha(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--first-submission", type=Path, required=True)
    parser.add_argument("--second-submission", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        repo = Path(__file__).resolve().parents[1]
        memory = checked_artifact_path(args.memory_root).resolve(strict=True)
        if memory.is_relative_to(checked_artifact_path(repo)):
            raise ValueError("private inputs must remain outside the repository")
        summary = build_summary(repo, memory, args.first_submission, args.second_submission)
        digest = write_private_summary(repo, args.output, summary)
        print(
            json.dumps(
                {
                    "status": summary["status"],
                    "private_summary_sha256": digest,
                    "inputs_unchanged": True,
                    "provider_calls": 0,
                }
            )
        )
        return 0
    except (OSError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
