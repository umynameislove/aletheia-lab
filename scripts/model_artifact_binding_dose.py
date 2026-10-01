#!/usr/bin/env python3
"""Run/verify a development dose sweep or audit an unexecuted new-source design."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.model_artifact_binding_dose import (
    execute_dose_study,
    prepare_dose_study,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_dose_verify import verify_dose_study
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import publish_json
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_protocol import (
    audit_m4_new_sources,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute", "verify", "inventory"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--predecessor", type=Path)
    parser.add_argument("--confirm-receipt-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--sources", type=Path)
    args = parser.parse_args()
    if args.action == "inventory":
        if args.sources is None:
            parser.error("inventory requires --sources")
    elif any(
        value is None
        for value in (args.archive, args.predecessor, args.confirm_receipt_sha256, args.output)
    ):
        parser.error(
            "dose operations require --archive --predecessor --confirm-receipt-sha256 --output"
        )
    if args.action == "execute" and args.confirm_plan_sha256 is None:
        parser.error("execute requires --confirm-plan-sha256")
    try:
        if args.action == "inventory":
            result = audit_m4_new_sources(root=args.root, sources=args.sources)
            if args.output is not None:
                publish_json(args.output, result)
        else:
            options = {
                "root": args.root,
                "archive": args.archive,
                "predecessor": args.predecessor,
                "expected_receipt_sha256": args.confirm_receipt_sha256,
                "output": args.output,
            }
            if args.action == "prepare":
                result = prepare_dose_study(**options)
            else:
                if args.action == "execute":
                    execute_dose_study(**options, confirm_plan_sha256=args.confirm_plan_sha256)
                result = verify_dose_study(**options)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
