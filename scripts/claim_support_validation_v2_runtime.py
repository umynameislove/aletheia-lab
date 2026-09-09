#!/usr/bin/env python3
"""Materialize or verify the outcome-blind claim-support V2 runtime freeze."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    RUNTIME_MANIFEST_PATH,
    RUNTIME_READINESS_PATH,
    ClaimValidationV2RuntimeError,
    build_v2_runtime_manifest,
    build_v2_runtime_readiness,
    canonical_json,
    verify_tracked_v2_runtime,
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
    manifest = build_v2_runtime_manifest(root)
    readiness = build_v2_runtime_readiness(manifest)
    manifest_disposition = publish_immutable_file(
        root / RUNTIME_MANIFEST_PATH,
        canonical_json(manifest).encode("utf-8"),
    )
    readiness_disposition = publish_immutable_file(
        root / RUNTIME_READINESS_PATH,
        canonical_json(readiness).encode("utf-8"),
    )
    return {
        **readiness.model_dump(mode="json"),
        "manifest_disposition": manifest_disposition,
        "readiness_disposition": readiness_disposition,
    }


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        payload = (
            _materialize(root)
            if args.command == "materialize"
            else verify_tracked_v2_runtime(root)
        )
    except (
        ClaimValidationV2RuntimeError,
        ImmutablePublicationConflictError,
        ImmutablePublicationIntegrityError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "status": "claim_support_validation_v2_runtime_verification_failed",
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
