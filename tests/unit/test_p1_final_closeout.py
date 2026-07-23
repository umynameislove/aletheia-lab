"""Regression tests for the offline P1 final closeout layer."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia_lab.cli import app
from aletheia_lab.evaluation.final_closeout import validate_p1_final_closeout

REPORT_ROOT = Path("reports/p1")
REVIEW_ROOT = REPORT_ROOT / "human-review"
runner = CliRunner()


def _paths(record: Path | None = None) -> tuple[Path, ...]:
    return (
        record or REPORT_ROOT / "p1-final-closeout.json",
        REPORT_ROOT / "p1-machine-result.json",
        REPORT_ROOT / "p1-result-lock.json",
        REVIEW_ROOT / "evidence-review.json",
        REVIEW_ROOT / "evidence-blind-packet.json",
        REVIEW_ROOT / "evidence-mapping-packet.json",
        REVIEW_ROOT / "diagnosis-review.json",
        REVIEW_ROOT / "diagnosis-blind-packet.json",
        REVIEW_ROOT / "diagnosis-mapping-packet.json",
    )


def test_final_closeout_validates_complete_frozen_p1() -> None:
    result = validate_p1_final_closeout(*_paths())
    assert result.status == "complete"
    assert result.decision == "go_to_phase_2"
    assert result.evidence_review.verdict == "pass"
    assert result.diagnosis_review.status == "valid_with_disclosed_correction"
    assert result.machine_summary.noisy_robust == 8
    assert result.diagnosis_review.human_semantic_noisy_robust == 10

    args = [
        "benchmark",
        "validate-p1-final",
        "--record",
        str(REPORT_ROOT / "p1-final-closeout.json"),
        "--machine-result",
        str(REPORT_ROOT / "p1-machine-result.json"),
        "--result-lock",
        str(REPORT_ROOT / "p1-result-lock.json"),
        "--evidence-review",
        str(REVIEW_ROOT / "evidence-review.json"),
        "--evidence-blind-packet",
        str(REVIEW_ROOT / "evidence-blind-packet.json"),
        "--evidence-mapping-packet",
        str(REVIEW_ROOT / "evidence-mapping-packet.json"),
        "--diagnosis-review",
        str(REVIEW_ROOT / "diagnosis-review.json"),
        "--diagnosis-blind-packet",
        str(REVIEW_ROOT / "diagnosis-blind-packet.json"),
        "--diagnosis-mapping-packet",
        str(REVIEW_ROOT / "diagnosis-mapping-packet.json"),
    ]
    completed = runner.invoke(app, args)
    assert completed.exit_code == 0, completed.output
    assert "P1 final closeout PASS" in completed.output


@pytest.mark.parametrize(
    ("field_path", "replacement", "message"),
    [
        (("machine_summary", "correct"), 24, "machine correctness census"),
        (("evidence_review", "entry_pass_count"), 14, "Input should be 15"),
        (("diagnosis_review", "round1_uncertain"), 0, "Input should be 12"),
        (("diagnosis_review", "human_machine_agreement"), 30, "Input should be 29"),
        (("status",), "pending", "Input should be 'complete'"),
    ],
)
def test_final_closeout_rejects_census_or_status_rewrites(
    tmp_path: Path,
    field_path: tuple[str, ...],
    replacement: object,
    message: str,
) -> None:
    payload = json.loads((REPORT_ROOT / "p1-final-closeout.json").read_text("utf-8"))
    target = payload
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = replacement
    record = tmp_path / "tampered-record.json"
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        validate_p1_final_closeout(*_paths(record))


@pytest.mark.parametrize(
    ("source", "index", "message"),
    [
        ("evidence-review", 3, "evidence review SHA-256"),
        ("diagnosis-review", 6, "diagnosis review SHA-256"),
        ("diagnosis-blind", 7, "diagnosis blind packet SHA-256"),
        ("machine-result", 1, "machine result SHA-256"),
    ],
)
def test_final_closeout_rejects_bound_artifact_tampering(
    tmp_path: Path,
    source: str,
    index: int,
    message: str,
) -> None:
    paths = list(_paths())
    tampered = tmp_path / f"{source}{paths[index].suffix}"
    shutil.copyfile(paths[index], tampered)
    tampered.write_bytes(tampered.read_bytes() + b"\n")
    paths[index] = tampered
    with pytest.raises(ValueError, match=message):
        validate_p1_final_closeout(*paths)


def test_final_closeout_rejects_symlink(tmp_path: Path) -> None:
    link = tmp_path / "review.json"
    link.symlink_to((REVIEW_ROOT / "evidence-review.json").resolve())
    paths = list(_paths())
    paths[3] = link
    with pytest.raises(ValueError, match="must not be symlinks"):
        validate_p1_final_closeout(*paths)
