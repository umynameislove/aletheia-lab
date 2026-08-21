#!/usr/bin/env python3
"""Verify the outcome-free v3 protocol candidate and frozen split receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DEFAULT_V3_PROTOCOL_PATH,
    compile_v3_split_receipts,
    load_v3_confirmatory_protocol,
    verify_compiled_split_receipts,
    verify_v3_protocol_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "compile-splits"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_V3_PROTOCOL_PATH)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/p2-v3"))
    args = parser.parse_args()

    root = args.root.resolve()
    protocol_path = args.protocol if args.protocol.is_absolute() else root / args.protocol
    data_dir = args.data_dir if args.data_dir.is_absolute() else root / args.data_dir
    protocol = load_v3_confirmatory_protocol(protocol_path)
    _, manifest, receipt = verify_v3_protocol_artifacts(protocol, root=root)
    splits_recompiled = False
    if args.command == "compile-splits":
        observed = compile_v3_split_receipts(
            manifest,
            receipt,
            archive_directory=data_dir,
        )
        verify_compiled_split_receipts(protocol, observed)
        splits_recompiled = True

    report = {
        "status": "frozen_protocol_candidate_verified_not_registered",
        "protocol_sha256": protocol.canonical_sha256(),
        "design_sha256": protocol.artifacts.design_sha256,
        "dataset_manifest_sha256": protocol.artifacts.dataset_manifest_sha256,
        "dataset_receipt_sha256": protocol.artifacts.dataset_receipt_sha256,
        "dataset_ids": [item.dataset_id for item in protocol.dataset_splits],
        "split_membership_sha256": {
            item.dataset_id: item.membership_sha256 for item in protocol.dataset_splits
        },
        "splits_recompiled_from_pinned_archives": splits_recompiled,
        "internal_outcome_blind_audit_required": (
            protocol.governance.structured_internal_outcome_blind_audit_required_before_registration
        ),
        "required_git_tag": protocol.governance.required_git_tag,
        "model_fitted": protocol.model_fitted,
        "predictive_metrics_generated": protocol.predictive_metrics_generated,
        "sealed_outcomes_generated": protocol.sealed_outcomes_generated,
        "registration_authorized": False,
        "execution_authorized": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
