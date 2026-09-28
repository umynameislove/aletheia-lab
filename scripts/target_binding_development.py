"""Run target-only development on pinned train/development sources, offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.target_binding_development import run_target_binding_development


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matched-prior", type=Path)
    parser.add_argument("--matched-prior-sha256")
    args = parser.parse_args()
    result = run_target_binding_development(
        root=args.root,
        prior=args.prior,
        output=args.output,
        matched_prior=args.matched_prior,
        expected_matched_sha256=args.matched_prior_sha256,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "cell_count", "provider_calls", "independently_admitted")
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
