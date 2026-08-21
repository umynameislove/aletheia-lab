#!/usr/bin/env python3
"""Acquire or verify the outcome-free v3 dataset bindings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DEFAULT_V3_DATASET_BINDINGS_PATH,
    DEFAULT_V3_DATASET_RECEIPT_PATH,
    acquire_v3_dataset_archives,
    build_v3_dataset_binding_receipt,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
    verify_v3_dataset_binding_design,
    verify_v3_dataset_binding_receipt,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("acquire", "audit", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_V3_DATASET_RECEIPT_PATH)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/p2-v3"))
    args = parser.parse_args()

    root = args.root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    receipt_path = args.receipt if args.receipt.is_absolute() else root / args.receipt
    data_dir = args.data_dir if args.data_dir.is_absolute() else root / args.data_dir
    manifest = load_v3_dataset_binding_manifest(manifest_path)
    verify_v3_dataset_binding_design(manifest, root=root)

    if args.command == "acquire":
        paths = acquire_v3_dataset_archives(manifest, destination=data_dir)
        report: dict[str, object] = {
            "status": "pinned_archives_acquired",
            "archives": [str(path) for path in paths],
        }
    else:
        observed = build_v3_dataset_binding_receipt(
            manifest,
            archive_directory=data_dir,
        )
        if args.command == "verify":
            expected = load_v3_dataset_binding_receipt(receipt_path)
            verify_v3_dataset_binding_receipt(observed, expected)
            status = "dataset_binding_verified"
        else:
            status = "dataset_binding_audited_not_registered"
        report = {
            "status": status,
            "manifest_sha256": manifest.canonical_sha256(),
            "receipt_sha256": observed.canonical_sha256(),
            "datasets": [
                {
                    "dataset_id": item.dataset_id,
                    "row_count": item.row_count,
                    "class_counts": item.class_counts,
                    "analysis_feature_count": len(item.analysis_feature_columns),
                    "excluded_features": list(item.excluded_feature_columns),
                    "duplicate_group_count": item.duplicate_group_count,
                    "conflicting_target_duplicate_group_count": (
                        item.conflicting_target_duplicate_group_count
                    ),
                    "eligible": item.eligible,
                }
                for item in observed.datasets
            ],
            "model_fitted": observed.model_fitted,
            "predictive_metrics_generated": observed.predictive_metrics_generated,
            "sealed_outcomes_generated": observed.sealed_outcomes_generated,
            "registration_authorized": observed.registration_authorized,
            "execution_authorized": observed.execution_authorized,
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
