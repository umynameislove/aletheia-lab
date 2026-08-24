#!/usr/bin/env python3
"""Verify the outcome-blind v3.2 technical-recovery protocol candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    DEFAULT_V3_2_PROTOCOL_PATH,
    load_v3_2_confirmatory_protocol,
    verify_v3_2_compiled_split_receipts,
    verify_v3_2_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    compile_v3_split_receipts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=check,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V3RuntimeError("cannot verify v3.2 registration Git state") from exc
    return completed.stdout.strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise V3RuntimeError("cannot hash v3.2 protocol candidate") from exc
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "compile-splits"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_V3_2_PROTOCOL_PATH)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/p2-v3"))
    args = parser.parse_args()

    root = args.root.resolve()
    protocol_path = args.protocol if args.protocol.is_absolute() else root / args.protocol
    data_dir = args.data_dir if args.data_dir.is_absolute() else root / args.data_dir
    protocol = load_v3_2_confirmatory_protocol(protocol_path)
    _, manifest, receipt, predecessor, failure_receipt = verify_v3_2_protocol_artifacts(
        protocol, root=root
    )
    recovery_commit = protocol.technical_recovery.recovery_implementation_commit
    if _git(root, "cat-file", "-t", recovery_commit) != "commit":
        raise V3RuntimeError("v3.2 recovery implementation binding is not a Git commit")
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", recovery_commit, "HEAD"),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V3RuntimeError("v3.2 recovery implementation is not an ancestor of HEAD") from exc

    splits_recompiled = False
    if args.command == "compile-splits":
        observed = compile_v3_split_receipts(
            manifest,
            receipt,
            archive_directory=data_dir,
        )
        verify_v3_2_compiled_split_receipts(protocol, observed)
        splits_recompiled = True

    tag = protocol.governance.required_git_tag
    tag_exists = bool(_git(root, "tag", "--list", tag, check=False))
    report = {
        "status": "v3_2_technical_recovery_protocol_verified_not_registered",
        "protocol_sha256": protocol.canonical_sha256(),
        "protocol_file_sha256": _file_sha256(protocol_path),
        "predecessor_protocol_sha256": predecessor.canonical_sha256(),
        "failure_receipt_sha256": failure_receipt.canonical_sha256(),
        "recovery_implementation_commit": recovery_commit,
        "scientific_sections_unchanged": True,
        "dataset_ids": [item.dataset_id for item in protocol.dataset_splits],
        "split_membership_sha256": {
            item.dataset_id: item.membership_sha256 for item in protocol.dataset_splits
        },
        "splits_recompiled_from_pinned_archives": splits_recompiled,
        "same_pinned_datasets_and_splits_reused": (
            protocol.technical_recovery.same_pinned_datasets_and_splits_reused
        ),
        "maximum_registered_execution_attempts": (
            protocol.technical_recovery.maximum_registered_execution_attempts
        ),
        "required_git_tag": tag,
        "required_git_tag_exists": tag_exists,
        "model_fitted": protocol.model_fitted,
        "predictive_metrics_generated": protocol.predictive_metrics_generated,
        "sealed_outcomes_generated": protocol.sealed_outcomes_generated,
        "registration_authorized": False,
        "execution_authorized": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
