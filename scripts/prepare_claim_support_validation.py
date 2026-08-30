#!/usr/bin/env python3
"""Verify or prepare the frozen independent claim-support validation study."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from aletheia_lab.evaluation.instrument_validation import (
    load_claim_pool,
    load_preparation_receipt,
    load_validation_protocol,
    prepare_validation_packets,
)
from aletheia_lab.filesystem import publish_staged_directory

DEFAULT_PROTOCOL = Path("configs/evaluation/claim_support_validation_protocol.json")
DEFAULT_RECEIPT = Path(
    "configs/evaluation/claim_support_validation_preparation_receipt.json"
)


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _verify(root: Path, protocol_path: Path, receipt_path: Path) -> dict[str, object]:
    protocol = load_validation_protocol(_resolve(root, protocol_path))
    receipt = load_preparation_receipt(_resolve(root, receipt_path))
    if receipt.protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("preparation receipt is not bound to the frozen protocol")
    return {
        "status": receipt.status,
        "protocol_sha256": protocol.protocol_sha256,
        "sample_minimum": protocol.sample_minimum,
        "sample_target": protocol.sample_target,
        "sample_maximum": protocol.sample_maximum,
        "independent_rater_count": protocol.independent_rater_count,
        "adjudicator_count": protocol.adjudicator_count,
        "human_annotations_collected": receipt.human_annotations_collected,
        "validation_metrics_generated": receipt.validation_metrics_generated,
        "main_or_sealed_outcomes_opened": receipt.main_or_sealed_outcomes_opened,
        "next_gate": "independent_human_annotation_and_adjudication",
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prepare(
    *,
    root: Path,
    protocol_path: Path,
    receipt_path: Path,
    pool_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    verified = _verify(root, protocol_path, receipt_path)
    protocol = load_validation_protocol(_resolve(root, protocol_path))
    entries = load_claim_pool(_resolve(root, pool_path))
    rater_1, rater_2, mapping, prepared = prepare_validation_packets(entries, protocol)
    destination = _resolve(root, output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=destination.parent))
    try:
        _write_json(stage / "rater-1-blind-packet.json", rater_1.model_dump(mode="json"))
        _write_json(stage / "rater-2-blind-packet.json", rater_2.model_dump(mode="json"))
        _write_json(stage / "evaluator-mapping.json", mapping.model_dump(mode="json"))
        _write_json(stage / "preparation-receipt.json", prepared.model_dump(mode="json"))
        publish_staged_directory(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    verified.update(
        {
            "status": prepared.status,
            "sample_count": prepared.sample_count,
            "label_census": dict(prepared.label_census),
            "prepared_study_receipt_sha256": prepared.receipt_sha256,
            "output_dir": str(destination),
        }
    )
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "prepare"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--pool", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "verify":
        result = _verify(root, args.protocol, args.receipt)
    else:
        if args.pool is None or args.output_dir is None:
            parser.error("prepare requires --pool and --output-dir")
        result = _prepare(
            root=root,
            protocol_path=args.protocol,
            receipt_path=args.receipt,
            pool_path=args.pool,
            output_dir=args.output_dir,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
