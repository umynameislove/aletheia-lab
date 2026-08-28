#!/usr/bin/env python3
"""Preflight, execute once, and verify the paired P2R v1.1 recovery."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from aletheia_lab.benchmark.p2.confirmatory_closeout import capture_execution_environment
from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    DEFAULT_V3_3_PROTOCOL_PATH,
    load_v3_3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DEFAULT_V3_DATASET_BINDINGS_PATH,
    DEFAULT_V3_DATASET_RECEIPT_PATH,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import prepare_runtime_dataset
from aletheia_lab.benchmark.p2.instrument_validity import load_instrument_validity_protocol
from aletheia_lab.benchmark.p2.lightweight_protocol import LightweightConfirmatoryProtocol
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RProtocolRegistration,
    build_joint_closeout,
    build_technical_failure,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    DEFAULT_P2R_FAILURE_AUDIT_PATH,
    DEFAULT_P2R_V1_MARKER_PATH,
    DEFAULT_P2R_V1_REGISTRATION_PATH,
    DEFAULT_P2R_V1_STORE_PATH,
    build_p2r_archive_readiness,
    load_archive_readiness,
    load_p2r_v1_failure_audit,
    verify_p2r_archive_readiness,
    verify_p2r_v1_failure_audit,
    write_archive_readiness_exclusive,
)
from aletheia_lab.benchmark.p2.p2r_recovery_execution import (
    P2RRecoveryExecutionError,
    P2RRecoveryRegistration,
    build_recovery_sealed_marker,
    load_and_verify_recovery_terminal_store,
    recovery_registration_from_release,
    verify_recovery_registration_pair,
    write_recovery_marker_exclusive,
    write_recovery_terminal_store,
)
from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH,
    P2RRecoveryProtocol,
    load_p2r_recovery_protocol,
    verify_p2r_recovery_protocol,
    verify_p2r_recovery_protocol_pair,
)
from aletheia_lab.benchmark.p2.p2r_runtime import (
    DatasetSeedMeasurement,
    build_joint_candidate_plan,
    execute_p2r_dataset,
    measurement_census,
    paired_observations,
)

_REPOSITORY = "umynameislove/aletheia-lab"
_DEFAULT_DATA_DIR = Path("data/raw/p2-v3")
_DEFAULT_READINESS = Path("experiments/p2/outputs/p2r-v1-1-archive-readiness.json")
_DEFAULT_REGISTRATION = Path("experiments/p2/outputs/p2r-v1-1-registration.json")
_DEFAULT_MARKER = Path("experiments/p2/outputs/p2r-v1-1-sealed-open.json")
_DEFAULT_STORE = Path("experiments/p2/outputs/p2r-confirmatory-v1-1")


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise P2RRecoveryExecutionError("cannot verify registered P2R v1.1 Git state") from exc
    return completed.stdout.strip()


def _verify_clean_main(root: Path) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise P2RRecoveryExecutionError("P2R v1.1 execution requires a clean worktree")
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "HEAD")
    origin = _git(root, "rev-parse", "refs/remotes/origin/main")
    if branch != "main" or head != origin:
        raise P2RRecoveryExecutionError(
            "P2R v1.1 execution requires main synchronized with origin/main"
        )
    return head


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _recovery_chain(
    args: argparse.Namespace,
) -> tuple[
    Path,
    tuple[Path, Path],
    tuple[P2RRecoveryProtocol, P2RRecoveryProtocol],
    tuple[LightweightConfirmatoryProtocol, LightweightConfirmatoryProtocol],
]:
    root = Path(args.root).resolve()
    paths = (
        _resolve(root, args.drift_protocol),
        _resolve(root, args.preprocessing_protocol),
    )
    recoveries = verify_p2r_recovery_protocol_pair(
        load_p2r_recovery_protocol(paths[0]),
        load_p2r_recovery_protocol(paths[1]),
        root=root,
    )
    predecessors = tuple(
        verify_p2r_recovery_protocol(item, root=root)[1] for item in recoveries
    )
    return root, paths, recoveries, predecessors  # type: ignore[return-value]


def _tagged_recovery(
    *, root: Path, path: Path, recovery: P2RRecoveryProtocol
) -> str:
    tag = recovery.governance.required_git_tag
    if _git(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise P2RRecoveryExecutionError("registered P2R v1.1 tag must be annotated")
    commit = _git(root, "rev-list", "-n", "1", tag)
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
            check=True,
            capture_output=True,
        )
        relative = path.relative_to(root).as_posix()
        tagged = P2RRecoveryProtocol.model_validate_json(_git(root, "show", f"{tag}:{relative}"))
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise P2RRecoveryExecutionError(
            "cannot reproduce P2R v1.1 protocol from its tag"
        ) from exc
    if tagged.canonical_sha256() != recovery.canonical_sha256():
        raise P2RRecoveryExecutionError(
            "tagged P2R v1.1 protocol differs from the execution protocol"
        )
    return commit


def _release_payload(tag: str) -> object:
    url = f"https://api.github.com/repos/{_REPOSITORY}/releases/tags/{tag}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aletheia-lab-p2r-v1-1-preflight",
        },
    )
    try:
        # Fixed GitHub endpoint plus strict release validation compensates B310.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise P2RRecoveryExecutionError(
            f"immutable P2R v1.1 GitHub release is unavailable: {tag}"
        ) from exc


def _write_json_exclusive(path: Path, models: Sequence[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        [item.model_dump(mode="json") for item in models],  # type: ignore[attr-defined]
        indent=2,
        sort_keys=True,
        default=str,
    ) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_text(encoding="utf-8") != content:
            raise P2RRecoveryExecutionError(
                "existing P2R v1.1 registration contains different evidence"
            ) from None


def _load_recovery_registrations(path: Path) -> tuple[P2RRecoveryRegistration, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError("recovery registration must contain a list")
        return tuple(P2RRecoveryRegistration.model_validate(item) for item in raw)
    except (OSError, TypeError, ValueError) as exc:
        raise P2RRecoveryExecutionError(
            "P2R v1.1 registration is unavailable or invalid"
        ) from exc


def _load_scientific_registrations(path: Path) -> tuple[P2RProtocolRegistration, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError("scientific registration must contain a list")
        registrations = tuple(P2RProtocolRegistration.model_validate(item) for item in raw)
    except (OSError, TypeError, ValueError) as exc:
        raise P2RRecoveryExecutionError(
            "P2R v1 scientific registration is unavailable or invalid"
        ) from exc
    if tuple(item.mechanism for item in registrations) != (
        "data_drift",
        "preprocessing_bug",
    ):
        raise P2RRecoveryExecutionError("P2R v1 scientific registration is incomplete")
    return registrations


def _failure_chain(args: argparse.Namespace, root: Path):  # type: ignore[no-untyped-def]
    audit = load_p2r_v1_failure_audit(_resolve(root, args.failure_audit))
    return verify_p2r_v1_failure_audit(
        audit,
        root=root,
        registration_path=_resolve(root, args.v1_registration),
        marker_path=_resolve(root, args.v1_marker),
        terminal_store_path=_resolve(root, args.v1_store),
    )


def _preflight(args: argparse.Namespace) -> dict[str, object]:
    root, paths, recoveries, _ = _recovery_chain(args)
    head = _verify_clean_main(root)
    tagged = tuple(
        _tagged_recovery(root=root, path=path, recovery=recovery)
        for path, recovery in zip(paths, recoveries, strict=True)
    )
    recovery_registrations = tuple(
        recovery_registration_from_release(
            recovery=recovery,
            tagged_protocol_commit=commit,
            payload=_release_payload(recovery.governance.required_git_tag),
        )
        for recovery, commit in zip(recoveries, tagged, strict=True)
    )
    verify_recovery_registration_pair(recoveries, recovery_registrations)
    failure = _failure_chain(args, root)
    marker = _resolve(root, args.marker)
    output = _resolve(root, args.output)
    if marker.exists() or marker.is_symlink() or output.exists() or output.is_symlink():
        raise P2RRecoveryExecutionError("P2R v1.1 marker or terminal store already exists")
    manifest = load_v3_dataset_binding_manifest(_resolve(root, args.manifest))
    receipt = load_v3_dataset_binding_receipt(_resolve(root, args.receipt))
    readiness = build_p2r_archive_readiness(
        manifest=manifest,
        pinned_receipt=receipt,
        archive_directory=_resolve(root, args.data_dir),
    )
    if readiness.canonical_sha256() != recoveries[0].readiness.expected_receipt_sha256:
        raise P2RRecoveryExecutionError("archive readiness differs from the v1.1 protocol")
    write_archive_readiness_exclusive(_resolve(root, args.readiness), readiness)
    _write_json_exclusive(_resolve(root, args.registration), recovery_registrations)
    return {
        "status": "registered_p2r_v1_1_preflight_pass",
        "execution_commit": head,
        "recovery_protocol_sha256s": {
            item.mechanism: item.canonical_sha256() for item in recoveries
        },
        "recovery_registration_sha256s": {
            item.mechanism: item.canonical_sha256() for item in recovery_registrations
        },
        "scientific_protocol_sha256s": {
            item.mechanism: item.artifacts.predecessor_protocol_sha256 for item in recoveries
        },
        "predecessor_terminal_store_sha256": failure.terminal_store_sha256,
        "archive_readiness_sha256": readiness.canonical_sha256(),
        "registered_attempts_consumed": 0,
        "sealed_test_opened": False,
        "model_fitted": False,
        "outcomes_generated": False,
        "scientific_sections_unchanged": True,
        "independent_new_dataset_replication": False,
    }


def _execute(args: argparse.Namespace) -> dict[str, object]:
    root, paths, recoveries, predecessors = _recovery_chain(args)
    head = _verify_clean_main(root)
    for path, recovery in zip(paths, recoveries, strict=True):
        _tagged_recovery(root=root, path=path, recovery=recovery)
    recovery_registrations = _load_recovery_registrations(
        _resolve(root, args.registration)
    )
    verify_recovery_registration_pair(recoveries, recovery_registrations)
    scientific_registrations = _load_scientific_registrations(
        _resolve(root, args.v1_registration)
    )
    if tuple(item.protocol_sha256 for item in scientific_registrations) != tuple(
        item.canonical_sha256() for item in predecessors
    ):
        raise P2RRecoveryExecutionError(
            "scientific registrations differ from the frozen predecessor protocols"
        )
    expected_protocols = ",".join(item.canonical_sha256() for item in recoveries)
    expected_registrations = ",".join(
        item.canonical_sha256() for item in recovery_registrations
    )
    if args.confirm_protocol_sha256s != expected_protocols:
        raise P2RRecoveryExecutionError("explicit P2R v1.1 protocol confirmation is incorrect")
    if args.confirm_registration_sha256s != expected_registrations:
        raise P2RRecoveryExecutionError(
            "explicit P2R v1.1 registration confirmation is incorrect"
        )
    marker_path = _resolve(root, args.marker)
    output = _resolve(root, args.output)
    if marker_path.exists() or marker_path.is_symlink() or output.exists() or output.is_symlink():
        raise P2RRecoveryExecutionError("P2R v1.1 attempt was already consumed")

    failure_audit = _failure_chain(args, root)
    manifest = load_v3_dataset_binding_manifest(_resolve(root, args.manifest))
    receipt = load_v3_dataset_binding_receipt(_resolve(root, args.receipt))
    readiness = verify_p2r_archive_readiness(
        load_archive_readiness(_resolve(root, args.readiness)),
        manifest=manifest,
        pinned_receipt=receipt,
        archive_directory=_resolve(root, args.data_dir),
    )
    if readiness.canonical_sha256() != recoveries[0].readiness.expected_receipt_sha256:
        raise P2RRecoveryExecutionError("current archives differ from registered readiness")
    reused_protocol = load_v3_3_confirmatory_protocol(_resolve(root, args.split_protocol))
    instrument = load_instrument_validity_protocol(
        _resolve(root, Path(predecessors[0].artifacts.instrument_protocol_uri))
    )
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(),
        protocols=predecessors,
    )
    environment = capture_execution_environment(head)
    marker = build_recovery_sealed_marker(
        execution_commit=head,
        recoveries=recoveries,
        recovery_registrations=recovery_registrations,
        scientific_registrations=scientific_registrations,
        readiness=readiness,
        failure_audit=failure_audit,
        repository_root=root,
    )
    write_recovery_marker_exclusive(marker_path, marker)

    measurements: list[DatasetSeedMeasurement] = []
    stage: Literal[
        "load_primary",
        "execute_primary",
        "load_replication",
        "execute_replication",
        "build_closeout",
    ] = "load_primary"
    try:
        for binding in manifest.datasets:
            stage = "load_primary" if binding.role == "primary" else "load_replication"
            _, frame = load_v3_dataset_snapshot_for_registration(
                dataset=binding,
                archive_path=_resolve(root, args.data_dir) / binding.archive.file_name,
            )
            split_receipt = next(
                item for item in reused_protocol.dataset_splits if item.dataset_id == binding.dataset_id
            )
            prepared = prepare_runtime_dataset(
                protocol=reused_protocol,
                dataset=binding,
                split_receipt=split_receipt,
                frame=frame,
            )
            train_indices = prepared.split.indices("train")
            sealed_indices = prepared.split.indices("sealed_test")
            features = frame.loc[:, list(binding.analysis_features)]
            training_frame = features.iloc[list(train_indices)].reset_index(drop=True)
            sealed_frame = features.iloc[list(sealed_indices)].reset_index(drop=True)
            stage = "execute_primary" if binding.role == "primary" else "execute_replication"
            for protocol in predecessors:
                _, observed = execute_p2r_dataset(
                    protocol=protocol,
                    prepared=prepared,
                    training_frame=training_frame,
                    sealed_frame=sealed_frame,
                )
                measurements.extend(observed)
        stage = "build_closeout"
        checked_measurements = measurement_census(
            measurements,
            {item.mechanism: item for item in predecessors},
        )
        observations = paired_observations(plan=plan, measurements=checked_measurements)
        audit, closeout = build_joint_closeout(
            execution_commit=head,
            plan=plan,
            observations=observations,
            measurements=checked_measurements,
            environment=environment,
            protocols={item.mechanism: item for item in predecessors},
            registrations={item.mechanism: item for item in scientific_registrations},
            instrument_protocol=instrument,
        )
        store = write_recovery_terminal_store(
            output_dir=output,
            recoveries=recoveries,
            recovery_registrations=recovery_registrations,
            predecessors=predecessors,
            scientific_registrations=scientific_registrations,
            terminal=closeout,
            environment=environment,
            readiness=readiness,
            failure_audit=failure_audit,
            sealed_marker=marker,
            repository_root=root,
            measurements=checked_measurements,
            observations=observations,
            audit=audit,
        )
        return {
            "status": "registered_p2r_v1_1_confirmatory_complete",
            "terminal_status": store.terminal_status,
            "terminal_store_sha256": store.store_sha256,
            "mechanism_dispositions": {
                item.mechanism: item.disposition for item in closeout.mechanism_closeouts
            },
            "n_admitted": closeout.n_admitted,
            "n_mechanisms": closeout.n_mechanisms,
            "outcomes_released_together": True,
            "rerun_forbidden": True,
            "independent_new_dataset_replication": False,
        }
    except Exception as exc:
        technical_failure = build_technical_failure(
            protocols=predecessors,
            registrations=scientific_registrations,
            execution_commit=head,
            failure_stage=stage,
            error=exc,
        )
        store = write_recovery_terminal_store(
            output_dir=output,
            recoveries=recoveries,
            recovery_registrations=recovery_registrations,
            predecessors=predecessors,
            scientific_registrations=scientific_registrations,
            terminal=technical_failure,
            environment=environment,
            readiness=readiness,
            failure_audit=failure_audit,
            sealed_marker=marker,
            repository_root=root,
        )
        return {
            "status": "registered_p2r_v1_1_technical_failure",
            "terminal_status": store.terminal_status,
            "terminal_store_sha256": store.store_sha256,
            "failure_stage": technical_failure.failure_stage,
            "exception_class": technical_failure.exception_class,
            "exception_message_sha256": technical_failure.exception_message_sha256,
            "partial_outcome_published": False,
            "scientific_disposition_generated": False,
            "rerun_forbidden": True,
        }


def _verify(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    store = load_and_verify_recovery_terminal_store(_resolve(root, args.output))
    return {
        "status": "p2r_v1_1_terminal_store_verified",
        "terminal_status": store.terminal_status,
        "scientific_store_sha256": store.scientific_store_sha256,
        "terminal_store_sha256": store.store_sha256,
        "artifact_count": len(store.entries),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--drift-protocol", type=Path, default=DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH
    )
    parser.add_argument(
        "--preprocessing-protocol",
        type=Path,
        default=DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH,
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_V3_DATASET_RECEIPT_PATH)
    parser.add_argument("--split-protocol", type=Path, default=DEFAULT_V3_3_PROTOCOL_PATH)
    parser.add_argument("--data-dir", type=Path, default=_DEFAULT_DATA_DIR)
    parser.add_argument("--failure-audit", type=Path, default=DEFAULT_P2R_FAILURE_AUDIT_PATH)
    parser.add_argument("--v1-registration", type=Path, default=DEFAULT_P2R_V1_REGISTRATION_PATH)
    parser.add_argument("--v1-marker", type=Path, default=DEFAULT_P2R_V1_MARKER_PATH)
    parser.add_argument("--v1-store", type=Path, default=DEFAULT_P2R_V1_STORE_PATH)
    parser.add_argument("--readiness", type=Path, default=_DEFAULT_READINESS)
    parser.add_argument("--registration", type=Path, default=_DEFAULT_REGISTRATION)
    parser.add_argument("--marker", type=Path, default=_DEFAULT_MARKER)
    parser.add_argument("--output", type=Path, default=_DEFAULT_STORE)
    parser.add_argument("--confirm-protocol-sha256s")
    parser.add_argument("--confirm-registration-sha256s")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "preflight":
        result = _preflight(args)
    elif args.command == "execute":
        if not args.confirm_protocol_sha256s or not args.confirm_registration_sha256s:
            raise P2RRecoveryExecutionError(
                "P2R v1.1 execution requires both explicit hash confirmations"
            )
        result = _execute(args)
    else:
        result = _verify(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
