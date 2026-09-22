#!/usr/bin/env python3
"""Rehearse terminal-to-analysis materialization without provider calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.diagnosis.main_execution import MainBatchResult
from aletheia_lab.diagnosis.main_runtime import MainRuntimeError, load_main_runtime_inputs
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainCensusSeal,
    DiagnosisMainPrivateCensusPacket,
)
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    DiagnosisMainMaterializationError,
    load_main_analysis_plan,
    load_main_scoring_contract,
    prepare_main_scoring,
    rehearse_main_materialization,
)
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json, content_sha256

DEFAULT_SEAL = Path("configs/evaluation/diagnosis_main_census_seal.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--private-packet", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--batch-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _outside_repository(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise DiagnosisMainMaterializationError(
        "private materialization artifacts must remain outside the repository"
    )


def _load_packet(root: Path, path: Path) -> DiagnosisMainPrivateCensusPacket:
    if path.is_symlink() or not path.is_file():
        raise DiagnosisMainMaterializationError("private census packet is unavailable")
    packet_bytes = path.read_bytes()
    seal_path = root / DEFAULT_SEAL
    if seal_path.is_symlink() or not seal_path.is_file():
        raise DiagnosisMainMaterializationError("public census seal is unavailable")
    seal = DiagnosisMainCensusSeal.model_validate_json(seal_path.read_bytes())
    packet = DiagnosisMainPrivateCensusPacket.model_validate_json(packet_bytes)
    if (
        content_sha256(packet_bytes) != seal.private_packet_byte_sha256
        or packet.packet_sha256 != seal.private_packet_canonical_sha256
        or packet.analysis_census.census_sha256 != seal.analysis_census_sha256
    ):
        raise DiagnosisMainMaterializationError(
            "private census packet differs from the public seal"
        )
    return packet


def _load_batch_result(path: Path) -> MainBatchResult:
    if path.is_symlink() or not path.is_file():
        raise DiagnosisMainMaterializationError("batch result is unavailable")
    try:
        result = MainBatchResult.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise DiagnosisMainMaterializationError("batch result is invalid") from exc
    if result.execution_mode != "offline_rehearsal":
        raise DiagnosisMainMaterializationError(
            "this entrypoint accepts offline rehearsal results only"
        )
    return result


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        packet_path = _outside_repository(root, args.private_packet)
        store_path = _outside_repository(root, args.store)
        batch_result_path = _outside_repository(root, args.batch_result)
        output_path = _outside_repository(root, args.output)
        if output_path in (batch_result_path, packet_path):
            raise DiagnosisMainMaterializationError(
                "rehearsal receipt cannot replace an input artifact"
            )
        try:
            output_path.relative_to(store_path)
        except ValueError:
            pass
        else:
            raise DiagnosisMainMaterializationError(
                "rehearsal receipt must remain outside the runtime store"
            )

        packet = _load_packet(root, packet_path)
        runtime_contract, fairness_freeze, response_contract = load_main_runtime_inputs(root)
        scoring_contract = load_main_scoring_contract(
            root,
            runtime_contract=runtime_contract,
            response_contract=response_contract,
        )
        batch_result = _load_batch_result(batch_result_path)
        preparation = prepare_main_scoring(
            packet=packet,
            runtime_contract=runtime_contract,
            response_contract=response_contract,
            fairness_freeze=fairness_freeze,
            scoring_contract=scoring_contract,
            batch_result=batch_result,
            store_root=store_path,
        )
        receipt = rehearse_main_materialization(
            plan=load_main_analysis_plan(root),
            census=packet.analysis_census,
            preparation=preparation,
        )
        serialized = (canonical_project_json(receipt.model_dump(mode="json")) + "\n").encode()
        disposition = publish_immutable_file(output_path, serialized)
        payload = receipt.model_dump(mode="json")
        payload["publication_disposition"] = disposition
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ValidationError,
        MainRuntimeError,
        DiagnosisMainMaterializationError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "offline_materialization_rehearsal_failed_closed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
