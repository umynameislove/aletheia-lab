#!/usr/bin/env python3
"""Verify the preserved v3.1 technical-failure evidence without rerunning it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_recovery import (
    DEFAULT_V3_1_FAILURE_RECEIPT_PATH,
    load_v3_technical_failure_receipt,
    verify_v3_technical_failure_receipt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--receipt", type=Path, default=DEFAULT_V3_1_FAILURE_RECEIPT_PATH
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    receipt_path = args.receipt if args.receipt.is_absolute() else root / args.receipt
    receipt = load_v3_technical_failure_receipt(receipt_path)
    verified = verify_v3_technical_failure_receipt(receipt, root=root)
    print(
        json.dumps(
            {
                "status": "v3_1_technical_failure_preserved",
                "failure_receipt_sha256": verified.canonical_sha256(),
                "protocol_sha256": verified.protocol_sha256,
                "execution_commit": verified.execution_commit,
                "affected_cell_count": len(verified.affected_cell_census),
                "result_store_published": verified.result_store_published,
                "scientific_disposition_generated": (
                    verified.scientific_disposition_generated
                ),
                "rerun_forbidden": verified.rerun_forbidden,
                "recovery_scope": verified.recovery_scope,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
