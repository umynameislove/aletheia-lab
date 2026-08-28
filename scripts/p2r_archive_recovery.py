#!/usr/bin/env python3
"""Verify P2R v1 failure evidence and compile outcome-free archive readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DEFAULT_V3_DATASET_BINDINGS_PATH,
    DEFAULT_V3_DATASET_RECEIPT_PATH,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    DEFAULT_P2R_FAILURE_AUDIT_PATH,
    DEFAULT_P2R_READINESS_PATH,
    DEFAULT_P2R_V1_STORE_PATH,
    build_p2r_archive_readiness,
    load_archive_readiness,
    load_p2r_v1_failure_audit,
    verify_p2r_archive_readiness,
    verify_p2r_v1_failure_audit,
    write_archive_readiness_exclusive,
)

DEFAULT_DATA_DIR = Path("data/raw/p2-v3")


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("verify-failure", "compile-readiness", "verify-readiness")
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--dataset-receipt", type=Path, default=DEFAULT_V3_DATASET_RECEIPT_PATH)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--readiness", type=Path, default=DEFAULT_P2R_READINESS_PATH)
    parser.add_argument("--failure-audit", type=Path, default=DEFAULT_P2R_FAILURE_AUDIT_PATH)
    parser.add_argument("--terminal-store", type=Path, default=DEFAULT_P2R_V1_STORE_PATH)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    if args.command == "verify-failure":
        audit = verify_p2r_v1_failure_audit(
            load_p2r_v1_failure_audit(_resolve(root, args.failure_audit)),
            root=root,
            terminal_store_path=args.terminal_store,
        )
        result: dict[str, object] = {
            "status": "p2r_v1_technical_failure_audit_verified",
            "audit_sha256": audit.canonical_sha256(),
            "terminal_store_sha256": audit.terminal_store_sha256,
            "failure_stage": audit.failure_stage,
            "root_cause_classification": audit.root_cause_classification,
            "scientific_disposition_generated": audit.scientific_disposition_generated,
            "rerun_forbidden": audit.rerun_forbidden,
        }
    else:
        manifest = load_v3_dataset_binding_manifest(_resolve(root, args.manifest))
        dataset_receipt = load_v3_dataset_binding_receipt(_resolve(root, args.dataset_receipt))
        data_dir = _resolve(root, args.data_dir)
        readiness_path = _resolve(root, args.readiness)
        if args.command == "compile-readiness":
            readiness = build_p2r_archive_readiness(
                manifest=manifest,
                pinned_receipt=dataset_receipt,
                archive_directory=data_dir,
            )
            write_archive_readiness_exclusive(readiness_path, readiness)
        else:
            readiness = verify_p2r_archive_readiness(
                load_archive_readiness(readiness_path),
                manifest=manifest,
                pinned_receipt=dataset_receipt,
                archive_directory=data_dir,
            )
        result = {
            "status": "p2r_archive_readiness_verified",
            "readiness_sha256": readiness.canonical_sha256(),
            "dataset_ids": list(readiness.dataset_ids),
            "all_pinned_archives_reproduced": readiness.all_pinned_archives_reproduced,
            "sealed_partition_opened": readiness.sealed_partition_opened,
            "model_fitted": readiness.model_fitted,
            "predictive_metrics_generated": readiness.predictive_metrics_generated,
            "execution_attempt_consumed": readiness.execution_attempt_consumed,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
