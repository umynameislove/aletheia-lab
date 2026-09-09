#!/usr/bin/env python3
"""Materialize or verify the offline V2 source-claim expressiveness review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.claim_validation_v2_expressiveness import (
    EXPRESSIVENESS_AMENDMENT_PATH,
    EXPRESSIVENESS_REVIEW_PATH,
    ClaimValidationV2ExpressivenessError,
    build_v2_expressiveness_amendment,
    build_v2_expressiveness_review,
    canonical_json,
    verify_tracked_v2_expressiveness,
)
from aletheia_lab.filesystem import (
    ImmutablePublicationConflictError,
    ImmutablePublicationIntegrityError,
    publish_immutable_file,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("materialize", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def _materialize(root: Path) -> dict[str, object]:
    amendment = build_v2_expressiveness_amendment(root)
    review = build_v2_expressiveness_review(root, amendment)
    amendment_disposition = publish_immutable_file(
        root / EXPRESSIVENESS_AMENDMENT_PATH,
        canonical_json(amendment).encode("utf-8"),
    )
    review_disposition = publish_immutable_file(
        root / EXPRESSIVENESS_REVIEW_PATH,
        canonical_json(review).encode("utf-8"),
    )
    return {
        **review.model_dump(mode="json"),
        "amendment_disposition": amendment_disposition,
        "review_disposition": review_disposition,
    }


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        payload = (
            _materialize(root)
            if args.command == "materialize"
            else verify_tracked_v2_expressiveness(root)
        )
    except (
        ClaimValidationV2ExpressivenessError,
        ImmutablePublicationConflictError,
        ImmutablePublicationIntegrityError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "status": "claim_support_validation_v2_expressiveness_failed",
            "error": type(exc).__name__,
            "message": str(exc),
            "provider_calls_executed": False,
            "claims_materialized": False,
            "blind_packets_generated": False,
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
        return 2
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
