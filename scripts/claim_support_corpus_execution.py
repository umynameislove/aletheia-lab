#!/usr/bin/env python3
"""Inspect or rehearse the development claim-corpus execution boundary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionError,
    build_execution_preflight,
    inspect_repository_state,
    rehearse_execution,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("preflight", "rehearse", "require-live-ready"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        if args.command == "rehearse":
            result = rehearse_execution(root)
        else:
            result = build_execution_preflight(
                root,
                repository_state=inspect_repository_state(root),
                credential_present=bool(os.environ.get("OPENAI_API_KEY")),
            )
    except ClaimCorpusExecutionError as exc:
        print(
            json.dumps(
                {"status": "claim_corpus_execution_preflight_failed", "error": str(exc)},
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if args.command == "require-live-ready" and result.live_blockers:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
