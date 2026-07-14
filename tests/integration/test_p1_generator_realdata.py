"""Real-data integration: generate P1 cases on the processed Telco dataset."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.case_validation import validate_p1_cases
from aletheia_lab.benchmark.generator import generate_p1

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
