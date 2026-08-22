#!/usr/bin/env python3
"""Preflight, execute once, and verify the registered v3 confirmatory study."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_v3_closeout import (
    V3ProtocolRegistrationReceipt,
    build_closeout,
    capture_execution_environment,
    load_and_verify_result_store,
    registration_from_github_release,
    write_result_store,
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
    execute_v3_dataset,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DEFAULT_V3_PROTOCOL_PATH,
    compile_v3_split_receipts,
    load_v3_confirmatory_protocol,
    verify_compiled_split_receipts,
    verify_v3_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError

_RELEASE_API = (
    "https://api.github.com/repos/umynameislove/aletheia-lab/releases/tags/"
    "p2-label-noise-shift-factorial-v3.1"
)
_DEFAULT_DATA_DIR = Path("data/raw/p2-v3")
_DEFAULT_REGISTRATION = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.1-registration.json"
)
_DEFAULT_MARKER = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.1-sealed-open.json"
)
_DEFAULT_STORE = Path("experiments/p2/outputs/label-noise-shift-factorial-v3.1")


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V3RuntimeError("cannot verify registered Git state") from exc
    return completed.stdout.strip()


def _verify_clean_main(root: Path) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise V3RuntimeError("registered execution requires a clean worktree")
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "HEAD")
    origin_main = _git(root, "rev-parse", "refs/remotes/origin/main")
    if branch != "main" or head != origin_main:
        raise V3RuntimeError(
            "registered execution requires main exactly synchronized with origin/main"
        )
    return head


def _verify_tag(root: Path, protocol_path: Path, expected_protocol_sha256: str) -> str:
    tag = "p2-label-noise-shift-factorial-v3.1"
    if _git(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise V3RuntimeError("registered protocol tag must be annotated")
    commit = _git(root, "rev-list", "-n", "1", tag)
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V3RuntimeError("registered protocol tag is not an ancestor of execution") from exc
    try:
        relative_protocol = protocol_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise V3RuntimeError("registered protocol must be inside the repository") from exc
    tagged_payload = _git(root, "show", f"{tag}:{relative_protocol}")
    tagged_protocol = load_v3_confirmatory_protocol_from_text(tagged_payload)
    if tagged_protocol.canonical_sha256() != expected_protocol_sha256:
        raise V3RuntimeError("tagged protocol differs from the execution protocol")
    return commit


def load_v3_confirmatory_protocol_from_text(payload: str):  # type: ignore[no-untyped-def]
    """Validate tagged protocol JSON without touching the working tree."""

    from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import V3ConfirmatoryProtocol

    try:
        return V3ConfirmatoryProtocol.model_validate_json(payload)
    except ValueError as exc:
        raise V3RuntimeError("tag does not contain a valid v3 protocol") from exc


def _github_release_payload() -> object:
    request = urllib.request.Request(
        _RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aletheia-lab-v3-confirmatory-preflight",
        },
    )
    try:
        # Fixed HTTPS host and strict response validation compensate for Bandit B310.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise V3RuntimeError("immutable GitHub release is missing or unavailable") from exc


def _write_registration_exclusive(
    path: Path, receipt: V3ProtocolRegistrationReceipt
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = receipt.model_dump_json(indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        try:
            existing = V3ProtocolRegistrationReceipt.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise V3RuntimeError("existing registration receipt is invalid") from exc
        if existing != receipt:
            raise V3RuntimeError(
                "existing registration receipt contains different evidence"
            ) from None


def _open_sealed_marker(
    *, path: Path, protocol_sha256: str, registration_sha256: str, execution_commit: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        {
            "schema_version": "p2-v3-sealed-open/1",
            "protocol_sha256": protocol_sha256,
            "registration_sha256": registration_sha256,
            "execution_commit": execution_commit,
            "opened_at": datetime.now(UTC).isoformat(),
            "rerun_forbidden": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise V3RuntimeError(
            "sealed execution marker already exists; rerun is forbidden"
        ) from exc


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
    root, protocol_path, manifest_path, receipt_path, data_dir, registration_path, marker = (
        _paths(args)
    )
    protocol = load_v3_confirmatory_protocol(protocol_path)
    _, manifest, receipt = verify_v3_protocol_artifacts(protocol, root=root)
    expected_manifest = load_v3_dataset_binding_manifest(manifest_path)
    expected_receipt = load_v3_dataset_binding_receipt(receipt_path)
    if manifest != expected_manifest or receipt != expected_receipt:
        raise V3RuntimeError("execution paths do not resolve to the protocol-bound artifacts")
    observed_splits = compile_v3_split_receipts(
        manifest, receipt, archive_directory=data_dir
    )
    verify_compiled_split_receipts(protocol, observed_splits)
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
        raise V3RuntimeError("sealed execution or result store already exists")
    _write_registration_exclusive(registration_path, registration)
    return {
        "status": "registered_v3_preflight_pass",
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
    }


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
    try:
        registration = V3ProtocolRegistrationReceipt.model_validate_json(
            registration_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("registered execution receipt is unavailable or invalid") from exc
    if registration.tagged_protocol_commit != tagged_commit:
        raise V3RuntimeError("registration and local protocol tag disagree")
    if args.confirm_protocol_sha256 != protocol.canonical_sha256():
        raise V3RuntimeError("explicit protocol confirmation is incorrect")
    if args.confirm_registration_sha256 != registration.canonical_sha256():
        raise V3RuntimeError("explicit registration confirmation is incorrect")
    output = args.output if args.output.is_absolute() else root / args.output
    if output.exists() or output.is_symlink():
        raise V3RuntimeError("v3 result store already exists")
    _open_sealed_marker(
        path=marker,
        protocol_sha256=protocol.canonical_sha256(),
        registration_sha256=registration.canonical_sha256(),
        execution_commit=head,
    )
    bindings = {item.role: item for item in manifest.datasets}
    loaded = {
        role: load_v3_dataset_snapshot_for_registration(
            dataset=binding,
            archive_path=data_dir / binding.archive.file_name,
        )
        for role, binding in bindings.items()
    }
    plan = ExecutionPlan.registered(protocol)
    primary_binding, primary_frame = loaded["primary"]
    replication_binding, replication_frame = loaded["external_replication"]
    # Nothing is printed or persisted until both complete outcomes and closeout exist.
    primary = execute_v3_dataset(
        protocol=protocol, dataset=primary_binding, frame=primary_frame, plan=plan
    )
    replication = execute_v3_dataset(
        protocol=protocol,
        dataset=replication_binding,
        frame=replication_frame,
        plan=plan,
    )
    environment = capture_execution_environment(head)
    closeout = build_closeout(
        protocol=protocol,
        registration=registration,
        environment=environment,
        execution_commit=head,
        primary=primary,
        replication=replication,
    )
    manifest_store = write_result_store(
        output_dir=output,
        registration=registration,
        environment=environment,
        primary=primary,
        replication=replication,
        closeout=closeout,
    )
    return {
        "status": "registered_v3_confirmatory_complete",
        "protocol_sha256": protocol.canonical_sha256(),
        "result_store_sha256": manifest_store.store_sha256,
        "cross_dataset_claim_allowed": closeout.decision.cross_dataset_claim_allowed,
        "direction_dispositions": closeout.decision.direction_dispositions,
        "disposition": closeout.decision.disposition,
        "outcomes_released_together": True,
    }


def _verify(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    manifest = load_and_verify_result_store(output)
    return {
        "status": "v3_result_store_verified",
        "protocol_sha256": manifest.protocol_sha256,
        "closeout_sha256": manifest.closeout_sha256,
        "result_store_sha256": manifest.store_sha256,
        "artifact_count": len(manifest.entries),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_V3_PROTOCOL_PATH)
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
            raise V3RuntimeError("execution requires both explicit hash confirmations")
        result = _execute(args)
    else:
        result = _verify(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
