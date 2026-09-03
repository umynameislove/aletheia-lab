#!/usr/bin/env python3
"""Render or verify zero-outcome claim-corpus materialization readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusContractError
from aletheia_lab.evaluation.claim_corpus_readiness import (
    publish_readiness_artifacts,
    verify_readiness,
)

ROOT: Final = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("render", "verify", "require-ready"))
    args = parser.parse_args()
    try:
        if args.command == "render":
            payload: object = {"artifacts": publish_readiness_artifacts(ROOT)}
        else:
            receipt = verify_readiness(ROOT)
            payload = receipt.model_dump(mode="json")
    except ClaimCorpusContractError as exc:
        print(
            json.dumps(
                {
                    "status": "claim_corpus_materialization_not_ready",
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
