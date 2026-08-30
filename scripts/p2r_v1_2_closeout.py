#!/usr/bin/env python3
"""Verify or preserve the completed P2R v1.2 study and downstream policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.downstream_disposition_v2 import (
    DEFAULT_DISPOSITION_POLICY_V2_PATH,
    DEFAULT_P4_P5_FILTER_MANIFEST_PATH,
    load_downstream_disposition_policy_v2,
    load_p4_p5_filter_manifest,
    verify_p4_p5_filter_manifest,
    verify_reconciled_downstream_policy,
)
from aletheia_lab.benchmark.p2.p2r_v1_2_results import (
    DEFAULT_PRESERVATION_ROOT,
    DEFAULT_PUBLICATION_SUMMARY_PATH,
    DEFAULT_TERMINAL_STORE_PATH,
    load_p2r_v1_2_publication_summary,
    preservation_destination,
    preserve_p2r_v1_2_evidence,
    verify_p2r_v1_2_publication_summary,
    verify_preserved_p2r_v1_2,
)


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _verified(args: argparse.Namespace) -> dict[str, object]:
    root = args.root.resolve()
    store = _resolve(root, args.store)
    summary = verify_p2r_v1_2_publication_summary(
        load_p2r_v1_2_publication_summary(_resolve(root, args.summary)),
        terminal_store_path=store,
    )
    policy = verify_reconciled_downstream_policy(
        load_downstream_disposition_policy_v2(_resolve(root, args.policy)),
        p2r_summary=summary,
    )
    filters = verify_p4_p5_filter_manifest(
        load_p4_p5_filter_manifest(_resolve(root, args.filters)),
        policy=policy,
    )
    return {
        "status": "p2r_v1_2_closeout_verified",
        "terminal_store_sha256": summary.terminal_store_sha256,
        "publication_summary_sha256": summary.canonical_sha256(),
        "disposition_policy_sha256": policy.canonical_sha256(),
        "p4_p5_filter_manifest_sha256": filters.canonical_sha256(),
        "mechanism_inventory": policy.denominators.mechanism_inventory,
        "primary_admitted_track": policy.denominators.primary_admitted_track,
        "assumption_limited_track": policy.denominators.assumption_limited_track,
        "rejected_track": policy.denominators.rejected_track,
        "pending_confirmatory_track": policy.denominators.pending_confirmatory_track,
        "diagnostic_ground_truth_track": policy.denominators.diagnostic_ground_truth_track,
        "validity_behavior_track": policy.denominators.validity_behavior_track,
        "rerun_forbidden": summary.rerun_forbidden,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "preserve"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--store", type=Path, default=DEFAULT_TERMINAL_STORE_PATH)
    parser.add_argument("--summary", type=Path, default=DEFAULT_PUBLICATION_SUMMARY_PATH)
    parser.add_argument("--policy", type=Path, default=DEFAULT_DISPOSITION_POLICY_V2_PATH)
    parser.add_argument("--filters", type=Path, default=DEFAULT_P4_P5_FILTER_MANIFEST_PATH)
    parser.add_argument("--preservation-root", type=Path, default=DEFAULT_PRESERVATION_ROOT)
    args = parser.parse_args()
    result = _verified(args)
    if args.command == "preserve":
        root = args.root.resolve()
        preservation_root = _resolve(root, args.preservation_root)
        receipt = preserve_p2r_v1_2_evidence(
            root=root,
            preservation_root=preservation_root,
            terminal_store_path=_resolve(root, args.store),
        )
        preserved = verify_preserved_p2r_v1_2(preservation_destination(preservation_root))
        result.update(
            {
                "status": "p2r_v1_2_closeout_preserved_and_verified",
                "preservation_receipt_sha256": preserved.canonical_sha256(),
                "preservation_content_address": receipt.content_address,
                "preservation_path": str(preservation_destination(preservation_root)),
                "source_store_modified": receipt.original_result_store_modified,
            }
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
