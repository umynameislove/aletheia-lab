#!/usr/bin/env python3
"""Verify, prepare, or publish the development claim-support pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from aletheia_lab.evaluation.claim_corpus_construction import (
    ClaimPoolConstructionError,
    ClaimPoolPreparation,
    ClaimRelationResultBundle,
    build_claim_pool_preparation,
    publish_claim_pool,
    verify_construction_inputs,
)
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
    ClaimCorpusRequestReconciliationReceipt,
    ClaimCorpusReserveDecisionReceipt,
)
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "prepare", "publish"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--evidence-census", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--lease", type=Path)
    parser.add_argument("--live-receipt", type=Path)
    parser.add_argument("--reserve-receipt", type=Path)
    parser.add_argument("--reconciliation-receipt", type=Path)
    parser.add_argument("--attempt-store", type=Path)
    parser.add_argument("--preparation", type=Path)
    parser.add_argument("--relation-results", type=Path)
    parser.add_argument("--pool-store", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def _outside_repository(root: Path, path: Path, *, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise ClaimPoolConstructionError(f"{label} must remain outside the repository")


def _load_model(path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("artifact is not a regular file")
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimPoolConstructionError("construction input is unavailable or invalid") from exc


def _required(args: argparse.Namespace, *names: str) -> None:
    missing = tuple(name for name in names if getattr(args, name) is None)
    if missing:
        raise ClaimPoolConstructionError(
            f"{args.command} requires: {', '.join(name.replace('_', '-') for name in missing)}"
        )


def _load_execution_inputs(
    args: argparse.Namespace,
    root: Path,
) -> tuple[
    ObservedEvidenceCensus,
    ClaimCorpusExecutionAuthorization,
    ClaimCorpusExecutionLease,
    ClaimCorpusLiveReceipt,
    ClaimCorpusReserveDecisionReceipt,
    ClaimCorpusRequestReconciliationReceipt,
    Path,
]:
    _required(
        args,
        "evidence_census",
        "authorization",
        "lease",
        "live_receipt",
        "reserve_receipt",
        "reconciliation_receipt",
        "attempt_store",
    )
    evidence = load_execution_evidence_census(root, args.evidence_census)
    authorization = load_execution_authorization(args.authorization)
    lease = ClaimCorpusExecutionLease.model_validate(
        _load_model(args.lease, ClaimCorpusExecutionLease).model_dump(mode="python")
    )
    live = ClaimCorpusLiveReceipt.model_validate(
        _load_model(args.live_receipt, ClaimCorpusLiveReceipt).model_dump(mode="python")
    )
    reserve = ClaimCorpusReserveDecisionReceipt.model_validate(
        _load_model(
            args.reserve_receipt, ClaimCorpusReserveDecisionReceipt
        ).model_dump(mode="python")
    )
    reconciliation = ClaimCorpusRequestReconciliationReceipt.model_validate(
        _load_model(
            args.reconciliation_receipt,
            ClaimCorpusRequestReconciliationReceipt,
        ).model_dump(mode="python")
    )
    store = _outside_repository(root, args.attempt_store, label="attempt store")
    return evidence, authorization, lease, live, reserve, reconciliation, store


def _publish(path: Path, value: BaseModel) -> str:
    payload = (
        canonical_project_json(value.model_dump(mode="json")) + "\n"
    ).encode("utf-8")
    return publish_immutable_file(path, payload)


def _preflight(args: argparse.Namespace, root: Path) -> dict[str, object]:
    evidence, authorization, lease, live, reserve, reconciliation, store = (
        _load_execution_inputs(args, root)
    )
    verify_construction_inputs(
        root,
        store_root=store,
        authorization=authorization,
        lease=lease,
        live_receipt=live,
        reserve_receipt=reserve,
        reconciliation_receipt=reconciliation,
        evidence_census=evidence,
    )
    return {
        "status": "claim_pool_construction_ready",
        "terminal_request_count": reconciliation.terminal_request_count,
        "parsed_terminal_count": reconciliation.parsed_terminal_count,
        "technical_failure_terminal_count": (
            reconciliation.technical_failure_terminal_count
        ),
        "failures_preserved_in_denominator": True,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
        "reconciliation_receipt_sha256": reconciliation.receipt_sha256,
    }


def _prepare(args: argparse.Namespace, root: Path) -> dict[str, object]:
    _required(args, "output")
    evidence, authorization, lease, live, reserve, reconciliation, store = (
        _load_execution_inputs(args, root)
    )
    result = build_claim_pool_preparation(
        root,
        store_root=store,
        authorization=authorization,
        lease=lease,
        live_receipt=live,
        reserve_receipt=reserve,
        reconciliation_receipt=reconciliation,
        evidence_census=evidence,
    )
    output = _outside_repository(root, args.output, label="preparation output")
    disposition = _publish(output, result)
    return {
        "status": "claim_pool_preparation_complete",
        "output": str(output),
        "publication_disposition": disposition,
        "preparation_sha256": result.preparation_sha256,
        "normalized_output_count": result.normalized_output_count,
        "normalization_rejection_count": result.normalization_rejection_count,
        "claim_candidate_count": result.claim_candidate_count,
        "relation_request_count": result.relation_request_count,
        "technical_failure_terminal_count": result.technical_failure_terminal_count,
        "automatic_labels_generated": result.automatic_labels_generated,
        "blind_packets_generated": result.blind_packets_generated,
        "human_annotations_collected": result.human_annotations_collected,
        "main_or_sealed_outcomes_opened": result.main_or_sealed_outcomes_opened,
    }


def _publish_pool(args: argparse.Namespace, root: Path) -> dict[str, object]:
    _required(args, "preparation", "relation_results", "pool_store", "output")
    preparation_path = _outside_repository(
        root, args.preparation, label="preparation input"
    )
    relation_path = _outside_repository(
        root, args.relation_results, label="relation-result input"
    )
    pool_store = _outside_repository(root, args.pool_store, label="pool store")
    output = _outside_repository(root, args.output, label="publication closeout")
    preparation = ClaimPoolPreparation.model_validate(
        _load_model(preparation_path, ClaimPoolPreparation).model_dump(mode="python")
    )
    relation_results = ClaimRelationResultBundle.model_validate(
        _load_model(relation_path, ClaimRelationResultBundle).model_dump(mode="python")
    )
    closeout = publish_claim_pool(
        root,
        preparation=preparation,
        relation_results=relation_results,
        store_root=pool_store,
    )
    disposition = _publish(output, closeout)
    return {
        "status": "claim_pool_publication_complete",
        "output": str(output),
        "publication_disposition": disposition,
        "run_id": closeout.corpus_store_receipt.run_id,
        "corpus_entry_count": closeout.corpus_entry_count,
        "closeout_sha256": closeout.closeout_sha256,
        "blind_packets_generated": closeout.blind_packets_generated,
        "human_annotations_collected": closeout.human_annotations_collected,
        "main_or_sealed_outcomes_opened": closeout.main_or_sealed_outcomes_opened,
    }


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        if args.command == "preflight":
            payload = _preflight(args, root)
        elif args.command == "prepare":
            payload = _prepare(args, root)
        else:
            payload = _publish_pool(args, root)
    except (ClaimPoolConstructionError, OSError, ValueError) as exc:
        payload = {"status": "claim_pool_construction_failed", "error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
