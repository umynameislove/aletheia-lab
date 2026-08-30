#!/usr/bin/env python3
"""Audit public research claims against frozen evidence and denominators."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.governance.claims import (
    audit_public_claim_registry,
    load_public_claim_registry,
)

DEFAULT_REGISTRY = Path("configs/governance/public_claim_registry_v1.json")
DEFAULT_FILTER = Path("configs/benchmark/provenance/p4_p5_mechanism_filter_manifest.json")
DEFAULT_P2R_SUMMARY = Path("configs/benchmark/provenance/p2r_v1_2_publication_summary.json")


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--mechanism-filter", type=Path, default=DEFAULT_FILTER)
    parser.add_argument("--p2r-summary", type=Path, default=DEFAULT_P2R_SUMMARY)
    args = parser.parse_args()

    root = args.root.resolve()
    registry = load_public_claim_registry(_resolve(root, args.registry))
    audit = audit_public_claim_registry(
        root=root,
        registry=registry,
        mechanism_filter_path=_resolve(root, args.mechanism_filter),
        p2r_summary_path=_resolve(root, args.p2r_summary),
    )
    print(json.dumps(audit.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0 if audit.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
