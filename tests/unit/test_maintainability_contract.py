"""Regression contracts for the repository maintainability budget."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "configs" / "maintainability_budget.json"
_SCRIPT = _ROOT / "scripts" / "check_maintainability.py"


def _config() -> dict[str, object]:
    payload = json.loads(_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_tracked_maintainability_budget_passes_with_a_self_hashing_receipt() -> None:
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "pass"
    assert receipt["findings"] == []
    assert len(receipt["audit_sha256"]) == 64


def test_complexity_budget_cannot_hide_growth_between_packages() -> None:
    complexity = _config()["complexity"]
    assert isinstance(complexity, dict)
    assert complexity["rule"] == "C901"
    assert complexity["maximum_total_violations"] == 83
    assert complexity["maximum_single_score"] == 42
    assert complexity["package_violation_budgets"] == {
        "benchmark": 49,
        "context": 3,
        "data": 1,
        "diagnosis": 4,
        "evaluation": 8,
        "evidence": 6,
        "model_gateway": 4,
        "project": 8,
    }


def test_oversized_modules_require_bounded_reviewed_exemptions() -> None:
    module_size = _config()["module_size"]
    assert isinstance(module_size, dict)
    assert module_size["maximum_lines_without_exemption"] == 800
    exemptions = module_size["exemptions"]
    assert isinstance(exemptions, dict)
    assert exemptions
    for path, raw_exemption in exemptions.items():
        assert isinstance(path, str)
        assert isinstance(raw_exemption, dict)
        maximum = raw_exemption["maximum_lines"]
        rationale = raw_exemption["rationale"]
        assert isinstance(maximum, int) and maximum > 800
        assert isinstance(rationale, str) and rationale.endswith(".")
        lines = len((_ROOT / path).read_text(encoding="utf-8").splitlines())
        assert 800 < lines == maximum


def test_direct_hash_budgets_are_frozen_per_package() -> None:
    hash_ownership = _config()["hash_ownership"]
    assert isinstance(hash_ownership, dict)
    assert hash_ownership["package_direct_sha256_budgets"] == {
        "baseline": 2,
        "benchmark": 47,
        "data": 1,
        "diagnosis": 3,
        "evaluation": 7,
        "evidence": 3,
        "project": 4,
    }


def test_immutable_publication_has_one_owner_and_reviewed_delegates() -> None:
    publication = _config()["publication_ownership"]
    assert isinstance(publication, dict)
    assert publication["immutable_file_primitive_owner"] == (
        "src/aletheia_lab/filesystem.py"
    )
    assert publication["required_immutable_file_consumers"] == [
        "src/aletheia_lab/data/manifest.py",
        "src/aletheia_lab/diagnosis/_development/store.py",
        "src/aletheia_lab/evaluation/_attempt_store/writer.py",
        "src/aletheia_lab/project/persistence.py",
    ]
    assert publication["allowed_atomic_create_delegates"] == [
        "src/aletheia_lab/evaluation/_attempt_store/store.py",
        "src/aletheia_lab/evaluation/_attempt_store/writer.py",
    ]


def test_tightened_budget_blocks_instead_of_self_declaring_success(tmp_path: Path) -> None:
    config = _config()
    complexity = config["complexity"]
    assert isinstance(complexity, dict)
    complexity["maximum_total_violations"] = 0
    constrained = tmp_path / "maintainability.json"
    constrained.write_text(json.dumps(config), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), "--config", str(constrained)],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "blocked"
    assert any("C901 total" in finding for finding in receipt["findings"])
