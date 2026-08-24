#!/usr/bin/env python3
"""Preserve or verify immutable v3.3 evidence without executing the study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_3_preservation import (
    DEFAULT_PRESERVATION_ROOT,
    DEFAULT_PUBLICATION_SUMMARY_PATH,
    DEFAULT_TERMINAL_STORE_PATH,
    load_v3_3_publication_summary,
    preservation_destination,
    preserve_v3_3_evidence,
    verify_preserved_v3_3,
    verify_v3_3_publication_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preserve", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preservation-root", type=Path, default=DEFAULT_PRESERVATION_ROOT)
    parser.add_argument("--terminal-store", type=Path, default=DEFAULT_TERMINAL_STORE_PATH)
    parser.add_argument("--summary", type=Path, default=DEFAULT_PUBLICATION_SUMMARY_PATH)
    args = parser.parse_args()
    root = args.root.resolve()
    preservation_root = (
        args.preservation_root
        if args.preservation_root.is_absolute()
        else root / args.preservation_root
    )
    terminal_store = args.terminal_store if args.terminal_store.is_absolute() else root / args.terminal_store
    summary_path = args.summary if args.summary.is_absolute() else root / args.summary
    if args.command == "preserve":
        receipt = preserve_v3_3_evidence(
            root=root,
            preservation_root=preservation_root,
            terminal_store_path=terminal_store,
        )
    else:
        receipt = verify_preserved_v3_3(preservation_destination(preservation_root))
    summary = verify_v3_3_publication_summary(
        load_v3_3_publication_summary(summary_path),
        terminal_store_path=terminal_store,
    )
    print(
        json.dumps(
            {
                "status": "v3_3_evidence_preserved_and_verified",
                "preservation_receipt_sha256": receipt.canonical_sha256(),
                "publication_summary_sha256": summary.canonical_sha256(),
                "terminal_store_sha256": receipt.terminal_store_sha256,
                "disposition": summary.disposition,
                "cross_dataset_claim_allowed": summary.cross_dataset_claim_allowed,
                "rerun_forbidden": summary.rerun_forbidden,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
