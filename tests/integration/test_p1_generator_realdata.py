"""Real-data integration: generate P1 cases on the processed Telco dataset."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.case_validation import validate_p1_cases
from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.evidence.p1 import (
    generate_p1_evidence_store,
    validate_p1_evidence_store,
)

_CONFIG = Path("configs/project.yaml")
_PROCESSED = Path("data/processed/telco_customer_churn.csv")

pytestmark = pytest.mark.skipif(
    not (_CONFIG.exists() and _PROCESSED.exists()),
    reason="processed Telco dataset not present (run `make data` first)",
)


def test_generate_validates_15_cases_zero_leakage(tmp_path):
    summary = generate_p1(_CONFIG, tmp_path / "cases")
    assert summary["case_count"] == 15
    assert summary["leakage_total"] == 0
    report = validate_p1_cases(tmp_path / "cases")
    assert report.passed, report.as_dict()
    assert report.leakage_total == 0


def test_real_generation_is_reproducible(tmp_path):
    generate_p1(_CONFIG, tmp_path / "a")
    generate_p1(_CONFIG, tmp_path / "b")
    for case in sorted(p.name for p in (tmp_path / "a").iterdir()):
        a = (tmp_path / "a" / case / "checksums.json").read_bytes()
        b = (tmp_path / "b" / case / "checksums.json").read_bytes()
        assert a == b


def test_real_outcome_composition_and_measured_distractor(tmp_path):
    from aletheia_lab.benchmark.case_writer import load_case_dir_schema_only

    summary = generate_p1(_CONFIG, tmp_path / "cases")
    # Honest composition on the real dataset: 3 regression + 1 improvement + 1 stable.
    assert summary["outcome_counts"] == {"regression": 3, "improvement": 1, "stable": 1}
    assert summary["eligibility_counts"] == {
        "eligible_failure": 3,
        "improvement_control": 1,
        "stable_control": 1,
    }
    for index in range(1, 6):
        gt = load_case_dir_schema_only(
            tmp_path / "cases" / f"p1-data-drift-{index:02d}-full"
        ).ground_truth
        if gt.failure_eligibility.classification == "eligible_failure":
            assert gt.hidden_failure_cause is not None
        else:
            assert gt.hidden_failure_cause is None
    # Noisy carries a measured gender distractor with a real PSI.
    noisy = load_case_dir_schema_only(
        tmp_path / "cases" / "p1-data-drift-01-noisy"
    ).manifest.observable_signals
    assert len(noisy.additional_comparisons) == 1
    assert noisy.additional_comparisons[0].feature == "gender"
    assert noisy.additional_comparisons[0].psi is not None


def test_real_15_context_evidence_store_roundtrip_and_machine_audit(tmp_path):
    cases = tmp_path / "cases"
    store = tmp_path / "evidence-store"
    generate_p1(_CONFIG, cases)

    manifest = generate_p1_evidence_store(cases, store)
    report = validate_p1_evidence_store(store, cases)

    assert manifest.bundle_count == 15
    assert report.passed, report.as_dict()
    assert report.bundle_count == 15
    assert report.machine_leakage_findings == 0
    # The machine must not fabricate an independent human sign-off.
    assert report.human_review_status == "pending"
