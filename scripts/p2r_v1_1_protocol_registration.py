#!/usr/bin/env python3
"""Verify the paired outcome-blind P2R v1.1 recovery protocol candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DEFAULT_V3_DATASET_BINDINGS_PATH,
    DEFAULT_V3_DATASET_RECEIPT_PATH,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
)
from aletheia_lab.benchmark.p2.p2r_recovery import build_p2r_archive_readiness
from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH,
    P2RRecoveryProtocolError,
    load_p2r_recovery_protocol,
    verify_p2r_recovery_protocol_pair,
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
        raise P2RRecoveryProtocolError("cannot verify P2R v1.1 registration Git state") from exc
    return completed.stdout.strip()


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise P2RRecoveryProtocolError("recovery protocol is not a regular file")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise P2RRecoveryProtocolError("cannot hash recovery protocol") from exc
    return digest.hexdigest()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "compile-readiness"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--drift-protocol",
        type=Path,
        default=DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    )
    parser.add_argument(
        "--preprocessing-protocol",
        type=Path,
        default=DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH,
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--dataset-receipt", type=Path, default=DEFAULT_V3_DATASET_RECEIPT_PATH)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/p2-v3"))
    args = parser.parse_args()

    root = args.root.resolve()
    drift_path = _resolve(root, args.drift_protocol)
    prep_path = _resolve(root, args.preprocessing_protocol)
    drift, prep = verify_p2r_recovery_protocol_pair(
        load_p2r_recovery_protocol(drift_path),
        load_p2r_recovery_protocol(prep_path),
        root=root,
    )
    recovery_commit = drift.technical_recovery.recovery_implementation_commit
    if _git(root, "cat-file", "-t", recovery_commit) != "commit":
        raise P2RRecoveryProtocolError("recovery implementation binding is not a Git commit")
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", recovery_commit, "HEAD"),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise P2RRecoveryProtocolError(
            "recovery implementation is not an ancestor of HEAD"
        ) from exc

    readiness_recompiled = False
    readiness_sha256 = drift.readiness.expected_receipt_sha256
    if args.command == "compile-readiness":
        readiness = build_p2r_archive_readiness(
            manifest=load_v3_dataset_binding_manifest(_resolve(root, args.manifest)),
            pinned_receipt=load_v3_dataset_binding_receipt(_resolve(root, args.dataset_receipt)),
            archive_directory=_resolve(root, args.data_dir),
        )
        if readiness.canonical_sha256() != readiness_sha256:
            raise P2RRecoveryProtocolError(
                "recompiled archive readiness differs from the frozen protocol"
            )
        readiness_recompiled = True

    protocols = (drift, prep)
    paths = (drift_path, prep_path)
    report = {
        "status": "p2r_v1_1_recovery_protocols_verified_not_registered",
        "protocol_sha256s": {item.mechanism: item.canonical_sha256() for item in protocols},
        "protocol_file_sha256s": {
            item.mechanism: _file_sha256(path) for item, path in zip(protocols, paths, strict=True)
        },
        "predecessor_protocol_sha256s": {
            item.mechanism: item.artifacts.predecessor_protocol_sha256 for item in protocols
        },
        "predecessor_terminal_store_sha256": (drift.artifacts.predecessor_terminal_store_sha256),
        "failure_audit_sha256": drift.artifacts.failure_audit_sha256,
        "archive_readiness_sha256": readiness_sha256,
        "archive_readiness_recompiled": readiness_recompiled,
        "scientific_sections_unchanged": True,
        "required_git_tags": {
            item.mechanism: item.governance.required_git_tag for item in protocols
        },
        "required_git_tags_exist": {
            item.mechanism: bool(
                _git(root, "tag", "--list", item.governance.required_git_tag, check=False)
            )
            for item in protocols
        },
        "maximum_registered_execution_attempts": 1,
        "predecessor_rerun_forbidden": True,
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
