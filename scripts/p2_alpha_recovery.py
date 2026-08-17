"""Reproduce the immutable primary audit and label-noise reserve recovery stores."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.alpha_execution import execute_primary_alpha, prepare_alpha_runtime
from aletheia_lab.benchmark.p2.alpha_recovery import execute_reserve_recovery
from aletheia_lab.benchmark.p2.artifacts import save_contract_store
from aletheia_lab.benchmark.p2.canonical import canonical_sha256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/project.yaml"))
    parser.add_argument("--source-output", type=Path, required=True)
    parser.add_argument("--recovery-output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    runtime = prepare_alpha_runtime(args.config)
    source = execute_primary_alpha(runtime)
    source_manifest = save_contract_store(source, args.source_output)
    recovered = execute_reserve_recovery(
        runtime=runtime,
        source=source,
        source_store_sha256=source_manifest.store_sha256,
    )
    recovery_manifest = save_contract_store(recovered, args.recovery_output)
    authorization = recovered.execution.reserve_recovery_authorization
    if authorization is None:  # pragma: no cover - construction guarantees the authorization
        raise RuntimeError("recovery execution omitted its authorization")
    reserve_ids = {
        item.candidate_id: item.slot_id
        for item in recovered.execution.executed
        if item.slot_kind == "reserve"
    }
    summary = {
        "source_store_sha256": source_manifest.store_sha256,
        "recovery_store_sha256": recovery_manifest.store_sha256,
        "source_gate_status": source.report.gate_status,
        "recovery_gate_status": recovered.report.gate_status,
        "mechanism_coverage_passed": recovered.report.mechanism_coverage_passed,
        "reserve_outcomes": [
            {
                "slot_id": reserve_ids[item.candidate_id],
                "delta": item.delta,
                "measured_outcome": item.measured_outcome,
            }
            for item in recovered.classifications.entries
            if item.candidate_id in reserve_ids
        ],
        "authorization_sha256": canonical_sha256(authorization.model_dump(mode="json")),
    }
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0 if recovered.report.mechanism_coverage_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
