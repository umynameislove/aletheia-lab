#!/usr/bin/env python3
"""Preflight, execute once, and verify the paired P2R mechanism studies."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from aletheia_lab.benchmark.p2.confirmatory_closeout import (
    capture_execution_environment,
)
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
from aletheia_lab.benchmark.p2.instrument_validity import (
    load_instrument_validity_protocol,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    LightweightConfirmatoryProtocol,
    load_lightweight_confirmatory_protocol,
    verify_protocol_pair,
)
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RCloseoutError,
    P2RProtocolRegistration,
    build_joint_closeout,
    build_technical_failure,
    load_and_verify_terminal_store,
    registration_from_release,
    write_terminal_store,
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
_DEFAULT_REGISTRATION = Path("experiments/p2/outputs/p2r-registration.json")
_DEFAULT_MARKER = Path("experiments/p2/outputs/p2r-sealed-open.json")
_DEFAULT_STORE = Path("experiments/p2/outputs/p2r-confirmatory-v1")


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise P2RCloseoutError("cannot verify registered P2R Git state") from exc
    return completed.stdout.strip()


def _verify_clean_main(root: Path) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise P2RCloseoutError("registered P2R execution requires a clean worktree")
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "HEAD")
    origin_main = _git(root, "rev-parse", "refs/remotes/origin/main")
    if branch != "main" or head != origin_main:
        raise P2RCloseoutError(
            "registered P2R execution requires main synchronized with origin/main"
        )
    return head


def _tagged_protocol(
    *, root: Path, path: Path, protocol: LightweightConfirmatoryProtocol
) -> str:
    tag = protocol.required_git_tag
    if _git(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise P2RCloseoutError("registered P2R protocol tag must be annotated")
    commit = _git(root, "rev-list", "-n", "1", tag)
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
            check=True,
            capture_output=True,
        )
        relative = path.relative_to(root).as_posix()
        tagged = LightweightConfirmatoryProtocol.model_validate_json(
            _git(root, "show", f"{tag}:{relative}")
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise P2RCloseoutError("cannot reproduce protocol from its registered tag") from exc
    if tagged.canonical_sha256() != protocol.canonical_sha256():
        raise P2RCloseoutError("tagged P2R protocol differs from the execution protocol")
    return commit


def _release_payload(tag: str) -> object:
    url = f"https://api.github.com/repos/{_REPOSITORY}/releases/tags/{tag}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aletheia-lab-p2r-confirmatory-preflight",
        },
    )
    try:
        # Fixed GitHub HTTPS endpoint plus strict schema validation compensates B310.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise P2RCloseoutError(
            f"immutable P2R GitHub release is unavailable: {tag}"
        ) from exc


def _write_registration_exclusive(
    path: Path, registrations: Sequence[P2RProtocolRegistration]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            [item.model_dump(mode="json") for item in registrations],
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if _load_registrations(path) != tuple(registrations):
            raise P2RCloseoutError("existing P2R registration contains different evidence") from None


def _load_registrations(path: Path) -> tuple[P2RProtocolRegistration, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError("registration receipt must contain a list")
        registrations = tuple(
            P2RProtocolRegistration.model_validate_json(json.dumps(item)) for item in raw
        )
    except (OSError, TypeError, ValueError) as exc:
        raise P2RCloseoutError("P2R registration receipt is unavailable or invalid") from exc
    if tuple(item.mechanism for item in registrations) != (
        "data_drift",
        "preprocessing_bug",
    ):
        raise P2RCloseoutError("P2R registration receipt has an incomplete mechanism census")
    return registrations


def _open_marker(
    *, path: Path, execution_commit: str, registrations: Sequence[P2RProtocolRegistration]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            {
                "schema_version": "p2r-sealed-open/1",
                "execution_commit": execution_commit,
                "registration_sha256s": [item.canonical_sha256() for item in registrations],
                "opened_at": datetime.now(UTC).isoformat(),
                "maximum_attempts_per_mechanism": 1,
                "rerun_forbidden": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise P2RCloseoutError("P2R sealed marker exists; rerun is forbidden") from exc


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _protocols(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    root = Path(args.root).resolve()
    drift_path = _resolve(root, args.drift_protocol)
    preprocessing_path = _resolve(root, args.preprocessing_protocol)
    drift, preprocessing = verify_protocol_pair(
        load_lightweight_confirmatory_protocol(drift_path),
        load_lightweight_confirmatory_protocol(preprocessing_path),
    )
    return root, drift_path, preprocessing_path, drift, preprocessing


def _preflight(args: argparse.Namespace) -> dict[str, object]:
    root, drift_path, preprocessing_path, drift, preprocessing = _protocols(args)
    head = _verify_clean_main(root)
    protocols = (drift, preprocessing)
    paths = (drift_path, preprocessing_path)
    tagged = tuple(
        _tagged_protocol(root=root, path=path, protocol=protocol)
        for path, protocol in zip(paths, protocols, strict=True)
    )
    # Fetch and validate both releases before any local attempt artifact is written.
    registrations = tuple(
        registration_from_release(
            protocol=protocol,
            tagged_protocol_commit=commit,
            payload=_release_payload(protocol.required_git_tag),
        )
        for protocol, commit in zip(protocols, tagged, strict=True)
    )
    registration_path = _resolve(root, args.registration)
    marker = _resolve(root, args.marker)
    output = _resolve(root, args.output)
    if marker.exists() or marker.is_symlink() or output.exists() or output.is_symlink():
        raise P2RCloseoutError("P2R execution marker or terminal store already exists")
    _write_registration_exclusive(registration_path, registrations)
    return {
        "status": "registered_p2r_preflight_pass",
        "execution_commit": head,
        "protocol_sha256s": {
            item.mechanism: item.canonical_sha256() for item in protocols
        },
        "registration_sha256s": {
            item.mechanism: item.canonical_sha256() for item in registrations
        },
        "tagged_protocol_commits": {
            item.mechanism: item.tagged_protocol_commit for item in registrations
        },
        "dataset_ids": [item.dataset_id for item in drift.datasets],
        "seeds": list(drift.execution.seeds),
        "registered_attempts_consumed": 0,
        "sealed_test_opened": False,
        "model_fitted": False,
        "outcomes_generated": False,
        "independent_new_dataset_replication": False,
    }


def _execute(args: argparse.Namespace) -> dict[str, object]:
    root, drift_path, preprocessing_path, drift, preprocessing = _protocols(args)
    head = _verify_clean_main(root)
    protocols = (drift, preprocessing)
    for path, protocol in zip((drift_path, preprocessing_path), protocols, strict=True):
        _tagged_protocol(root=root, path=path, protocol=protocol)
    registrations = _load_registrations(_resolve(root, args.registration))
    if tuple(item.protocol_sha256 for item in registrations) != tuple(
        item.canonical_sha256() for item in protocols
    ):
        raise P2RCloseoutError("P2R registrations differ from the execution protocols")
    expected_protocols = ",".join(item.canonical_sha256() for item in protocols)
    expected_registrations = ",".join(item.canonical_sha256() for item in registrations)
    if args.confirm_protocol_sha256s != expected_protocols:
        raise P2RCloseoutError("explicit P2R protocol confirmation is incorrect")
    if args.confirm_registration_sha256s != expected_registrations:
        raise P2RCloseoutError("explicit P2R registration confirmation is incorrect")
    marker = _resolve(root, args.marker)
    output = _resolve(root, args.output)
    if output.exists() or output.is_symlink():
        raise P2RCloseoutError("P2R terminal store already exists")

    manifest = load_v3_dataset_binding_manifest(_resolve(root, args.manifest))
    receipt = load_v3_dataset_binding_receipt(_resolve(root, args.receipt))
    if manifest.canonical_sha256() != drift.artifacts.dataset_manifest_sha256:
        raise P2RCloseoutError("runtime dataset manifest differs from both protocols")
    if receipt.canonical_sha256() != drift.artifacts.dataset_receipt_sha256:
        raise P2RCloseoutError("runtime dataset receipt differs from both protocols")
    reused_protocol = load_v3_3_confirmatory_protocol(_resolve(root, args.split_protocol))
    instrument = load_instrument_validity_protocol(
        _resolve(root, Path(drift.artifacts.instrument_protocol_uri))
    )
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(), protocols=protocols
    )
    environment = capture_execution_environment(head)
    _open_marker(path=marker, execution_commit=head, registrations=registrations)

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
            for protocol in protocols:
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
            {"data_drift": drift, "preprocessing_bug": preprocessing},
        )
        observations = paired_observations(plan=plan, measurements=checked_measurements)
        audit, closeout = build_joint_closeout(
            execution_commit=head,
            plan=plan,
            observations=observations,
            measurements=checked_measurements,
            environment=environment,
            protocols={"data_drift": drift, "preprocessing_bug": preprocessing},
            registrations={
                item.mechanism: item for item in registrations
            },
            instrument_protocol=instrument,
        )
        store = write_terminal_store(
            output_dir=output,
            protocols=protocols,
            registrations=registrations,
            terminal=closeout,
            environment=environment,
            measurements=checked_measurements,
            observations=observations,
            audit=audit,
        )
        return {
            "status": "registered_p2r_confirmatory_complete",
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
        )
        return {
            "status": "registered_p2r_technical_failure",
            "terminal_status": store.terminal_status,
            "terminal_store_sha256": store.store_sha256,
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
        "status": "p2r_terminal_store_verified",
        "terminal_status": store.terminal_status,
        "terminal_artifact_sha256": store.terminal_artifact_sha256,
        "terminal_store_sha256": store.store_sha256,
        "artifact_count": len(store.entries),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--drift-protocol", type=Path, default=DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    parser.add_argument(
        "--preprocessing-protocol",
        type=Path,
        default=DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_V3_DATASET_RECEIPT_PATH)
    parser.add_argument("--split-protocol", type=Path, default=DEFAULT_V3_3_PROTOCOL_PATH)
    parser.add_argument("--data-dir", type=Path, default=_DEFAULT_DATA_DIR)
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
            raise P2RCloseoutError("P2R execution requires both explicit hash confirmations")
        result = _execute(args)
    else:
        result = _verify(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
