#!/usr/bin/env python3
"""Verify and operate the private claim-support human-rating boundary."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.human_workflow import (
    HumanWorkflowError,
    load_human_workflow,
    load_onboarding_fixture,
    load_rater_submission,
    lock_completed_packet,
    prepare_onboarding_materials,
    submission_template,
)
from aletheia_lab.evaluation.instrument_validation import (
    BlindAnnotationPacket,
    load_validation_protocol,
)
from aletheia_lab.filesystem import (
    fsync_directory_tree,
    publish_immutable_file,
    publish_staged_directory,
    write_new_file,
)

DEFAULT_WORKFLOW = Path("configs/evaluation/claim_support_human_workflow.json")


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_packet(path: Path) -> BlindAnnotationPacket:
    try:
        return BlindAnnotationPacket.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HumanWorkflowError("blind annotation packet is unavailable or invalid") from exc


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _verify(root: Path, workflow_path: Path) -> dict[str, object]:
    workflow = load_human_workflow(root, workflow_path)
    fixture = load_onboarding_fixture(root / workflow.onboarding_fixture_path)
    validation = load_validation_protocol(root / workflow.validation_protocol_path)
    rater_1, rater_2, answer_key = prepare_onboarding_materials(
        fixture,
        validation,
        workflow,
    )
    return {
        "status": "human_workflow_verified_no_annotations",
        "workflow_sha256": workflow.workflow_sha256,
        "validation_protocol_sha256": validation.protocol_sha256,
        "onboarding_fixture_sha256": fixture.fixture_sha256,
        "onboarding_case_count": len(fixture.cases),
        "main_claim_count": workflow.main_claim_count,
        "independent_rater_count": len(workflow.rater_slots),
        "rater_packet_hashes_distinct": rater_1.packet_sha256 != rater_2.packet_sha256,
        "answer_key_sha256": answer_key.answer_key_sha256,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
        "next_gate": "private_onboarding_delivery_and_real_human_completion",
    }


def _require_external_destination(root: Path, destination: Path) -> None:
    try:
        destination.resolve().relative_to(root.resolve())
    except ValueError:
        return
    raise HumanWorkflowError("human-rating deliveries must be written outside the repository")


def _prepare_onboarding(
    root: Path,
    workflow_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    workflow = load_human_workflow(root, workflow_path)
    fixture = load_onboarding_fixture(root / workflow.onboarding_fixture_path)
    validation = load_validation_protocol(root / workflow.validation_protocol_path)
    rater_1, rater_2, answer_key = prepare_onboarding_materials(
        fixture,
        validation,
        workflow,
    )
    destination = output_dir.resolve()
    _require_external_destination(root, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=destination.parent))
    try:
        for label, packet in (("rater-1", rater_1), ("rater-2", rater_2)):
            write_new_file(
                stage / label / "blind-packet.json",
                _json_bytes(packet.model_dump(mode="json")),
            )
            write_new_file(
                stage / label / "submission-template.json",
                _json_bytes(submission_template(packet, workflow, phase="onboarding")),
            )
            write_new_file(
                stage / label / "RATER_GUIDE.md",
                (root / workflow.rater_guide_path).read_bytes(),
            )
        write_new_file(
            stage / "coordinator-private" / "onboarding-answer-key.json",
            _json_bytes(answer_key.model_dump(mode="json")),
        )
        fsync_directory_tree(stage)
        publish_staged_directory(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "status": "private_onboarding_deliveries_prepared",
        "workflow_sha256": workflow.workflow_sha256,
        "output_dir": str(destination),
        "rater_1_packet_sha256": rater_1.packet_sha256,
        "rater_2_packet_sha256": rater_2.packet_sha256,
        "answer_key_sha256": answer_key.answer_key_sha256,
        "human_annotations_collected": False,
        "warning": "send each rater directory separately; retain coordinator-private locally",
    }


def _lock(
    root: Path,
    workflow_path: Path,
    packet_path: Path,
    submission_path: Path,
    output_path: Path,
) -> dict[str, object]:
    workflow = load_human_workflow(root, workflow_path)
    packet = _load_packet(_resolve(root, packet_path))
    submission = load_rater_submission(_resolve(root, submission_path))
    completed = lock_completed_packet(packet, submission, workflow)
    destination = output_path.resolve()
    _require_external_destination(root, destination)
    disposition = publish_immutable_file(
        destination,
        _json_bytes(completed.model_dump(mode="json")),
    )
    return {
        "status": "human_submission_validated_and_locked",
        "phase": completed.phase,
        "rater_slot": completed.rater_slot,
        "claim_count": completed.claim_count,
        "completed_packet_sha256": completed.completed_packet_sha256,
        "publication_disposition": disposition,
        "output_path": str(destination),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("verify", "dry-run", "prepare-onboarding", "lock-submission"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--submission", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command in {"verify", "dry-run"}:
        result = _verify(root, args.workflow)
        if args.command == "dry-run":
            result["status"] = "synthetic_onboarding_preparation_dry_run_passed"
            result["artifacts_written"] = False
    elif args.command == "prepare-onboarding":
        if args.output_dir is None:
            parser.error("prepare-onboarding requires --output-dir")
        result = _prepare_onboarding(root, args.workflow, args.output_dir)
    else:
        if args.packet is None or args.submission is None or args.output is None:
            parser.error("lock-submission requires --packet, --submission, and --output")
        result = _lock(
            root,
            args.workflow,
            args.packet,
            args.submission,
            args.output,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
