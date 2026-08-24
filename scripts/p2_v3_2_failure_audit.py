#!/usr/bin/env python3
"""Verify the tracked v3.2 technical-failure audit against local evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_2_failure import (
    DEFAULT_V3_2_FAILURE_AUDIT_PATH,
    DEFAULT_V3_2_TERMINAL_STORE_PATH,
    load_v3_2_failure_audit,
    verify_v3_2_failure_audit,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--audit", type=Path, default=DEFAULT_V3_2_FAILURE_AUDIT_PATH)
    parser.add_argument("--terminal-store", type=Path, default=DEFAULT_V3_2_TERMINAL_STORE_PATH)
    args = parser.parse_args()
    root = args.root.resolve()
    audit_path = args.audit if args.audit.is_absolute() else root / args.audit
    audit = verify_v3_2_failure_audit(
        load_v3_2_failure_audit(audit_path),
        root=root,
        terminal_store_path=args.terminal_store,
    )
    print(
        json.dumps(
            {
                "status": "v3_2_technical_failure_audit_verified",
                "audit_sha256": audit.canonical_sha256(),
                "protocol_sha256": audit.protocol_sha256,
                "terminal_store_sha256": audit.terminal_store_sha256,
                "failure_stage": audit.failure_stage,
                "root_cause_classification": audit.root_cause_classification,
                "scientific_disposition_generated": audit.scientific_disposition_generated,
                "rerun_forbidden": audit.rerun_forbidden,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
