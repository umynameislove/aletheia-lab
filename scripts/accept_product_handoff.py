#!/usr/bin/env python3
"""Read-only current product acceptance against a byte-pinned trusted reference."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from aletheia_lab.evaluation.model_load_research_acceptance import read_document
from aletheia_lab.evaluation.product_handoff_acceptance import (
    MAX_VIEW_BYTES,
    check_product_handoff,
)
from aletheia_lab.project.identity import content_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--expected-reference-sha256", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    try:
        if re.fullmatch(r"[0-9a-f]{64}", args.expected_reference_sha256) is None:
            raise ValueError("invalid reference identity")
        reference_bytes, reference = read_document(args.reference, limit=MAX_VIEW_BYTES)
        if content_sha256(reference_bytes) != args.expected_reference_sha256:
            raise ValueError("trusted reference bytes differ from the supplied pin")
        candidate_bytes, candidate = read_document(args.candidate, limit=MAX_VIEW_BYTES)
        result = check_product_handoff(reference, candidate)
        if (
            read_document(args.reference, limit=MAX_VIEW_BYTES)[0] != reference_bytes
            or read_document(args.candidate, limit=MAX_VIEW_BYTES)[0] != candidate_bytes
        ):
            raise ValueError("product input changed during acceptance")
        result["reference_file_sha256"] = args.expected_reference_sha256
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, OSError, KeyError, RecursionError):
        print(json.dumps({"status": "product_handoff_acceptance_failed_closed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
