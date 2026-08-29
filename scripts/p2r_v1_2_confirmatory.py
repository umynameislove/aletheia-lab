#!/usr/bin/env python3
"""Preflight, execute once, and verify the paired P2R v1.2 amendment."""

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
    build_joint_closeout,
    build_technical_failure,
    load_and_verify_terminal_store,
    write_terminal_store,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    P2RArchiveReadinessReceipt,
    build_p2r_archive_readiness,
    load_archive_readiness,
    verify_p2r_archive_readiness,
    write_archive_readiness_exclusive,
)
from aletheia_lab.benchmark.p2.p2r_runtime import (
    DatasetSeedMeasurement,
    build_joint_candidate_plan,
    execute_p2r_dataset,
    measurement_census,
    paired_observations,
)
from aletheia_lab.benchmark.p2.p2r_v1_2_execution import (
    P2RV12ExecutionError,
    P2RV12Registration,
    build_sealed_marker,
    compile_execution_protocol,
    registration_from_release,
    verify_registration_pair,
    write_marker_exclusive,
)
from aletheia_lab.benchmark.p2.p2r_v1_2_protocol import (
    DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH,
    P2RV12MethodologicalAmendmentProtocol,
    load_p2r_v1_2_protocol,
    verify_p2r_v1_2_protocol_pair,
)

_REPOSITORY = "umynameislove/aletheia-lab"
_DEFAULT_DATA_DIR = Path("data/raw/p2-v3")
_DEFAULT_READINESS = Path("experiments/p2/outputs/p2r-v1-2-archive-readiness.json")
_DEFAULT_REGISTRATION = Path("experiments/p2/outputs/p2r-v1-2-registration.json")
_DEFAULT_MARKER = Path("experiments/p2/outputs/p2r-v1-2-sealed-open.json")
_DEFAULT_STORE = Path("experiments/p2/outputs/p2r-confirmatory-v1-2")


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise P2RV12ExecutionError("cannot verify registered P2R v1.2 Git state") from exc
    return completed.stdout.strip()


def _verify_clean_main(root: Path) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise P2RV12ExecutionError("P2R v1.2 execution requires a clean worktree")
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "HEAD")
    origin = _git(root, "rev-parse", "refs/remotes/origin/main")
    if branch != "main" or head != origin:
        raise P2RV12ExecutionError(
            "P2R v1.2 execution requires main synchronized with origin/main"
        )
    return head


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _chain(
    args: argparse.Namespace,
) -> tuple[
    Path,
    tuple[Path, Path],
    tuple[P2RV12MethodologicalAmendmentProtocol, P2RV12MethodologicalAmendmentProtocol],
    tuple[LightweightConfirmatoryProtocol, LightweightConfirmatoryProtocol],
]:
    root = Path(args.root).resolve()
    paths = (
        _resolve(root, args.drift_protocol),
        _resolve(root, args.preprocessing_protocol),
    )
    amendments = verify_p2r_v1_2_protocol_pair(
        load_p2r_v1_2_protocol(paths[0]),
        load_p2r_v1_2_protocol(paths[1]),
        root=root,
    )
    protocols = tuple(compile_execution_protocol(item, root=root) for item in amendments)
    return root, paths, amendments, (protocols[0], protocols[1])


def _tagged_amendment(
    *, root: Path, path: Path, amendment: P2RV12MethodologicalAmendmentProtocol
) -> str:
    tag = amendment.governance.required_git_tag
    if _git(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise P2RV12ExecutionError("registered P2R v1.2 tag must be annotated")
    commit = _git(root, "rev-list", "-n", "1", tag)
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
            check=True,
            capture_output=True,
        )
        relative = path.relative_to(root).as_posix()
        tagged = P2RV12MethodologicalAmendmentProtocol.model_validate_json(
            _git(root, "show", f"{tag}:{relative}")
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise P2RV12ExecutionError(
            "cannot reproduce P2R v1.2 amendment from its tag"
        ) from exc
    if tagged.canonical_sha256() != amendment.canonical_sha256():
        raise P2RV12ExecutionError(
            "tagged P2R v1.2 amendment differs from the execution amendment"
        )
    return commit


def _release_payload(tag: str) -> object:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{_REPOSITORY}/releases/tags/{tag}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aletheia-lab-p2r-v1-2-preflight",
        },
    )
    try:
        # Fixed GitHub endpoint plus strict release validation compensates B310.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise P2RV12ExecutionError(
            f"immutable P2R v1.2 GitHub release is unavailable: {tag}"
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
            raise P2RV12ExecutionError(
                "existing P2R v1.2 registration contains different evidence"
            ) from None


