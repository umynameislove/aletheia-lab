"""Validate the outcome-free shift-aware label-noise study design."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_design import (
    DEFAULT_V3_DESIGN_PATH,
    load_v3_study_design,
    normal_approximation_power,
    verify_v3_predecessor,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--design", type=Path, default=DEFAULT_V3_DESIGN_PATH)
    args = parser.parse_args()
    root = args.root.resolve()
    design_path = args.design if args.design.is_absolute() else root / args.design
    design = load_v3_study_design(design_path)
    verify_v3_predecessor(design, root=root)
    planned_power = normal_approximation_power(
        replicate_count=design.inference.replicate_count_per_cell,
        standardized_effect=design.inference.target_standardized_effect,
        one_sided_alpha=design.inference.worst_case_direction_alpha,
    )
    report = {
        "status": "outcome_blind_design_ready_for_methods_review",
        "schema_version": design.schema_version,
        "design_sha256": design.canonical_sha256(),
        "v2_result_store_sha256": design.predecessor.v2_result_store_sha256,
        "new_dataset_ids": [item.dataset_id for item in design.new_datasets],
        "primary_model": design.models.primary_model,
        "secondary_sensitivity_model": design.models.secondary_sensitivity_model,
        "v2_opened_datasets_excluded": list(design.excluded_confirmation_datasets),
        "corruption_cell_count_per_dataset": (
            len(design.corruption.directions) * len(design.corruption.conditional_rates)
        ),
        "corruption_replicates_per_cell": design.corruption.corruption_seeds.count,
        "prior_environment_count": len(design.prior_environments.odds_multipliers),
        "shift_baseline_count": len(design.shift_estimators.baselines),
        "planned_normal_approximation_power": planned_power,
        "target_power": design.inference.target_power,
        "registration_authorized": False,
        "execution_authorized": False,
        "outcomes_generated": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
