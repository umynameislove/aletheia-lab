#!/usr/bin/env python3
"""Prepare or verify the provider-free V2 claim-extraction closeout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.claim_validation_v2_extraction import (
    build_dashboard_input_usage_observation,
    build_v2_extraction_closeout,
    checked_extraction_run_directory,
    publish_v2_extraction_closeout,
    verify_v2_extraction_closeout,
)
from aletheia_lab.evaluation.claim_validation_v2_extraction_contracts import (
    ClaimValidationV2ExtractionError,
    V2ClaimExtractionCloseout,
)
from aletheia_lab.filesystem import publish_immutable_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--qualification-run-dir", type=Path, required=True)
    parser.add_argument("--cohort-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dashboard-utc-date")
    parser.add_argument("--dashboard-input-tokens", type=int)
    parser.add_argument("--dashboard-evidence", type=Path)
    return parser


def _summary(
    closeout: V2ClaimExtractionCloseout,
    *,
    output: Path,
    publication_disposition: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": closeout.status,
        "terminal_request_count": closeout.terminal_request_count,
        "parsed_terminal_count": closeout.parsed_terminal_count,
        "normalized_output_count": closeout.normalized_output_count,
        "raw_atomic_claim_count": closeout.raw_atomic_claim_count,
        "selected_source_claim_count": closeout.selected_source_claim_count,
        "distinct_canonical_claim_text_count": (closeout.distinct_canonical_claim_text_count),
        "repeated_claim_instance_count": closeout.repeated_claim_instance_count,
        "distinct_normalized_output_count": (closeout.distinct_normalized_output_count),
        "repeated_normalized_output_instance_count": (
            closeout.repeated_normalized_output_instance_count
        ),
        "sample_target": closeout.sample_target,
        "relation_request_ceiling": closeout.relation_request_ceiling,
        "exact_frozen_selection_feasibility": (closeout.exact_frozen_selection_feasibility),
        "relation_frame_batch_built": closeout.relation_frame_batch_built,
        "extraction_blockers": closeout.extraction_blockers,
        "relation_execution_technically_unlocked": (
            closeout.relation_execution_technically_unlocked
        ),
        "relation_execution_ready": closeout.relation_execution_ready,
        "relation_execution_authorized": closeout.relation_execution_authorized,
        "dashboard_usage_observation_recorded": (
            closeout.dashboard_input_usage_observation is not None
        ),
        "additional_provider_calls_executed": (closeout.additional_provider_calls_executed),
        "claims_materialized": closeout.claims_materialized,
        "blind_packets_generated": closeout.blind_packets_generated,
        "closeout_sha256": closeout.closeout_sha256,
        "output": output.as_posix(),
    }
    if publication_disposition is not None:
        result["publication_disposition"] = publication_disposition
    return result


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def _dashboard_arguments(args: argparse.Namespace) -> tuple[str, int, Path] | None:
    values = (
        args.dashboard_utc_date,
        args.dashboard_input_tokens,
        args.dashboard_evidence,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ClaimValidationV2ExtractionError(
            "dashboard date, input-token count and evidence must be supplied together"
        )
    return values


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        run = checked_extraction_run_directory(root, args.run_dir)
        output = run / "closeout.json"
        if args.command == "verify":
            if _dashboard_arguments(args) is not None:
                raise ClaimValidationV2ExtractionError(
                    "verify reads its dashboard observation from the immutable closeout"
                )
            closeout = verify_v2_extraction_closeout(
                root,
                qualification_run_dir=args.qualification_run_dir,
                cohort_run_dir=args.cohort_run_dir,
                extraction_run_dir=run,
            )
            _print(_summary(closeout, output=output))
            return 0

        dashboard = _dashboard_arguments(args)
        observation = None
        evidence_output = run / "dashboard-input-usage-evidence.png"
        if dashboard is None:
            if evidence_output.exists() or evidence_output.is_symlink():
                raise ClaimValidationV2ExtractionError(
                    "dashboard evidence exists but no observation was requested"
                )
        else:
            observed_date, input_tokens, evidence_path = dashboard
            if evidence_path.is_symlink() or not evidence_path.resolve(strict=True).is_file():
                raise ClaimValidationV2ExtractionError(
                    "dashboard evidence must be a regular non-linked file"
                )
            evidence = evidence_path.read_bytes()
            observation = build_dashboard_input_usage_observation(
                observed_utc_date=observed_date,
                input_token_count=input_tokens,
                evidence=evidence,
            )
            publish_immutable_file(evidence_output, evidence)
        closeout = build_v2_extraction_closeout(
            root,
            qualification_run_dir=args.qualification_run_dir,
            cohort_run_dir=args.cohort_run_dir,
            dashboard_observation=observation,
        )
        disposition = publish_v2_extraction_closeout(run, closeout)
        _print(
            _summary(
                closeout,
                output=output,
                publication_disposition=disposition,
            )
        )
        return 0
    except (ClaimValidationV2ExtractionError, OSError, ValueError) as exc:
        _print(
            {
                "status": "claim_support_validation_v2_extraction_failed",
                "error": type(exc).__name__,
                "message": str(exc),
                "additional_provider_calls_executed": False,
                "relation_execution_authorized": False,
                "claims_materialized": False,
                "blind_packets_generated": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
