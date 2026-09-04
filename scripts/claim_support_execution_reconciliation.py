#!/usr/bin/env python3
"""Audit the completed claim-corpus execution without producing claims or packets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionAuthorization,
    load_execution_authorization,
    load_execution_evidence_census,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    ClaimCorpusExecutionLease,
    ClaimCorpusLiveReceipt,
)
from aletheia_lab.evaluation.claim_corpus_reconciliation import (
    ClaimCorpusReconciliationError,
    reconcile_claim_corpus_execution,
)
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--evidence-census", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--lease", type=Path, required=True)
    parser.add_argument("--live-receipt", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--reserve-output", type=Path, required=True)
    parser.add_argument("--reconciliation-output", type=Path, required=True)
    return parser


def _outside_repository(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise ClaimCorpusReconciliationError("private reconciliation artifacts must stay outside the repository")


def _load_model(path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("artifact path is not a regular file")
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimCorpusReconciliationError("reconciliation input is unavailable or invalid") from exc


def _publish(path: Path, value: BaseModel) -> str:
    payload = (canonical_project_json(value.model_dump(mode="json")) + "\n").encode()
    return publish_immutable_file(path, payload)


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        store = _outside_repository(root, args.store)
        reserve_output = _outside_repository(root, args.reserve_output)
        reconciliation_output = _outside_repository(root, args.reconciliation_output)
        authorization: ClaimCorpusExecutionAuthorization = load_execution_authorization(
            args.authorization
        )
        evidence = load_execution_evidence_census(root, args.evidence_census)
        lease = ClaimCorpusExecutionLease.model_validate(
            _load_model(args.lease, ClaimCorpusExecutionLease).model_dump(mode="python")
        )
        live_receipt = ClaimCorpusLiveReceipt.model_validate(
            _load_model(args.live_receipt, ClaimCorpusLiveReceipt).model_dump(mode="python")
        )
        reserve, reconciliation = reconcile_claim_corpus_execution(
            root,
            store_root=store,
            authorization=authorization,
            lease=lease,
            live_receipt=live_receipt,
            evidence_census=evidence,
        )
        reserve_disposition = _publish(reserve_output, reserve)
        reconciliation_disposition = _publish(reconciliation_output, reconciliation)
    except (ClaimCorpusReconciliationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "claim_corpus_execution_reconciliation_failed", "error": str(exc)},
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": reconciliation.status,
                "reserve_status": reserve.status,
                "reserve_output": str(reserve_output),
                "reserve_publication_disposition": reserve_disposition,
                "reconciliation_output": str(reconciliation_output),
                "reconciliation_publication_disposition": reconciliation_disposition,
                "terminal_request_count": reconciliation.terminal_request_count,
                "parsed_terminal_count": reconciliation.parsed_terminal_count,
                "technical_failure_terminal_count": (
                    reconciliation.technical_failure_terminal_count
                ),
                "provider_failure_count": reserve.provider_failure_count,
                "reserve_activation_performed": reserve.reserve_activation_performed,
                "ready_for_output_normalization": (
                    reconciliation.ready_for_output_normalization
                ),
                "claims_materialized": reconciliation.claims_materialized,
                "blind_packets_generated": reconciliation.blind_packets_generated,
                "main_or_sealed_outcomes_opened": (
                    reconciliation.main_or_sealed_outcomes_opened
                ),
                "reserve_receipt_sha256": reserve.receipt_sha256,
                "reconciliation_receipt_sha256": reconciliation.receipt_sha256,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
