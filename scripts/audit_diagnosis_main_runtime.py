#!/usr/bin/env python3
"""Audit the exact private census against the frozen main runtime without execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.diagnosis.main_runtime import (
    MainRuntimeError,
    build_main_runtime_preflight,
    load_main_runtime_contract,
)
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainPrivateCensusPacket,
)

DEFAULT_CONTRACT = Path("configs/evaluation/diagnosis_main_runtime_contract.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--private-packet", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.private_packet.is_symlink() or not args.private_packet.is_file():
            raise MainRuntimeError("private census packet is unavailable")
        packet = DiagnosisMainPrivateCensusPacket.model_validate_json(
            args.private_packet.read_bytes()
        )
        contract = load_main_runtime_contract(args.contract)
        preflight = build_main_runtime_preflight(packet, contract)
        serialized = (
            json.dumps(
                preflight.model_dump(mode="json"),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as handle:
                handle.write(serialized.encode("utf-8"))
        print(serialized, end="")
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
                    "status": "runtime_preflight_failed_closed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
