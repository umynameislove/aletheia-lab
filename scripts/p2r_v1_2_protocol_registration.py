#!/usr/bin/env python3
"""Verify the paired outcome-blind P2R v1.2 amendment candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from aletheia_lab.benchmark.p2.p2r_v1_2_protocol import (
    DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH,
    P2R_V1_1_FEASIBILITY_SHA256,
    P2R_V1_2_AMENDMENT_IMPLEMENTATION_COMMIT,
    P2RV12ProtocolError,
    load_p2r_v1_2_protocol,
    verify_p2r_v1_2_protocol_pair,
)


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=check,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise P2RV12ProtocolError("cannot verify P2R v1.2 registration Git state") from exc
    return completed.stdout.strip()


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise P2RV12ProtocolError("P2R v1.2 candidate is not a regular file")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise P2RV12ProtocolError("cannot hash P2R v1.2 candidate") from exc
    return digest.hexdigest()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _recompile_feasibility(root: Path, data_dir: Path) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    command = (
        sys.executable,
        str(root / "scripts/p2r_v1_1_failure_audit.py"),
        "verify-feasibility",
        "--root",
        str(root),
        "--data-dir",
        str(data_dir),
    )
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        payload: object = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise P2RV12ProtocolError(
            "cannot reproduce the outcome-blind P2R feasibility receipt"
        ) from exc
    if not isinstance(payload, dict):
        raise P2RV12ProtocolError("P2R feasibility verifier returned an invalid payload")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "compile-feasibility"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--drift-protocol",
        type=Path,
        default=DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH,
    )
    parser.add_argument(
        "--preprocessing-protocol",
        type=Path,
        default=DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH,
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/p2-v3"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    drift_path = _resolve(root, args.drift_protocol)
    prep_path = _resolve(root, args.preprocessing_protocol)
    drift, prep = verify_p2r_v1_2_protocol_pair(
        load_p2r_v1_2_protocol(drift_path),
        load_p2r_v1_2_protocol(prep_path),
        root=root,
    )
    commit = drift.artifacts.amendment_implementation_commit
    if commit != P2R_V1_2_AMENDMENT_IMPLEMENTATION_COMMIT:
        raise P2RV12ProtocolError("v1.2 binds another amendment implementation")
    if _git(root, "cat-file", "-t", commit) != "commit":
        raise P2RV12ProtocolError("v1.2 amendment binding is not a Git commit")
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise P2RV12ProtocolError(
            "v1.2 amendment implementation is not an ancestor of HEAD"
        ) from exc

    feasibility_recompiled = False
    if args.command == "compile-feasibility":
        payload = _recompile_feasibility(root, _resolve(root, args.data_dir))
        if payload.get("receipt_sha256") != P2R_V1_1_FEASIBILITY_SHA256:
            raise P2RV12ProtocolError("recompiled feasibility differs from the v1.2 amendment")
        if any(
            payload.get(field) is not expected
            for field, expected in (
                ("target_values_used_for_capacity_or_selection", False),
                ("model_fitted", False),
                ("predictive_metrics_generated", False),
            )
        ):
            raise P2RV12ProtocolError("feasibility reproduction crossed the outcome boundary")
        feasibility_recompiled = True

    protocols = (drift, prep)
    paths = (drift_path, prep_path)
    report = {
        "status": "p2r_v1_2_methodological_amendments_verified_not_registered",
        "protocol_sha256s": {item.mechanism: item.canonical_sha256() for item in protocols},
        "protocol_file_sha256s": {
            item.mechanism: _file_sha256(path) for item, path in zip(protocols, paths, strict=True)
        },
        "required_git_tags": {
            item.mechanism: item.governance.required_git_tag for item in protocols
        },
        "required_git_tags_exist": {
            item.mechanism: bool(
                _git(root, "tag", "--list", item.governance.required_git_tag, check=False)
            )
            for item in protocols
        },
        "amendment_implementation_commit": commit,
        "predecessor_terminal_store_sha256": (drift.artifacts.predecessor_terminal_store_sha256),
        "failure_audit_sha256": drift.artifacts.failure_audit_sha256,
        "feasibility_receipt_sha256": drift.artifacts.feasibility_receipt_sha256,
        "feasibility_recompiled_from_pinned_archives": feasibility_recompiled,
        "dataset_target_features": {
            item.dataset_id: item.selected_target_feature for item in drift.datasets
        },
        "dataset_target_row_counts": {
            item.dataset_id: item.target_row_count for item in drift.datasets
        },
        "dataset_minimum_capacity_counts": {
            item.dataset_id: item.minimum_capacity_count for item in drift.datasets
        },
        "declared_manipulation_magnitude": (
            drift.scientific_invariants.declared_manipulation_magnitude
        ),
        "minimum_capacity_reserve": (drift.scientific_invariants.minimum_capacity_reserve),
        "scientific_semantics_changed_and_disclosed": True,
        "all_other_scientific_sections_inherited_by_hash": True,
        "reuses_previously_opened_named_partitions": True,
        "independent_new_dataset_replication": False,
        "maximum_registered_execution_attempts": 1,
        "predecessor_v1_1_rerun_forbidden": True,
        "model_fitted": False,
        "predictive_metrics_generated": False,
        "sealed_outcomes_generated": False,
        "registration_authorized": False,
        "execution_authorized": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
