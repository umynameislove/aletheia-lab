#!/usr/bin/env python3
"""Review or verify the prospective V3.2 source-measurement role freeze."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_support_v3_2_role import (
    FALSE_FLAGS,
    build_protocol,
    verify_protocol,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("review", "verify-protocol"))
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def _print(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key != "implementation_bindings"},
            sort_keys=True,
            indent=2,
        )
    )


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        protocol = build_protocol(root) if args.command == "review" else verify_protocol(root)
        _print(protocol)
        return 0
    except (OSError, TypeError, ValueError) as exc:
        _print(
            {
                "status": "v3_2_source_measurement_role_blocked",
                "error_type": type(exc).__name__,
                "provider_calls_executed": False,
                **FALSE_FLAGS,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
