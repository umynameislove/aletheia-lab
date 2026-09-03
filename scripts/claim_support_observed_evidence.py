#!/usr/bin/env python3
"""Materialize or verify the measured 45-context claim-evidence census."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusContractError
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.observed_evidence import (
    OBSERVED_EVIDENCE_CENSUS_PATH,
    OBSERVED_EVIDENCE_RECEIPT_PATH,
    ObservedEvidenceMaterializationError,
    materialize_observed_evidence,
)
from aletheia_lab.evaluation.observed_evidence_receipt import (
    ObservedEvidenceReceipt,
    build_observed_evidence_receipt,
    canonical_json_bytes,
    validate_observed_evidence_receipt,
)
from aletheia_lab.filesystem import (
    ImmutablePublicationConflictError,
    ImmutablePublicationIntegrityError,
    publish_immutable_file,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("materialize", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--census", type=Path)
    parser.add_argument("--receipt", type=Path)
    return parser


def _paths(args: argparse.Namespace, root: Path) -> tuple[Path, Path]:
    census = args.census or root / OBSERVED_EVIDENCE_CENSUS_PATH
    receipt = args.receipt or root / OBSERVED_EVIDENCE_RECEIPT_PATH
    return census.resolve(), receipt.resolve()


def _load(census_path: Path, receipt_path: Path) -> tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt]:
    if (
        census_path.is_symlink()
        or receipt_path.is_symlink()
        or not census_path.is_file()
        or not receipt_path.is_file()
    ):
        raise ObservedEvidenceMaterializationError("tracked evidence census or receipt is unavailable")
    try:
        return (
            ObservedEvidenceCensus.model_validate_json(census_path.read_bytes()),
            ObservedEvidenceReceipt.model_validate_json(receipt_path.read_bytes()),
        )
    except (OSError, ValidationError) as exc:
        raise ObservedEvidenceMaterializationError(
            "tracked evidence census or receipt is invalid"
        ) from exc


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    census_path, receipt_path = _paths(args, root)
    try:
        rebuilt_census = materialize_observed_evidence(root)
        if args.command == "materialize":
            rebuilt_receipt = build_observed_evidence_receipt(root, rebuilt_census)
            census_disposition = publish_immutable_file(
                census_path, canonical_json_bytes(rebuilt_census)
            )
            receipt_disposition = publish_immutable_file(
                receipt_path, canonical_json_bytes(rebuilt_receipt)
            )
        else:
            tracked_census, tracked_receipt = _load(census_path, receipt_path)
            validate_observed_evidence_receipt(tracked_census, tracked_receipt)
            if tracked_census != rebuilt_census:
                raise ObservedEvidenceMaterializationError(
                    "tracked evidence differs from a fresh measured reconstruction"
                )
            rebuilt_receipt = build_observed_evidence_receipt(root, rebuilt_census)
            if tracked_receipt != rebuilt_receipt:
                raise ObservedEvidenceMaterializationError(
                    "tracked receipt differs from independent token accounting"
                )
            census_disposition = "verified"
            receipt_disposition = "verified"
    except (
        ClaimCorpusContractError,
        ImmutablePublicationConflictError,
        ImmutablePublicationIntegrityError,
        ObservedEvidenceMaterializationError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "claim_observed_evidence_census_failed",
                    "error": str(exc),
                    "provider_calls_executed": False,
                    "outcomes_generated": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                **rebuilt_receipt.model_dump(mode="json"),
                "census_path": census_path.as_posix(),
                "receipt_path": receipt_path.as_posix(),
                "census_disposition": census_disposition,
                "receipt_disposition": receipt_disposition,
                "observed_evidence_census_pending": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
