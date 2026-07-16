"""Validator for the full set of P1 benchmark cases.

Beyond schema and checksum integrity, this cross-checks the four payloads of each
case against one another, checks observable signals against the injection
provenance, requires the three conditions of a setting to share identical
injection/ground-truth, and checks the whole 15-case set (single dataset/split
hash, severity ranks, condition counts, and the honest outcome composition).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.case_schema import (
    EVIDENCE_CONDITIONS,
    EXPECTED_BEHAVIOR,
    case_role_for,
    classify_outcome,
    expected_symptom_for,
    project_diagnosis_input,
)
from aletheia_lab.benchmark.case_writer import (
    LoadedCase,
    diagnosis_input_leakage,
    load_case_dir_schema_only,
    sha256_file,
)

EXPECTED_CASE_COUNT = 15
EXPECTED_SETTINGS = 5
EXPECTED_CASES_PER_CONDITION = 5
EXPECTED_SEVERITY_RANKS = {1, 2, 3, 4, 5}
# Honest, dataset-agnostic invariant: the set must contain at least one failure
# and at least one control, so it cannot be an all-regression set produced by
# selecting settings for a failing outcome. The exact composition for the real
# dataset (3 regression / 1 improvement / 1 stable) is asserted by the real-data
# integration test, not baked into this validator.
REQUIRED_ARTIFACTS = frozenset(
    {"manifest.json", "diagnosis_input.json", "ground_truth.json", "injection.json"}
)
_ALL_METRIC_SYMPTOMS = {"metric_regression", "metric_improvement", "metric_stable"}
_TOL = 1e-12


@dataclass
class ValidationReport:
    passed: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    leakage_total: int = 0
    case_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def record(self, name: str, ok: bool, error: str | None = None) -> None:
        self.checks[name] = ok
        if not ok:
            self.passed = False
            self.errors.append(error or name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "leakage_total": self.leakage_total,
            "case_count": len(self.case_ids),
            "errors": self.errors[:10],
        }


def _case_dirs(cases_dir: Path) -> list[Path]:
    return sorted(p for p in cases_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists())


def _cross_artifact_errors(case: LoadedCase, case_dir: Path) -> list[str]:
    m, inj, gt, di = case.manifest, case.injection, case.ground_truth, case.diagnosis_input
    cid = m.case_id
    errors: list[str] = []

    if m.fault_type != inj.fault_type:
        errors.append(f"{cid}: fault_type mismatch manifest vs injection")
    if m.injection_setting != m.injection_id:
        errors.append(f"{cid}: injection_setting != injection_id")
    if m.injection_id != inj.injection_id:
        errors.append(f"{cid}: injection_id mismatch manifest vs injection")
    if m.injection_seed != inj.seed:
        errors.append(f"{cid}: seed mismatch manifest vs injection")
    if m.dataset_sha256 != inj.dataset_sha256 or m.dataset_id != inj.dataset_id:
        errors.append(f"{cid}: dataset mismatch manifest vs injection")

    params = m.injection_parameters
    if (
        params.get("feature") != inj.feature
        or params.get("seed") != inj.seed
        or params.get("target_distribution") != inj.target_distribution
        or params.get("output_size") != inj.output_size
    ):
        errors.append(f"{cid}: injection parameters mismatch manifest vs injection")
    if gt.injection_parameters != params:
        errors.append(f"{cid}: ground_truth params != manifest params")
    if gt.cause_label != m.fault_type:
        errors.append(f"{cid}: ground_truth.cause_label != fault_type")

    # Outcome / role / symptom must be consistent with the measured delta.
    if gt.metric_outcome != classify_outcome(gt.metric_delta):
        errors.append(f"{cid}: metric_outcome does not match metric_delta")
    if gt.case_role != case_role_for(gt.metric_outcome):
        errors.append(f"{cid}: case_role does not match metric_outcome")
    right_symptom = expected_symptom_for(gt.metric_outcome)
    wrong_symptoms = (_ALL_METRIC_SYMPTOMS - {right_symptom}) & set(gt.expected_symptoms)
    if right_symptom not in gt.expected_symptoms or wrong_symptoms:
        errors.append(f"{cid}: expected_symptoms inconsistent with outcome")

    if di.model_dump() != project_diagnosis_input(m).model_dump():
        errors.append(f"{cid}: diagnosis_input is not the manifest projection")

    if m.observable_signals.candidate_feature != inj.feature:
        errors.append(f"{cid}: candidate_feature != injection.feature")
    if m.observable_signals.sample_size != inj.output_size:
        errors.append(f"{cid}: sample_size != injection.output_size")
    if m.expected_diagnosis_behavior != EXPECTED_BEHAVIOR.get(m.evidence_condition):
        errors.append(f"{cid}: expected_diagnosis_behavior does not match the condition contract")

    if m.ground_truth_ref != "ground_truth.json" or not (case_dir / m.ground_truth_ref).exists():
        errors.append(f"{cid}: ground_truth_ref missing or wrong")
    for ref in m.artifacts.values():
        if not (case_dir / ref).exists():
            errors.append(f"{cid}: artifact reference missing: {ref}")

    recorded = json.loads((case_dir / "checksums.json").read_text("utf-8"))
    if set(recorded) != REQUIRED_ARTIFACTS:
        errors.append(f"{cid}: checksums do not cover exactly the required artifacts")

    # Observable signals vs injection provenance, per condition.
    sig = m.observable_signals
    if m.evidence_condition in ("full", "noisy"):
        if sig.psi != inj.psi:
            errors.append(f"{cid}: observable.psi != injection.psi")
        if sig.distribution_reference != inj.reference_distribution:
            errors.append(f"{cid}: distribution_reference != injection.reference_distribution")
        if sig.distribution_observed != inj.achieved_distribution:
            errors.append(f"{cid}: distribution_observed != injection.achieved_distribution")
        if sig.baseline_metric_reference is None:
            errors.append(f"{cid}: {m.evidence_condition} is missing the metric comparison")
        elif abs(sig.baseline_metric_reference.delta - gt.metric_delta) > _TOL:
            errors.append(f"{cid}: metric delta != ground_truth.metric_delta")
    if m.evidence_condition == "missing_key" and (
        sig.distribution_reference is not None
        or sig.psi is not None
        or sig.baseline_metric_reference is not None
        or sig.distractor_comparisons
    ):
        errors.append(f"{cid}: missing_key does not withhold the decisive evidence")
    if m.evidence_condition == "noisy" and (
        not sig.distractor_comparisons or any(d.psi is None for d in sig.distractor_comparisons)
    ):
        errors.append(f"{cid}: noisy lacks a measured distractor comparison")
    if m.evidence_condition != "noisy" and sig.distractor_comparisons:
        errors.append(f"{cid}: distractor comparison present outside noisy")
    return errors


def _cross_condition_errors(by_setting: dict[str, list[tuple[Path, LoadedCase]]]) -> list[str]:
    errors: list[str] = []
    for injection_id, items in by_setting.items():
        conditions = [c.manifest.evidence_condition for _, c in items]
        if set(conditions) != set(EVIDENCE_CONDITIONS) or len(conditions) != len(
            EVIDENCE_CONDITIONS
        ):
            errors.append(f"{injection_id}: conditions are not exactly {set(EVIDENCE_CONDITIONS)}")
            continue
        inj_bytes = {(d / "injection.json").read_bytes() for d, _ in items}
        gt_bytes = {(d / "ground_truth.json").read_bytes() for d, _ in items}
        if len(inj_bytes) != 1:
            errors.append(f"{injection_id}: injection.json differs across conditions")
        if len(gt_bytes) != 1:
            errors.append(f"{injection_id}: ground_truth.json differs across conditions")
        if len({c.ground_truth.metric_outcome for _, c in items}) != 1:
            errors.append(f"{injection_id}: metric_outcome differs across conditions")
        if len({c.manifest.severity_rank for _, c in items}) != 1:
            errors.append(f"{injection_id}: severity_rank differs across conditions")
    return errors


def validate_p1_cases(cases_dir: str | Path) -> ValidationReport:
    """Validate a generated P1 case directory. Returns a structured report."""

    report = ValidationReport()
    base = Path(cases_dir)
    if not base.exists():
        report.record("cases_dir_exists", False, f"no such directory: {base}")
        return report

    dirs = _case_dirs(base)
    loaded: list[LoadedCase] = []
    for case_dir in dirs:
        try:
            loaded.append(load_case_dir_schema_only(case_dir))
        except Exception as exc:  # noqa: BLE001 - report any invalid case
            report.record(f"load:{case_dir.name}", False, f"invalid case {case_dir.name}: {exc}")
    if not report.passed:
        return report

    report.case_ids = [c.manifest.case_id for c in loaded]
    report.record(
        "exactly_15_cases",
        len(loaded) == EXPECTED_CASE_COUNT,
        f"expected {EXPECTED_CASE_COUNT} cases, got {len(loaded)}",
    )
    report.record(
        "unique_case_ids", len(set(report.case_ids)) == len(report.case_ids), "duplicate case_id"
    )
    report.record(
        "only_data_drift",
        all(c.manifest.fault_type == "data_drift" for c in loaded),
        "non data_drift fault_type present",
    )

    # Checksum integrity.
    integrity_ok = True
    for case_dir in dirs:
        recorded = json.loads((case_dir / "checksums.json").read_text("utf-8"))
        for name, digest in recorded.items():
            if sha256_file(case_dir / name) != digest:
                integrity_ok = False
    report.record("artifact_checksums_match", integrity_ok, "an artifact checksum mismatch")

    # Per-case cross-artifact consistency.
    cross_errors: list[str] = []
    for case, case_dir in zip(loaded, dirs, strict=True):
        cross_errors.extend(_cross_artifact_errors(case, case_dir))
    report.record(
        "cross_artifact_consistent",
        not cross_errors,
        "; ".join(cross_errors[:5]) if cross_errors else None,
    )

    # Cross-condition (three conditions of a setting share injection/ground_truth).
    by_setting: dict[str, list[tuple[Path, LoadedCase]]] = {}
    for case, case_dir in zip(loaded, dirs, strict=True):
        by_setting.setdefault(case.manifest.injection_id, []).append((case_dir, case))
    cc_errors = _cross_condition_errors(by_setting)
    report.record(
        "conditions_share_injection_and_ground_truth",
        not cc_errors,
        "; ".join(cc_errors[:5]) if cc_errors else None,
    )

    # Whole-set checks.
    report.record(
        "exactly_5_settings",
        len(by_setting) == EXPECTED_SETTINGS,
        f"expected {EXPECTED_SETTINGS} settings, got {len(by_setting)}",
    )
    report.record(
        "exact_conditions_per_setting",
        all(
            {c.manifest.evidence_condition for _, c in v} == set(EVIDENCE_CONDITIONS)
            for v in by_setting.values()
        ),
        "a setting does not have exactly {full, missing_key, noisy}",
    )

    per_condition = {c: 0 for c in EVIDENCE_CONDITIONS}
    for case in loaded:
        per_condition[case.manifest.evidence_condition] += 1
    report.record(
        "five_cases_per_condition",
        all(per_condition[c] == EXPECTED_CASES_PER_CONDITION for c in EVIDENCE_CONDITIONS),
        f"condition counts not 5/5/5: {per_condition}",
    )

    report.record(
        "single_dataset_hash",
        len({c.manifest.dataset_sha256 for c in loaded}) == 1,
        "inconsistent dataset hash",
    )
    report.record(
        "single_split_manifest_hash",
        len({c.manifest.split_manifest_sha256 for c in loaded}) == 1,
        "inconsistent split-manifest hash",
    )
    report.record(
        "severity_ranks_are_1_to_5",
        {c.manifest.severity_rank for c in loaded} == EXPECTED_SEVERITY_RANKS,
        "severity ranks are not exactly {1..5}",
    )

    setting_psi = {iid: items[0][1].injection.psi for iid, items in by_setting.items()}
    setting_rank = {iid: items[0][1].manifest.severity_rank for iid, items in by_setting.items()}
    expected_order = sorted(setting_psi, key=lambda iid: (-setting_psi[iid], iid))
    expected_rank = {iid: rank for rank, iid in enumerate(expected_order, start=1)}
    report.record(
        "severity_ranks_match_psi_order",
        setting_rank == expected_rank,
        f"severity ranks do not follow PSI order: {setting_rank} vs {expected_rank}",
    )

    setting_outcome = {
        iid: items[0][1].ground_truth.metric_outcome for iid, items in by_setting.items()
    }
    counts = {"regression": 0, "improvement": 0, "stable": 0}
    for outcome in setting_outcome.values():
        counts[outcome] += 1
    has_mix = counts["regression"] >= 1 and (counts["improvement"] + counts["stable"]) >= 1
    report.record(
        "outcome_mix_has_failures_and_controls",
        has_mix,
        f"outcome composition {counts} lacks a failure/control mix",
    )

    leakage = sum(len(diagnosis_input_leakage(c.diagnosis_input)) for c in loaded)
    report.leakage_total = leakage
    report.record("zero_diagnosis_leakage", leakage == 0, f"diagnosis leakage = {leakage}")
    return report
