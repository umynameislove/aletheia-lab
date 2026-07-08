"""Create or validate benchmark case manifests.

Planned usage:

python scripts/create_case_manifest.py \
  --fault-type data_drift \
  --n-cases 15 \
  --output data/benchmark_cases/p1_data_drift_manifest.jsonl
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fault-type", required=True)
    parser.add_argument("--n-cases", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raise NotImplementedError(
        "Implement manifest generation after choosing the first dataset and injector. "
        f"Requested {args.n_cases} cases for {args.fault_type} -> {args.output}"
    )


if __name__ == "__main__":
    main()
