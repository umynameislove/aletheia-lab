"""Run offline score-mapping feasibility on two datasets and model families."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.score_mapping_development import (
    run_score_mapping_development_feasibility,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_score_mapping_development_feasibility(root=args.root, output=args.output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "cell_count": result["cell_count"],
                "registered_attempt": result["registered_attempt"],
                "provider_calls": result["provider_calls"],
                "sealed_predictions_or_metrics_computed": result[
                    "sealed_predictions_or_metrics_computed"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
