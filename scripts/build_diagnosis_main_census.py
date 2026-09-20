#!/usr/bin/env python3
"""Build create-new private/main census artifacts from frozen prior sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainCensusError,
    DiagnosisMainCensusSources,
    build_diagnosis_main_census,
    serialize_census_artifact,
)

DEFAULT_SOURCE_CONTRACT = Path("configs/evaluation/diagnosis_main_census_source_contract.json")
DEFAULT_P2R = Path("experiments/p2/outputs/p2r-confirmatory-v1-2/measurements.json")
DEFAULT_PRESERVED_ROOT = Path(
    "../preserved-artifacts/p2-label-noise-shift-factorial-v3.3/"
    "sha256-d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152/"
    "result-store"
)
DEFAULT_LABEL_PROTOCOL = Path("configs/benchmark/p2_label_noise_shift_v3_3_protocol.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-contract", type=Path, default=DEFAULT_SOURCE_CONTRACT)
    parser.add_argument("--p2r-measurements", type=Path, default=DEFAULT_P2R)
    parser.add_argument(
        "--label-noise-primary",
        type=Path,
        default=DEFAULT_PRESERVED_ROOT / "primary-attempt.json",
    )
    parser.add_argument(
        "--label-noise-replication",
        type=Path,
        default=DEFAULT_PRESERVED_ROOT / "replication-attempt.json",
    )
    parser.add_argument("--label-noise-protocol", type=Path, default=DEFAULT_LABEL_PROTOCOL)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--seal-output", type=Path, required=True)
    parser.add_argument("--qwen-output", type=Path, required=True)
    return parser


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def main() -> int:
    args = _parser().parse_args()
    try:
        packet, seal, qwen = build_diagnosis_main_census(
            DiagnosisMainCensusSources(
                source_contract=args.source_contract,
                p2r_measurements=args.p2r_measurements,
                label_noise_primary=args.label_noise_primary,
                label_noise_replication=args.label_noise_replication,
                label_noise_protocol=args.label_noise_protocol,
            )
        )
        _write_new(args.private_output, serialize_census_artifact(packet))
        _write_new(args.seal_output, serialize_census_artifact(seal))
        _write_new(args.qwen_output, serialize_census_artifact(qwen))
        print(
            json.dumps(
                {
                    "status": "census_locked_execution_not_authorized",
                    "analysis_census_sha256": packet.analysis_census.census_sha256,
                    "private_packet_byte_sha256": seal.private_packet_byte_sha256,
                    "seal_sha256": seal.seal_sha256,
                    "qwen_census_sha256": qwen.census_sha256,
                    "family_count": seal.family_count,
                    "context_count": seal.context_count,
                    "controlled_request_count": seal.controlled_request_count,
                    "qwen_request_count": seal.qwen_request_count,
                    "protected_main_outcomes_opened": False,
                    "execution_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ValidationError,
        DiagnosisMainCensusError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "census_build_failed_closed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
