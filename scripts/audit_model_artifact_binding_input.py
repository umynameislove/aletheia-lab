#!/usr/bin/env python3
"""Audit an already verified development artifact cell without provider calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.model_artifact_binding_observation import (
    retained_artifact_observations,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_binding_reader import audit_artifact_binding_observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cell-dir", type=Path, required=True)
    parser.add_argument("--confirm-receipt-sha256", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    before = {p.name: file_sha256(p) for p in args.cell_dir.iterdir() if p.is_file()}
    observations = retained_artifact_observations(
        root=root, cell_dir=args.cell_dir, expected_receipt_sha256=args.confirm_receipt_sha256
    )
    result = audit_artifact_binding_observations(root, observations)
    after = {p.name: file_sha256(p) for p in args.cell_dir.iterdir() if p.is_file()}
    if before != after:
        raise ValueError("retained development cell changed during its input audit")
    result.update(source_receipt_sha256=args.confirm_receipt_sha256, retained_cell_unchanged=True)
    result["audit_code_sha256"] = {
        name: file_sha256(root / name)
        for name in (
            "scripts/audit_model_artifact_binding_input.py",
            "src/aletheia_lab/benchmark/p2/model_artifact_binding_observation.py",
            "src/aletheia_lab/evaluation/artifact_binding_reader.py",
        )
    }
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
