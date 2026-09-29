#!/usr/bin/env python3
"""Audit the pinned M5 development reader boundary without re-running a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.score_mapping_observation_audit import (
    audit_development_observation_summaries,
)
from aletheia_lab.content_hashing import file_sha256

MAPPING_SHA256 = "aa891dcec952caefb54b1ccb11470fc22955d738b3e3b49ee961cfe6d4a31d50"
TARGET_SHA256 = "dcd43c47dfeb26f11b123e61c719ad83ed1d88a00aa3422287b4088b4e93c1bf"


def _load_pinned(path: Path, expected_sha256: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or file_sha256(path) != expected_sha256:
        raise ValueError("a pinned development summary is missing or changed")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("a pinned development summary is malformed")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping-summary", required=True, type=Path)
    parser.add_argument("--target-summary", required=True, type=Path)
    args = parser.parse_args()
    mapping = _load_pinned(args.mapping_summary, MAPPING_SHA256)
    target = _load_pinned(args.target_summary, TARGET_SHA256)
    result = audit_development_observation_summaries(
        mapping, target, mapping_summary_sha256=MAPPING_SHA256
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
