"""Preflight, execute and verify the registered label-noise confirmation study."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_closeout import (
    ProtocolRegistrationReceipt,
    build_closeout,
    capture_execution_environment,
    load_and_verify_result_store,
    registration_from_github_release,
    write_result_store,
)
from aletheia_lab.benchmark.p2.confirmatory_execution import ConfirmatoryExecutionError
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    DEFAULT_CONFIRMATORY_PROTOCOL_PATH,
    ConfirmatoryProtocol,
    load_confirmatory_protocol,
    verify_confirmatory_predecessor,
)
from aletheia_lab.benchmark.p2.confirmatory_registered import (
    execute_registered_dataset,
    load_registered_dataset,
)
from aletheia_lab.benchmark.p2.confirmatory_sources import (
    download_registered_archive,
    extract_registered_snapshot,
    sha256_file,
)

_RELEASE_API = (
    "https://api.github.com/repos/umynameislove/aletheia-lab/releases/tags/"
    "p2-label-noise-confirmatory-v2"
)
_DEFAULT_REGISTRATION = Path("experiments/p2/outputs/label-noise-confirmatory-v2-registration.json")
_DEFAULT_STORE = Path("experiments/p2/outputs/label-noise-confirmatory-v2")
_DEFAULT_PRIMARY = Path("data/processed/telco_customer_churn.csv")
_DEFAULT_BANK_ARCHIVE = Path("data/raw/bank-marketing.zip")
_DEFAULT_BANK_SNAPSHOT = Path("data/processed/bank-additional-full.csv")


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfirmatoryExecutionError("cannot verify registered Git state") from exc
    return completed.stdout.strip()


def _verify_clean_main(root: Path) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ConfirmatoryExecutionError("registered execution requires a clean worktree")
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "HEAD")
    origin_main = _git(root, "rev-parse", "refs/remotes/origin/main")
    if branch != "main" or head != origin_main:
        raise ConfirmatoryExecutionError(
            "registered execution requires main exactly synchronized with origin/main"
        )
    return head


def _verify_tag(root: Path, protocol: ConfirmatoryProtocol) -> str:
    tag = protocol.governance.required_git_tag
    if _git(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise ConfirmatoryExecutionError("registered protocol tag must be annotated")
    commit = _git(root, "rev-list", "-n", "1", tag)
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfirmatoryExecutionError("registered protocol tag is not an ancestor") from exc
    tagged_payload = _git(
        root,
        "show",
        f"{tag}:configs/benchmark/p2_label_noise_confirmatory_protocol.json",
    )
    try:
        tagged_protocol = ConfirmatoryProtocol.model_validate_json(tagged_payload)
    except ValueError as exc:
        raise ConfirmatoryExecutionError("tag does not contain a valid protocol") from exc
    if tagged_protocol.canonical_sha256() != protocol.canonical_sha256():
        raise ConfirmatoryExecutionError("tagged protocol differs from the execution protocol")
    return commit


def _github_release_payload() -> object:
    request = urllib.request.Request(
        _RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aletheia-lab-confirmatory-preflight",
        },
    )
    try:
        # Fixed HTTPS host and strict response validation are compensating controls for B310.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise ConfirmatoryExecutionError(
            "immutable GitHub release is missing or unavailable"
        ) from exc


def _write_registration_exclusive(path: Path, receipt: ProtocolRegistrationReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(receipt.model_dump_json(indent=2))
            handle.write("\n")
    except FileExistsError:
        existing = ProtocolRegistrationReceipt.model_validate_json(path.read_text(encoding="utf-8"))
        if existing != receipt:
            raise ConfirmatoryExecutionError(
                "existing registration receipt contains different evidence"
            ) from None


def _preflight(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    protocol = load_confirmatory_protocol(root / args.protocol)
    verify_confirmatory_predecessor(protocol, root=root)
    head = _verify_clean_main(root)
    tagged_commit = _verify_tag(root, protocol)
    registration = registration_from_github_release(
        protocol=protocol,
        tagged_protocol_commit=tagged_commit,
        payload=_github_release_payload(),
    )
    primary = next(item for item in protocol.datasets if item.role == "primary")
    primary_path = root / args.primary
    if sha256_file(primary_path) != primary.snapshot_sha256:
        raise ConfirmatoryExecutionError("primary snapshot checksum mismatch")
    external = next(item for item in protocol.datasets if item.role == "external_replication")
    archive = download_registered_archive(dataset=external, destination=root / args.bank_archive)
    snapshot = extract_registered_snapshot(
        dataset=external,
        archive_path=archive,
        destination=root / args.bank_snapshot,
    )
    _write_registration_exclusive(root / args.registration, registration)
    return {
        "status": "registered_preflight_pass",
        "protocol_sha256": protocol.canonical_sha256(),
        "registration_sha256": registration.canonical_sha256(),
        "tagged_protocol_commit": tagged_commit,
        "execution_commit": head,
        "primary_snapshot_sha256": sha256_file(primary_path),
        "replication_archive_sha256": sha256_file(archive),
        "replication_snapshot_sha256": sha256_file(snapshot),
        "sealed_test_opened": False,
        "outcomes_generated": False,
    }


def _execute(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    protocol = load_confirmatory_protocol(root / args.protocol)
    verify_confirmatory_predecessor(protocol, root=root)
    head = _verify_clean_main(root)
    tagged_commit = _verify_tag(root, protocol)
    registration = ProtocolRegistrationReceipt.model_validate_json(
        (root / args.registration).read_text(encoding="utf-8")
    )
    if registration.tagged_protocol_commit != tagged_commit:
        raise ConfirmatoryExecutionError("registration and local protocol tag disagree")
    if args.confirm_protocol_sha256 != protocol.canonical_sha256():
        raise ConfirmatoryExecutionError("explicit protocol confirmation is incorrect")
    if args.confirm_registration_sha256 != registration.canonical_sha256():
        raise ConfirmatoryExecutionError("explicit registration confirmation is incorrect")
    primary_binding = next(item for item in protocol.datasets if item.role == "primary")
    replication_binding = next(
        item for item in protocol.datasets if item.role == "external_replication"
    )
    primary_data = load_registered_dataset(
        protocol=protocol,
        dataset=primary_binding,
        snapshot_path=root / args.primary,
    )
    replication_data = load_registered_dataset(
        protocol=protocol,
        dataset=replication_binding,
        snapshot_path=root / args.bank_snapshot,
        archive_path=root / args.bank_archive,
    )
    # No result is printed or persisted until both complete frozen matrices exist.
    primary = execute_registered_dataset(protocol=protocol, registered=primary_data)
    replication = execute_registered_dataset(protocol=protocol, registered=replication_data)
    environment = capture_execution_environment(head)
    closeout = build_closeout(
        protocol=protocol,
        registration=registration,
        environment=environment,
        execution_commit=head,
        primary=primary,
        replication=replication,
    )
    manifest = write_result_store(
        output_dir=root / args.output,
        registration=registration,
        environment=environment,
        primary=primary,
        replication=replication,
        closeout=closeout,
    )
    return {
        "status": "registered_confirmatory_complete",
        "protocol_sha256": protocol.canonical_sha256(),
        "result_store_sha256": manifest.store_sha256,
        "primary_pass": closeout.decision.primary_pass,
        "replication_pass": closeout.decision.replication_pass,
        "mechanism_admitted": closeout.decision.mechanism_admitted,
        "cross_dataset_claim_allowed": closeout.decision.cross_dataset_claim_allowed,
        "disposition": closeout.decision.disposition,
        "outcomes_released_together": True,
    }


def _verify(args: argparse.Namespace) -> dict[str, object]:
    manifest = load_and_verify_result_store(Path(args.root).resolve() / args.output)
    return {
        "status": "result_store_verified",
        "protocol_sha256": manifest.protocol_sha256,
        "closeout_sha256": manifest.closeout_sha256,
        "result_store_sha256": manifest.store_sha256,
        "artifact_count": len(manifest.entries),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_CONFIRMATORY_PROTOCOL_PATH)
    parser.add_argument("--registration", type=Path, default=_DEFAULT_REGISTRATION)
    parser.add_argument("--primary", type=Path, default=_DEFAULT_PRIMARY)
    parser.add_argument("--bank-archive", type=Path, default=_DEFAULT_BANK_ARCHIVE)
    parser.add_argument("--bank-snapshot", type=Path, default=_DEFAULT_BANK_SNAPSHOT)
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
            raise ConfirmatoryExecutionError(
                "registered execution requires both explicit hash confirmations"
            )
        result = _execute(args)
    else:
        result = _verify(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
