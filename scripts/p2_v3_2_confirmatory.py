#!/usr/bin/env python3
"""Preflight, execute once, and verify the registered v3.2 recovery study."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_2_closeout import (
    V32ProtocolRegistrationReceipt,
    build_closeout,
    build_technical_failure,
    dataset_attempt,
    load_and_verify_terminal_store,
    registration_from_github_release,
    write_failure_store,
    write_result_store,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    DEFAULT_V3_2_PROTOCOL_PATH,
    V32ConfirmatoryProtocol,
    load_v3_2_confirmatory_protocol,
    verify_v3_2_compiled_split_receipts,
    verify_v3_2_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_closeout import (
    capture_execution_environment,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DEFAULT_V3_DATASET_BINDINGS_PATH,
    DEFAULT_V3_DATASET_RECEIPT_PATH,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import (
    ExecutionPlan,
    execute_v3_dataset_fail_closed,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    compile_v3_split_receipts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError

_TAG = "p2-label-noise-shift-factorial-v3.2"
_RELEASE_API = "https://api.github.com/repos/umynameislove/aletheia-lab/releases/tags/" + _TAG
_DEFAULT_DATA_DIR = Path("data/raw/p2-v3")
_DEFAULT_REGISTRATION = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.2-registration.json"
)
_DEFAULT_MARKER = Path("experiments/p2/outputs/label-noise-shift-factorial-v3.2-sealed-open.json")
_DEFAULT_STORE = Path("experiments/p2/outputs/label-noise-shift-factorial-v3.2")


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V3RuntimeError("cannot verify registered v3.2 Git state") from exc
    return completed.stdout.strip()


def _verify_clean_main(root: Path) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise V3RuntimeError("registered v3.2 execution requires a clean worktree")
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "HEAD")
    origin_main = _git(root, "rev-parse", "refs/remotes/origin/main")
    if branch != "main" or head != origin_main:
        raise V3RuntimeError(
            "registered v3.2 execution requires main synchronized with origin/main"
        )
    return head


def _protocol_from_text(payload: str) -> V32ConfirmatoryProtocol:
    try:
        return V32ConfirmatoryProtocol.model_validate_json(payload)
    except ValueError as exc:
        raise V3RuntimeError("tag does not contain a valid v3.2 protocol") from exc


def _verify_tag(root: Path, protocol_path: Path, expected_sha256: str) -> str:
    if _git(root, "cat-file", "-t", f"refs/tags/{_TAG}") != "tag":
        raise V3RuntimeError("registered v3.2 protocol tag must be annotated")
    commit = _git(root, "rev-list", "-n", "1", _TAG)
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V3RuntimeError("registered v3.2 tag is not an execution ancestor") from exc
    try:
        relative_protocol = protocol_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise V3RuntimeError("registered v3.2 protocol must be inside the repository") from exc
    tagged = _protocol_from_text(_git(root, "show", f"{_TAG}:{relative_protocol}"))
    if tagged.canonical_sha256() != expected_sha256:
        raise V3RuntimeError("tagged v3.2 protocol differs from the execution protocol")
    return commit


def _github_release_payload() -> object:
    request = urllib.request.Request(
        _RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aletheia-lab-v3-2-confirmatory-preflight",
        },
    )
    try:
        # Fixed HTTPS host and strict response validation compensate for Bandit B310.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise V3RuntimeError("immutable v3.2 GitHub release is unavailable") from exc


def _write_registration_exclusive(path: Path, receipt: V32ProtocolRegistrationReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = receipt.model_dump_json(indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        try:
            existing = V32ProtocolRegistrationReceipt.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise V3RuntimeError("existing v3.2 registration is invalid") from exc
        if existing != receipt:
            raise V3RuntimeError("existing v3.2 registration contains different evidence") from None


def _open_sealed_marker(
    *, path: Path, protocol_sha256: str, registration_sha256: str, execution_commit: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            {
                "schema_version": "p2-v3-2-sealed-open/1",
                "protocol_sha256": protocol_sha256,
                "registration_sha256": registration_sha256,
                "execution_commit": execution_commit,
                "opened_at": datetime.now(UTC).isoformat(),
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
        raise V3RuntimeError("v3.2 sealed marker exists; rerun is forbidden") from exc


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    root = Path(args.root).resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    return (
        root,
        resolve(args.protocol),
        resolve(args.manifest),
        resolve(args.receipt),
        resolve(args.data_dir),
        resolve(args.registration),
        resolve(args.marker),
    )


def _validated_inputs(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    root, protocol_path, manifest_path, receipt_path, data_dir, registration_path, marker = _paths(
        args
    )
    protocol = load_v3_2_confirmatory_protocol(protocol_path)
    _, manifest, receipt, _, _ = verify_v3_2_protocol_artifacts(protocol, root=root)
    expected_manifest = load_v3_dataset_binding_manifest(manifest_path)
    expected_receipt = load_v3_dataset_binding_receipt(receipt_path)
    if manifest != expected_manifest or receipt != expected_receipt:
        raise V3RuntimeError("v3.2 paths differ from protocol-bound dataset artifacts")
    observed_splits = compile_v3_split_receipts(
        manifest,
        receipt,
        archive_directory=data_dir,
    )
    verify_v3_2_compiled_split_receipts(protocol, observed_splits)
    return (
        root,
        protocol_path,
        data_dir,
        registration_path,
        marker,
        protocol,
        manifest,
        receipt,
    )


def _preflight(args: argparse.Namespace) -> dict[str, object]:
    (
        root,
        protocol_path,
        _data_dir,
        registration_path,
        marker,
        protocol,
        manifest,
        receipt,
    ) = _validated_inputs(args)
    head = _verify_clean_main(root)
    tagged_commit = _verify_tag(root, protocol_path, protocol.canonical_sha256())
    registration = registration_from_github_release(
        protocol=protocol,
        tagged_protocol_commit=tagged_commit,
        payload=_github_release_payload(),
    )
    output = args.output if args.output.is_absolute() else root / args.output
    if marker.exists() or marker.is_symlink() or output.exists() or output.is_symlink():
        raise V3RuntimeError("v3.2 sealed execution or terminal store already exists")
    _write_registration_exclusive(registration_path, registration)
    return {
        "status": "registered_v3_2_preflight_pass",
        "protocol_sha256": protocol.canonical_sha256(),
        "registration_sha256": registration.canonical_sha256(),
        "tagged_protocol_commit": tagged_commit,
        "execution_commit": head,
        "dataset_manifest_sha256": manifest.canonical_sha256(),
        "dataset_receipt_sha256": receipt.canonical_sha256(),
        "split_membership_sha256": {
            item.dataset_id: item.membership_sha256 for item in protocol.dataset_splits
        },
        "sealed_test_opened": False,
        "model_fitted": False,
        "predictive_metrics_generated": False,
        "outcomes_generated": False,
        "maximum_execution_attempts": 1,
    }


def _load_registration(path: Path) -> V32ProtocolRegistrationReceipt:
    try:
        return V32ProtocolRegistrationReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.2 registration receipt is unavailable or invalid") from exc


def _execute(args: argparse.Namespace) -> dict[str, object]:
    (
        root,
        protocol_path,
        data_dir,
        registration_path,
        marker,
        protocol,
        manifest,
        _receipt,
    ) = _validated_inputs(args)
    head = _verify_clean_main(root)
    tagged_commit = _verify_tag(root, protocol_path, protocol.canonical_sha256())
    registration = _load_registration(registration_path)
    if registration.tagged_protocol_commit != tagged_commit:
        raise V3RuntimeError("v3.2 registration and protocol tag disagree")
    if args.confirm_protocol_sha256 != protocol.canonical_sha256():
        raise V3RuntimeError("explicit v3.2 protocol confirmation is incorrect")
    if args.confirm_registration_sha256 != registration.canonical_sha256():
        raise V3RuntimeError("explicit v3.2 registration confirmation is incorrect")
    output = args.output if args.output.is_absolute() else root / args.output
    if output.exists() or output.is_symlink():
        raise V3RuntimeError("v3.2 terminal store already exists")
    environment = capture_execution_environment(head)
    _open_sealed_marker(
        path=marker,
        protocol_sha256=protocol.canonical_sha256(),
        registration_sha256=registration.canonical_sha256(),
        execution_commit=head,
    )
    bindings = {item.role: item for item in manifest.datasets}
    plan = ExecutionPlan.registered(protocol)
    stage: str = "load_primary"
    role: str | None = "primary"
    try:
        primary_binding, primary_frame = load_v3_dataset_snapshot_for_registration(
            dataset=bindings["primary"],
            archive_path=data_dir / bindings["primary"].archive.file_name,
        )
        stage = "execute_primary"
        primary = dataset_attempt(
            execute_v3_dataset_fail_closed(
                protocol=protocol,
                dataset=primary_binding,
                frame=primary_frame,
                plan=plan,
            )
        )
        stage = "load_replication"
        role = "external_replication"
        replication_binding, replication_frame = load_v3_dataset_snapshot_for_registration(
            dataset=bindings["external_replication"],
            archive_path=(data_dir / bindings["external_replication"].archive.file_name),
        )
        stage = "execute_replication"
        replication = dataset_attempt(
            execute_v3_dataset_fail_closed(
                protocol=protocol,
                dataset=replication_binding,
                frame=replication_frame,
                plan=plan,
            )
        )
        stage = "build_closeout"
        role = None
        closeout = build_closeout(
            protocol=protocol,
            registration=registration,
            environment=environment,
            execution_commit=head,
            primary=primary,
            replication=replication,
        )
        store = write_result_store(
            output_dir=output,
            registration=registration,
            environment=environment,
            primary=primary,
            replication=replication,
            closeout=closeout,
        )
        return {
            "status": "registered_v3_2_confirmatory_complete",
            "protocol_sha256": protocol.canonical_sha256(),
            "terminal_store_sha256": store.store_sha256,
            "terminal_status": store.terminal_status,
            "cross_dataset_claim_allowed": closeout.cross_dataset_claim_allowed,
            "disposition": closeout.disposition,
            "outcomes_released_together": True,
            "rerun_forbidden": True,
        }
    except Exception as exc:
        stage_value = stage
        if stage_value not in {
            "load_primary",
            "execute_primary",
            "load_replication",
            "execute_replication",
            "build_closeout",
        }:
            raise AssertionError("unreachable v3.2 failure stage") from exc
        role_value = role
        if role_value not in {None, "primary", "external_replication"}:
            raise AssertionError("unreachable v3.2 dataset role") from exc
        failure = build_technical_failure(
            registration=registration,
            execution_commit=head,
            failure_stage=stage_value,  # type: ignore[arg-type]
            dataset_role=role_value,  # type: ignore[arg-type]
            error=exc,
        )
        store = write_failure_store(
            output_dir=output,
            registration=registration,
            environment=environment,
            failure=failure,
        )
        return {
            "status": "registered_v3_2_technical_failure",
            "protocol_sha256": protocol.canonical_sha256(),
            "terminal_store_sha256": store.store_sha256,
            "terminal_status": store.terminal_status,
            "failure_stage": failure.failure_stage,
            "exception_class": failure.exception_class,
            "exception_message_sha256": failure.exception_message_sha256,
            "partial_outcome_published": False,
            "scientific_disposition_generated": False,
            "rerun_forbidden": True,
        }


def _verify(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    manifest = load_and_verify_terminal_store(output)
    return {
        "status": "v3_2_terminal_store_verified",
        "protocol_sha256": manifest.protocol_sha256,
        "terminal_status": manifest.terminal_status,
        "terminal_artifact_sha256": manifest.terminal_artifact_sha256,
        "terminal_store_sha256": manifest.store_sha256,
        "artifact_count": len(manifest.entries),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_V3_2_PROTOCOL_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_V3_DATASET_BINDINGS_PATH)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_V3_DATASET_RECEIPT_PATH)
    parser.add_argument("--data-dir", type=Path, default=_DEFAULT_DATA_DIR)
    parser.add_argument("--registration", type=Path, default=_DEFAULT_REGISTRATION)
    parser.add_argument("--marker", type=Path, default=_DEFAULT_MARKER)
    parser.add_argument("--output", type=Path, default=_DEFAULT_STORE)
    parser.add_argument("--confirm-protocol-sha256")
    parser.add_argument("--confirm-registration-sha256")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "preflight":
        result = _preflight(args)
    elif args.command == "execute":
        if not args.confirm_protocol_sha256 or not args.confirm_registration_sha256:
            raise V3RuntimeError("v3.2 execution requires both hash confirmations")
        result = _execute(args)
    else:
        result = _verify(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
