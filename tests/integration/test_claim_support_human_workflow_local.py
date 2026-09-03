from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aletheia_lab.evaluation.human_workflow import (
    CompletedAnnotationDecision,
    CompletedAnnotationPacket,
    HumanRaterAttestation,
    HumanRaterSubmission,
    OnboardingAnswerKey,
)
from aletheia_lab.evaluation.instrument_validation import BlindAnnotationPacket

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/claim_support_human_workflow.py"


def _run(command: str, *extra: str, hash_seed: str = "1") -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONHASHSEED"] = hash_seed
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), command, "--root", str(ROOT), *extra],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_verify_and_dry_run_are_outcome_free_and_hash_seed_stable() -> None:
    first = _run("verify", hash_seed="1")
    second = _run("verify", hash_seed="104729")
    dry_run = _run("dry-run", hash_seed="209759")

    assert first == second
    assert first["status"] == "human_workflow_verified_no_annotations"
    assert first["human_annotations_collected"] is False
    assert first["main_or_sealed_outcomes_opened"] is False
    assert dry_run["status"] == "synthetic_onboarding_preparation_dry_run_passed"
    assert dry_run["artifacts_written"] is False


def test_prepare_onboarding_separates_raters_from_private_answer_key(tmp_path: Path) -> None:
    destination = tmp_path / "human-onboarding"
    result = _run("prepare-onboarding", "--output-dir", str(destination))

    assert result["status"] == "private_onboarding_deliveries_prepared"
    assert (destination / "rater-1/blind-packet.json").is_file()
    assert (destination / "rater-1/submission-template.json").is_file()
    assert (destination / "rater-2/blind-packet.json").is_file()
    assert (destination / "rater-2/submission-template.json").is_file()
    assert (destination / "coordinator-private/onboarding-answer-key.json").is_file()
    for rater in ("rater-1", "rater-2"):
        public_text = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted((destination / rater).iterdir())
        )
        assert "reference_label" not in public_text
        assert "teaching_note" not in public_text


def test_completed_submission_locks_through_the_cli(tmp_path: Path) -> None:
    destination = tmp_path / "human-onboarding"
    _run("prepare-onboarding", "--output-dir", str(destination))
    packet_path = destination / "rater-1/blind-packet.json"
    packet = BlindAnnotationPacket.model_validate_json(packet_path.read_text(encoding="utf-8"))
    answer_key = OnboardingAnswerKey.model_validate_json(
        (destination / "coordinator-private/onboarding-answer-key.json").read_text(encoding="utf-8")
    )
    references = {item.blind_claim_id: item.reference_label for item in answer_key.entries}
    submission = HumanRaterSubmission(
        workflow_sha256=answer_key.workflow_sha256,
        source_packet_sha256=packet.packet_sha256,
        phase="onboarding",
        rater_slot="rater_1",
        decisions=tuple(
            CompletedAnnotationDecision(
                blind_claim_id=claim.blind_claim_id,
                support_label=references[claim.blind_claim_id],
                evidence_ids_used=(claim.visible_evidence[0].evidence_id,),
                rationale="The visible evidence establishes the material support boundary.",
            )
            for claim in packet.claims
        ),
        attestation=HumanRaterAttestation(
            completed_by_human=True,
            worked_independently=True,
            model_assistance_used=False,
            rubric_read_before_rating=True,
        ),
    )
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(
        json.dumps(submission.model_dump(mode="json")),
        encoding="utf-8",
    )
    output_path = tmp_path / "locked/completed.json"

    result = _run(
        "lock-submission",
        "--packet",
        str(packet_path),
        "--submission",
        str(submission_path),
        "--output",
        str(output_path),
    )

    assert result["status"] == "human_submission_validated_and_locked"
    assert result["claim_count"] == 20
    completed = CompletedAnnotationPacket.model_validate_json(
        output_path.read_text(encoding="utf-8")
    )
    assert completed.completed_packet_sha256 == result["completed_packet_sha256"]


def test_prepare_onboarding_refuses_to_publish_inside_repository() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "prepare-onboarding",
            "--root",
            str(ROOT),
            "--output-dir",
            str(ROOT / "forbidden-human-packets"),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "outside the repository" in completed.stderr
    assert not (ROOT / "forbidden-human-packets").exists()
