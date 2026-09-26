"""Run the offline evaluator-mapping symptom-matching study once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.score_mapping_development_symptoms import (
    run_development_symptom_study,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_development_symptom_study(root=args.root, prior=args.prior, output=args.output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "cell_count": result["cell_count"],
                "candidate_selected_shards": result["candidate_selected_shards"],
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
