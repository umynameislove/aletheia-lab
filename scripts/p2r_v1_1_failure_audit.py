#!/usr/bin/env python3
"""Compile and verify the outcome-free P2R v1.1 replication-failure audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    DEFAULT_V3_3_PROTOCOL_PATH,
    load_v3_3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DEFAULT_V3_DATASET_BINDINGS_PATH,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import reconstruct_runtime_split
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    load_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_replication_failure import (
    DEFAULT_P2R_V1_1_FAILURE_AUDIT_PATH,
    DEFAULT_P2R_V1_1_FEASIBILITY_PATH,
    DEFAULT_P2R_V1_1_MARKER_PATH,
    DEFAULT_P2R_V1_1_REGISTRATION_PATH,
    DEFAULT_P2R_V1_1_TERMINAL_STORE_PATH,
    P2RInterventionFeasibilityReceipt,
    build_intervention_feasibility_receipt,
    load_intervention_feasibility_receipt,
    load_p2r_v1_1_replication_failure_audit,
    verify_p2r_v1_1_replication_failure_audit,
)

DEFAULT_DATA_DIR = Path("data/raw/p2-v3")


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _compile(args: argparse.Namespace, root: Path) -> P2RInterventionFeasibilityReceipt:
    manifest = load_v3_dataset_binding_manifest(_resolve(root, args.manifest))
    split_protocol = load_v3_3_confirmatory_protocol(_resolve(root, args.split_protocol))
    drift = load_lightweight_confirmatory_protocol(_resolve(root, args.drift_protocol))
    preprocessing = load_lightweight_confirmatory_protocol(
        _resolve(root, args.preprocessing_protocol)
    )
    frames = {}
    for binding in manifest.datasets:
        _, frame = load_v3_dataset_snapshot_for_registration(
            dataset=binding,
            archive_path=_resolve(root, args.data_dir) / binding.archive.file_name,
        )
        split_receipt = next(
            item
            for item in split_protocol.dataset_splits
            if item.dataset_id == binding.dataset_id
        )
        split = reconstruct_runtime_split(
            protocol=split_protocol,
            dataset=binding,
            frame=frame,
            receipt=split_receipt,
        )
        # Restrict the compiler interface to covariates that define the two
        # categorical interventions.  Numeric predictors and target labels
        # cannot enter capacity measurement or target-feature selection.
        features = frame.loc[:, list(binding.categorical_features)]
        frames[binding.dataset_id] = (
            features.iloc[list(split.indices("train"))].reset_index(drop=True),
            features.iloc[list(split.indices("sealed_test"))].reset_index(drop=True),
        )
    return build_intervention_feasibility_receipt(
        manifest=manifest,
        protocols=(drift, preprocessing),
        frames=frames,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("compile-feasibility", "verify-feasibility", "verify-failure", "readiness"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--split-protocol", type=Path, default=DEFAULT_V3_3_PROTOCOL_PATH)
    parser.add_argument("--drift-protocol", type=Path, default=DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    parser.add_argument(
        "--preprocessing-protocol",
        type=Path,
        default=DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--feasibility", type=Path, default=DEFAULT_P2R_V1_1_FEASIBILITY_PATH)
    parser.add_argument("--failure-audit", type=Path, default=DEFAULT_P2R_V1_1_FAILURE_AUDIT_PATH)
    parser.add_argument(
        "--registration", type=Path, default=DEFAULT_P2R_V1_1_REGISTRATION_PATH
    )
    parser.add_argument("--marker", type=Path, default=DEFAULT_P2R_V1_1_MARKER_PATH)
    parser.add_argument(
        "--terminal-store", type=Path, default=DEFAULT_P2R_V1_1_TERMINAL_STORE_PATH
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    if args.command == "compile-feasibility":
        result: object = _compile(args, root).model_dump(mode="json")
    elif args.command == "verify-feasibility":
        tracked = load_intervention_feasibility_receipt(_resolve(root, args.feasibility))
        reproduced = _compile(args, root)
        if reproduced != tracked:
            raise SystemExit("tracked P2R feasibility receipt differs from pinned archives")
        result = {
            "status": "p2r_v1_1_intervention_feasibility_verified",
            "receipt_sha256": tracked.canonical_sha256(),
            "selected_target_features": {
                item.dataset_id: item.selected_target_feature for item in tracked.datasets
            },
            "frozen_target_feasibility": {
                item.dataset_id: item.frozen_target_jointly_feasible
                for item in tracked.datasets
            },
            "target_values_used_for_capacity_or_selection": (
                tracked.target_values_used_for_capacity_or_selection
            ),
            "model_fitted": tracked.model_fitted,
            "predictive_metrics_generated": tracked.predictive_metrics_generated,
        }
    else:
        feasibility = load_intervention_feasibility_receipt(
            _resolve(root, args.feasibility)
        )
        audit = verify_p2r_v1_1_replication_failure_audit(
            load_p2r_v1_1_replication_failure_audit(
                _resolve(root, args.failure_audit)
            ),
            root=root,
            feasibility=feasibility,
            registration_path=_resolve(root, args.registration),
            marker_path=_resolve(root, args.marker),
            terminal_store_path=_resolve(root, args.terminal_store),
        )
        result = {
            "status": "p2r_v1_1_replication_failure_audit_verified",
            "audit_sha256": audit.canonical_sha256(),
            "terminal_store_sha256": audit.terminal_store_sha256,
            "failure_stage": audit.failure_stage,
            "root_cause_classification": audit.root_cause_classification,
            "failed_dataset_id": audit.failed_dataset_id,
            "failed_mechanism": audit.failed_mechanism,
            "failed_feature": audit.failed_feature,
            "declared_target_row_count": audit.declared_target_row_count,
            "eligible_row_count": audit.eligible_row_count,
            "capacity_shortfall": audit.capacity_shortfall,
            "replacement_feature": audit.replacement_feature,
            "scientific_negative_result": audit.scientific_negative_result,
            "scientific_semantics_changed_by_repair": (
                audit.scientific_semantics_changed_by_repair
            ),
            "rerun_forbidden": audit.rerun_forbidden,
            "required_successor_scope": audit.required_successor_scope,
        }
        if args.command == "readiness":
            reproduced = _compile(args, root)
            if reproduced != feasibility:
                raise SystemExit("tracked P2R feasibility receipt differs from pinned archives")
            result["status"] = "p2r_v1_2_methodological_amendment_ready_to_design"
            result["feasibility_receipt_sha256"] = feasibility.canonical_sha256()
            result["predictive_outcomes_inspected_for_repair"] = (
                audit.predictive_outcomes_inspected_for_repair
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
