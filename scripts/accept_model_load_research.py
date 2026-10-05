#!/usr/bin/env python3
"""Read-only pinned research replay and selected synthetic-view acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_handoff_acceptance import check_synthetic_product_view
from aletheia_lab.evaluation.model_load_research_acceptance import (
    SCHEMAS,
    accept_receipt,
    read_document,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    research = commands.add_parser("receipt")
    research.add_argument("--root", type=Path, required=True)
    research.add_argument("--kind", choices=tuple(SCHEMAS), required=True)
    research.add_argument("--receipt", type=Path, required=True)
    research.add_argument("--expected-sha256", required=True)
    research.add_argument("--identity-only", action="store_true")
    research.add_argument("--plan", type=Path)
    research.add_argument("--study", type=Path)
    research.add_argument("--parent-results-sha256")
    view = commands.add_parser("synthetic-view")
    view.add_argument("--reference", type=Path, required=True)
    view.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "receipt":
            result = accept_receipt(
                args.root,
                args.receipt,
                args.kind,
                args.expected_sha256,
                replay=not args.identity_only,
                plan=args.plan,
                study=args.study,
                parent_results_sha256=args.parent_results_sha256,
            )
        else:
            reference_bytes, reference = read_document(args.reference, limit=1_048_576)
            candidate_bytes, candidate = read_document(args.candidate, limit=1_048_576)
            result = check_synthetic_product_view(reference, candidate)
            if (
                args.reference.read_bytes() != reference_bytes
                or args.candidate.read_bytes() != candidate_bytes
            ):
                raise ValueError("view input mutated during acceptance")
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (
        ValueError,
        RuntimeError,
        KeyError,
        AttributeError,
        OSError,
        TypeError,
        ImportError,
        RecursionError,
    ):
        print(json.dumps({"status": "research_acceptance_failed_closed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
