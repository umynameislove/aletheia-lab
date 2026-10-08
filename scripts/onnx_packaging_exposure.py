"""Prepare or execute a bounded anonymous public packaging census."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.onnx_packaging_exposure import collect, prepare
from aletheia_lab.evaluation.onnx_packaging_review import verify_retained


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frozen-metadata", type=Path)
    args = parser.parse_args()
    if args.command != "prepare" and args.frozen_metadata is not None:
        parser.error("frozen metadata must be bound at prepare, not execution")
    if args.command == "verify":
        print(json.dumps(verify_retained(args.output), indent=2))
        return 0
    result = (
        prepare(args.output, args.frozen_metadata)
        if args.command == "prepare"
        else collect(args.output)
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("plan_sha256", "status", "results_sha256", "analysis")
                if key in result
            },
            indent=2,
        )
    )
    return 1 if result.get("status") == "census_incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
