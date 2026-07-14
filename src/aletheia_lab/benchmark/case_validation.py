"""Validator for the full set of P1 benchmark cases.

Checks the 5x3 matrix, uniqueness, schema validity, checksum integrity, dataset
consistency, presence of an observable drift signal per setting, and — most
importantly — that no case leaks ground truth into its diagnosis-visible payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.case_schema import EVIDENCE_CONDITIONS
from aletheia_lab.benchmark.case_writer import (
    diagnosis_input_leakage,
    load_case_dir,
    sha256_file,
)

EXPECTED_CASE_COUNT = 15
EXPECTED_SETTINGS = 5
EXPECTED_CONDITIONS_PER_SETTING = 3
EXPECTED_CASES_PER_CONDITION = 5


@dataclass
class ValidationReport:
    passed: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    leakage_total: int = 0
    case_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def _record(self, name: str, ok: bool, error: str | None = None) -> None:
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
            "errors": self.errors,
        }


def _case_dirs(cases_dir: Path) -> list[Path]:
    return sorted(p for p in cases_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists())


def validate_p1_cases(cases_dir: str | Path) -> ValidationReport:
    """Validate a generated P1 case directory. Returns a structured report."""

    report = ValidationReport()
    base = Path(cases_dir)
    if not base.exists():
        report._record("cases_dir_exists", False, f"no such directory: {base}")
        return report

    dirs = _case_dirs(base)
    loaded = []
    for case_dir in dirs:
        try:
            loaded.append(load_case_dir(case_dir))
        except Exception as exc:  # noqa: BLE001 - report any invalid case, don't crash
            report._record(f"load:{case_dir.name}", False, f"invalid case {case_dir.name}: {exc}")
    if not report.passed:
        return report

    report.case_ids = [c.manifest.case_id for c in loaded]

    report._record(
        "exactly_15_cases",
        len(loaded) == EXPECTED_CASE_COUNT,
        f"expected {EXPECTED_CASE_COUNT} cases, got {len(loaded)}",
    )
    report._record(
        "unique_case_ids",
        len(set(report.case_ids)) == len(report.case_ids),
        "duplicate case_id found",
    )
    report._record(
        "only_data_drift",
        all(c.manifest.fault_type == "data_drift" for c in loaded),
        "non data_drift fault_type present",
    )

    settings = {c.manifest.injection_id for c in loaded}
    report._record(
        "exactly_5_settings",
        len(settings) == EXPECTED_SETTINGS,
        f"expected {EXPECTED_SETTINGS} injection settings, got {len(settings)}",
    )

    per_setting: dict[str, set[str]] = {}
    per_condition: dict[str, int] = {c: 0 for c in EVIDENCE_CONDITIONS}
    for case in loaded:
        per_setting.setdefault(case.manifest.injection_id, set()).add(
            case.manifest.evidence_condition
        )
        per_condition[case.manifest.evidence_condition] += 1
    report._record(
        "three_conditions_per_setting",
        all(conds == set(EVIDENCE_CONDITIONS) for conds in per_setting.values()),
        "a setting does not have exactly the 3 evidence conditions",
    )
    report._record(
        "five_cases_per_condition",
        all(per_condition[c] == EXPECTED_CASES_PER_CONDITION for c in EVIDENCE_CONDITIONS),
        f"condition counts not all {EXPECTED_CASES_PER_CONDITION}: {per_condition}",
    )

    dataset_shas = {c.manifest.dataset_sha256 for c in loaded}
    report._record(
        "dataset_checksum_consistent",
        len(dataset_shas) == 1,
        f"inconsistent dataset checksums: {dataset_shas}",
    )

    # Checksum integrity: recorded checksums must match files on disk.
    integrity_ok = True
    for case_dir in dirs:
        recorded = load_case_dir(case_dir)  # ensures files parse
        _ = recorded
        checks_path = case_dir / "checksums.json"
        if not checks_path.exists():
            integrity_ok = False
            continue
        import json

        recorded_checks = json.loads(checks_path.read_text("utf-8"))
        for name, digest in recorded_checks.items():
            if sha256_file(case_dir / name) != digest:
                integrity_ok = False
    report._record("artifact_checksums_match", integrity_ok, "an artifact checksum mismatch")

    # Observable drift signal present for each of the 5 settings (via full/noisy).
    setting_has_signal: dict[str, bool] = {}
    for case in loaded:
        sig = case.manifest.observable_signals
        has = sig.psi is not None and sig.distribution_reference is not None
        setting_has_signal[case.manifest.injection_id] = (
            setting_has_signal.get(case.manifest.injection_id, False) or has
        )
    report._record(
        "drift_signal_per_setting",
        len(setting_has_signal) == EXPECTED_SETTINGS and all(setting_has_signal.values()),
        "a setting has no observable drift signal in any condition",
    )

    report._record(
        "reproduction_command_recorded",
        all(c.manifest.reproduction.get("command") for c in loaded),
        "a case is missing its reproduction command",
    )

    # Ground-truth boundary: zero leakage across every diagnosis-visible payload.
    leakage = sum(len(diagnosis_input_leakage(c.diagnosis_input)) for c in loaded)
    report.leakage_total = leakage
    report._record(
        "zero_diagnosis_leakage", leakage == 0, f"diagnosis-visible leakage count = {leakage}"
    )

    # No two cases identical beyond design: (injection_id, condition) pairs unique.
    pairs = [(c.manifest.injection_id, c.manifest.evidence_condition) for c in loaded]
    report._record(
        "unique_setting_condition_pairs",
        len(set(pairs)) == len(pairs),
        "duplicate (setting, condition) pair",
    )

    return report
