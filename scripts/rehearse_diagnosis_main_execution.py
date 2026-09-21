#!/usr/bin/env python3
"""Run the complete diagnosis census through a network-incapable fake adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.diagnosis.main_execution import rehearse_main_execution
from aletheia_lab.diagnosis.main_runtime import (
    MainRuntimeError,
    MainRuntimeStore,
    load_main_runtime_inputs,
)
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainCensusSeal,
    DiagnosisMainPrivateCensusPacket,
)
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json, content_sha256

DEFAULT_SEAL = Path("configs/evaluation/diagnosis_main_census_seal.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--private-packet", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _outside_repository(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise MainRuntimeError("private rehearsal artifacts must remain outside the repository")


def _load_packet(root: Path, path: Path) -> DiagnosisMainPrivateCensusPacket:
    if path.is_symlink() or not path.is_file():
        raise MainRuntimeError("private census packet is unavailable")
    packet_bytes = path.read_bytes()
    seal_path = root / DEFAULT_SEAL
    if seal_path.is_symlink() or not seal_path.is_file():
        raise MainRuntimeError("public census seal is unavailable")
    seal = DiagnosisMainCensusSeal.model_validate_json(seal_path.read_bytes())
    packet = DiagnosisMainPrivateCensusPacket.model_validate_json(packet_bytes)
    if (
        content_sha256(packet_bytes) != seal.private_packet_byte_sha256
        or packet.packet_sha256 != seal.private_packet_canonical_sha256
        or packet.analysis_census.census_sha256 != seal.analysis_census_sha256
    ):
        raise MainRuntimeError("private census packet differs from the public seal")
    return packet


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        store_path = _outside_repository(root, args.store)
        output_path = _outside_repository(root, args.output)
        try:
            output_path.relative_to(store_path)
        except ValueError:
            pass
        else:
            raise MainRuntimeError("rehearsal summary must remain outside the runtime store")
        packet = _load_packet(root, args.private_packet)
        contract, fairness_freeze, response_contract = load_main_runtime_inputs(root)
        result = rehearse_main_execution(
            packet=packet,
            contract=contract,
            response_contract=response_contract,
            fairness_freeze=fairness_freeze,
            store=MainRuntimeStore(store_path),
        )
        serialized = (canonical_project_json(result.model_dump(mode="json")) + "\n").encode()
        disposition = publish_immutable_file(output_path, serialized)
        payload = result.model_dump(mode="json")
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
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "offline_rehearsal_failed_closed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