def _load_registrations(path: Path) -> tuple[P2RV12Registration, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError("v1.2 registration must contain a list")
        return tuple(
            P2RV12Registration.model_validate_json(json.dumps(item)) for item in raw
        )
    except (OSError, TypeError, ValueError) as exc:
        raise P2RV12ExecutionError(
            "P2R v1.2 registration is unavailable or invalid"
        ) from exc


def _readiness(args: argparse.Namespace, root: Path) -> P2RArchiveReadinessReceipt:
    manifest = load_v3_dataset_binding_manifest(_resolve(root, args.manifest))
    receipt = load_v3_dataset_binding_receipt(_resolve(root, args.receipt))
    return build_p2r_archive_readiness(
        manifest=manifest,
        pinned_receipt=receipt,
        archive_directory=_resolve(root, args.data_dir),
    )


def _preflight(args: argparse.Namespace) -> dict[str, object]:
    root, paths, amendments, protocols = _chain(args)
    head = _verify_clean_main(root)
    if _resolve(root, args.marker).exists() or _resolve(root, args.output).exists():
        raise P2RV12ExecutionError("P2R v1.2 attempt was already consumed")
    readiness = _readiness(args, root)
    write_archive_readiness_exclusive(_resolve(root, args.readiness), readiness)
    commits = tuple(
        _tagged_amendment(root=root, path=path, amendment=amendment)
        for path, amendment in zip(paths, amendments, strict=True)
    )
    registrations = tuple(
        registration_from_release(
            amendment=amendment,
            execution_protocol=protocol,
            archive_readiness=readiness,
            tagged_protocol_commit=commit,
            payload=_release_payload(amendment.governance.required_git_tag),
        )
        for amendment, protocol, commit in zip(
            amendments, protocols, commits, strict=True
        )
    )
    verify_registration_pair(
        amendments, protocols, registrations, readiness, root=root
    )
    _write_json_exclusive(_resolve(root, args.registration), registrations)
    return {
        "status": "registered_p2r_v1_2_preflight_pass",
        "execution_commit": head,
        "amendment_protocol_sha256s": {
            item.mechanism: item.canonical_sha256() for item in amendments
        },
        "execution_protocol_sha256s": {
            item.mechanism: item.canonical_sha256() for item in protocols
        },
        "registration_sha256s": {
            item.mechanism: item.canonical_sha256() for item in registrations
        },
        "archive_readiness_sha256": readiness.canonical_sha256(),
        "selected_targets": {
            item.mechanism: {
                dataset.dataset_id: dataset.selected_target_feature
                for dataset in item.datasets
            }
            for item in amendments
        },
        "registered_attempts_consumed": 0,
        "sealed_test_opened": False,
        "model_fitted": False,
        "outcomes_generated": False,
        "independent_new_dataset_replication": False,
    }


def _execute(args: argparse.Namespace) -> dict[str, object]:
    root, paths, amendments, protocols = _chain(args)
    head = _verify_clean_main(root)
    for path, amendment in zip(paths, amendments, strict=True):
        _tagged_amendment(root=root, path=path, amendment=amendment)
    registrations = _load_registrations(_resolve(root, args.registration))
    manifest = load_v3_dataset_binding_manifest(_resolve(root, args.manifest))
    receipt = load_v3_dataset_binding_receipt(_resolve(root, args.receipt))
    readiness = verify_p2r_archive_readiness(
        load_archive_readiness(_resolve(root, args.readiness)),
        manifest=manifest,
        pinned_receipt=receipt,
        archive_directory=_resolve(root, args.data_dir),
    )
    verify_registration_pair(
        amendments, protocols, registrations, readiness, root=root
    )
    expected_protocols = ",".join(item.canonical_sha256() for item in amendments)
    expected_registrations = ",".join(
        item.canonical_sha256() for item in registrations
    )
    if args.confirm_protocol_sha256s != expected_protocols:
        raise P2RV12ExecutionError("explicit P2R v1.2 amendment confirmation is incorrect")
    if args.confirm_registration_sha256s != expected_registrations:
        raise P2RV12ExecutionError(
            "explicit P2R v1.2 registration confirmation is incorrect"
        )
    marker_path = _resolve(root, args.marker)
    output = _resolve(root, args.output)
    if marker_path.exists() or marker_path.is_symlink() or output.exists() or output.is_symlink():
        raise P2RV12ExecutionError("P2R v1.2 attempt was already consumed")

    split_protocol = load_v3_3_confirmatory_protocol(_resolve(root, args.split_protocol))
    instrument = load_instrument_validity_protocol(
        _resolve(root, Path(protocols[0].artifacts.instrument_protocol_uri))
    )
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(),
        protocols=protocols,
    )
    environment = capture_execution_environment(head)
    marker = build_sealed_marker(
        execution_commit=head,
        amendments=amendments,
        protocols=protocols,
        registrations=registrations,
        archive_readiness=readiness,
        root=root,
    )
    write_marker_exclusive(marker_path, marker)

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
                item
                for item in split_protocol.dataset_splits
                if item.dataset_id == binding.dataset_id
            )
            prepared = prepare_runtime_dataset(
                protocol=split_protocol,
                dataset=binding,
                split_receipt=split_receipt,
                frame=frame,
            )
            features = frame.loc[:, list(binding.analysis_features)]
            training_frame = features.iloc[
                list(prepared.split.indices("train"))
            ].reset_index(drop=True)
            sealed_frame = features.iloc[
                list(prepared.split.indices("sealed_test"))
            ].reset_index(drop=True)
            stage = "execute_primary" if binding.role == "primary" else "execute_replication"
            for protocol in protocols:
                _, observed = execute_p2r_dataset(
                    protocol=protocol,
                    prepared=prepared,
                    training_frame=training_frame,
                    sealed_frame=sealed_frame,
                )
                measurements.extend(observed)
        stage = "build_closeout"
        protocol_map = {item.mechanism: item for item in protocols}
        checked = measurement_census(measurements, protocol_map)
        observations = paired_observations(plan=plan, measurements=checked)
        audit, closeout = build_joint_closeout(
            execution_commit=head,
            plan=plan,
            observations=observations,
            measurements=checked,
            environment=environment,
            protocols=protocol_map,
            registrations={item.mechanism: item for item in registrations},
            instrument_protocol=instrument,
        )
        store = write_terminal_store(
            output_dir=output,
            protocols=protocols,
            registrations=registrations,
            terminal=closeout,
            environment=environment,
            measurements=checked,
            observations=observations,
            audit=audit,
            sealed_marker=marker,
        )
        return {
            "status": "registered_p2r_v1_2_confirmatory_complete",
            "terminal_status": store.terminal_status,
            "terminal_store_sha256": store.store_sha256,
            "sealed_marker_sha256": marker.canonical_sha256(),
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
        failure = build_technical_failure(
            protocols=protocols,
            registrations=registrations,
            execution_commit=head,
            failure_stage=stage,
            error=exc,
        )
        store = write_terminal_store(
            output_dir=output,
            protocols=protocols,
            registrations=registrations,
            terminal=failure,
            environment=environment,
            sealed_marker=marker,
        )
        return {
            "status": "registered_p2r_v1_2_technical_failure",
            "terminal_status": store.terminal_status,
            "terminal_store_sha256": store.store_sha256,
            "sealed_marker_sha256": marker.canonical_sha256(),
            "failure_stage": failure.failure_stage,
            "exception_class": failure.exception_class,
            "exception_message_sha256": failure.exception_message_sha256,
            "partial_outcome_published": False,
            "scientific_disposition_generated": False,
            "rerun_forbidden": True,
        }


def _verify(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    store = load_and_verify_terminal_store(_resolve(root, args.output))
    return {
        "status": "p2r_v1_2_terminal_store_verified",
        "terminal_status": store.terminal_status,
        "terminal_store_sha256": store.store_sha256,
        "protocol_sha256s": store.protocol_sha256s,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--drift-protocol", type=Path, default=DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH)
    parser.add_argument(
        "--preprocessing-protocol",
        type=Path,
        default=DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH,
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_V3_DATASET_RECEIPT_PATH)
    parser.add_argument("--split-protocol", type=Path, default=DEFAULT_V3_3_PROTOCOL_PATH)
    parser.add_argument("--data-dir", type=Path, default=_DEFAULT_DATA_DIR)
    parser.add_argument("--readiness", type=Path, default=_DEFAULT_READINESS)
    parser.add_argument("--registration", type=Path, default=_DEFAULT_REGISTRATION)
    parser.add_argument("--marker", type=Path, default=_DEFAULT_MARKER)
    parser.add_argument("--output", type=Path, default=_DEFAULT_STORE)
    parser.add_argument("--confirm-protocol-sha256s")
    parser.add_argument("--confirm-registration-sha256s")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "execute" and (
        not args.confirm_protocol_sha256s or not args.confirm_registration_sha256s
    ):
        raise P2RV12ExecutionError(
            "P2R v1.2 execute requires both explicit hash confirmations"
        )
    result = (
        _preflight(args)
        if args.action == "preflight"
        else _execute(args)
        if args.action == "execute"
        else _verify(args)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
