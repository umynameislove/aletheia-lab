#!/usr/bin/env python3
"""Verify the outcome-blind claim-support corpus protocol and readiness receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

from aletheia_lab.evaluation.claim_corpus_protocol import (
    ClaimCorpusProtocolError,
    verify_tracked_claim_support_corpus_protocol,
)

ROOT: Final = Path(__file__).resolve().parents[1]
PROTOCOL_PATH: Final = ROOT / "configs/evaluation/claim_support_corpus_protocol.json"
RECEIPT_PATH: Final = (
    ROOT / "configs/evaluation/claim_support_corpus_feasibility_receipt.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("verify", "require-materialization-ready"),
        help="verify the freeze or additionally require every materialization blocker to be clear",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = verify_tracked_claim_support_corpus_protocol(
            ROOT,
            PROTOCOL_PATH,
            RECEIPT_PATH,
        )
    except ClaimCorpusProtocolError as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "status": "claim_support_corpus_protocol_invalid",
                },
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(receipt.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True))
    if args.command == "require-materialization-ready" and not receipt.materialization_ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
