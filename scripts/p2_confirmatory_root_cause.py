"""Reproduce the outcome-aware Bank root-cause audit from frozen artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_audit import audit_bank_replication
from aletheia_lab.benchmark.p2.confirmatory_closeout import load_and_verify_result_store
from aletheia_lab.benchmark.p2.confirmatory_execution import ConfirmatoryExecutionError
from aletheia_lab.benchmark.p2.confirmatory_protocol import load_confirmatory_protocol
from aletheia_lab.benchmark.p2.confirmatory_registered import (
    DatasetOutcome,
    load_registered_dataset,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("configs/benchmark/p2_label_noise_confirmatory_protocol.json"),
    )
    parser.add_argument(
        "--result-store",
        type=Path,
        default=Path("experiments/p2/outputs/label-noise-confirmatory-v2"),
    )
    parser.add_argument(
        "--bank-snapshot",
        type=Path,
        default=Path("data/processed/bank-additional-full.csv"),
    )
    parser.add_argument(
        "--bank-archive",
        type=Path,
        default=Path("data/raw/bank-marketing.zip"),
    )
    parser.add_argument("--expected-store-sha256", required=True)
    parser.add_argument("--skip-convergence-refits", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    store = root / args.result_store
    manifest = load_and_verify_result_store(store)
    if manifest.store_sha256 != args.expected_store_sha256:
        raise ConfirmatoryExecutionError("root-cause audit received another result store")
    protocol = load_confirmatory_protocol(root / args.protocol)
    dataset = next(item for item in protocol.datasets if item.role == "external_replication")
    registered = load_registered_dataset(
        protocol=protocol,
        dataset=dataset,
        snapshot_path=root / args.bank_snapshot,
        archive_path=root / args.bank_archive,
    )
    outcome = DatasetOutcome.model_validate_json((store / "replication-outcome.json").read_bytes())
    report = audit_bank_replication(
        protocol=protocol,
        registered=registered,
        outcome=outcome,
        result_store_sha256=manifest.store_sha256,
        verify_convergence=not args.skip_convergence_refits,
    )
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
