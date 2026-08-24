#!/usr/bin/env python3
"""Verify the frozen mechanism status, denominator, and abstention policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.downstream_disposition import (
    DEFAULT_DISPOSITION_POLICY_PATH,
    load_downstream_disposition_policy,
    verify_frozen_downstream_policy,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--policy", type=Path, default=DEFAULT_DISPOSITION_POLICY_PATH)
    args = parser.parse_args()
    root = args.root.resolve()
    policy_path = args.policy if args.policy.is_absolute() else root / args.policy
    policy = verify_frozen_downstream_policy(load_downstream_disposition_policy(policy_path))
    print(
        json.dumps(
            {
                "status": "p2_downstream_disposition_policy_verified",
                "policy_sha256": policy.canonical_sha256(),
                "n_inventory": policy.n_inventory,
                "n_admitted": policy.n_admitted,
                "primary_admitted_track": policy.denominators.primary_admitted_track,
                "assumption_limited_track": policy.denominators.assumption_limited_track,
                "pending_confirmatory_track": policy.denominators.pending_confirmatory_track,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
