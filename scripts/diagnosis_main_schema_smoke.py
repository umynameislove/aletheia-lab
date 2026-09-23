#!/usr/bin/env python3
"""Prepare, execute once, or verify the synthetic main-schema transport smoke."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.diagnosis._main_pipeline_contracts import (
    DiagnosisMainPipelineAuthorization,
)
from aletheia_lab.diagnosis.main_pipeline import verify_completed_pipeline
from aletheia_lab.diagnosis.main_schema_smoke import (
    EXPECTED_FAILED_MAIN_COUNTS,
    SMOKE_PLAN_SCHEMA_VERSION,
    SMOKE_RECEIPT_SCHEMA_VERSION,
    OpenAIMainRecoveryAdapter,
    build_smoke_plan,
    build_synthetic_main_schema_request,
    provider_call,
    validate_self_hash,
    validate_smoke_response,
)
from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import checked_private_path
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import AdapterInvocationError
from aletheia_lab.project.identity import canonical_project_json, content_sha256


class SchemaSmokeError(ValueError):
    """Public-safe local failure at the one-shot smoke boundary."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "execute", "verify"):
        item = subparsers.add_parser(command)
        item.add_argument("--root", type=Path, default=Path("."))
        item.add_argument("--predecessor-run", type=Path, required=True)
        item.add_argument("--smoke-dir", type=Path, required=True)
        if command == "execute":
            item.add_argument("--confirm-plan-sha256", required=True)
    return parser


def _serialized(payload: dict[str, object]) -> bytes:
    return (canonical_project_json(payload) + "\n").encode()


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise SchemaSmokeError(f"required smoke artifact is unavailable: {path.name}")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise SchemaSmokeError(f"smoke artifact root is not an object: {path.name}")
    return value


def _tree_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise SchemaSmokeError("predecessor run is not a real directory")
    inventory: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise SchemaSmokeError("predecessor run contains a symbolic link")
        if path.is_file():
            inventory.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": content_sha256(path.read_bytes()),
                }
            )
    if not inventory:
        raise SchemaSmokeError("predecessor run contains no evidence files")
    return canonical_execution_sha256(inventory)


def _private_directories(
    root: Path,
    predecessor_run: Path,
    smoke_dir: Path,
) -> tuple[Path, Path]:
    predecessor = checked_private_path(predecessor_run.expanduser(), root)
    smoke = checked_private_path(smoke_dir.expanduser(), root)
    if (
        predecessor == smoke
        or predecessor.is_relative_to(smoke)
        or smoke.is_relative_to(predecessor)
    ):
        raise SchemaSmokeError("smoke destination overlaps the preserved predecessor")
    return predecessor, smoke


def _predecessor_facts(run_dir: Path) -> tuple[str, str]:
    authorization = DiagnosisMainPipelineAuthorization.model_validate_json(
        (run_dir / "authorization.json").read_bytes()
    )
    receipt = verify_completed_pipeline(run_dir=run_dir, authorization=authorization)
    if (
        receipt.main_terminal_status_counts != EXPECTED_FAILED_MAIN_COUNTS
        or receipt.logical_request_count != 1024
        or receipt.relation_request_count != 0
        or receipt.relation_terminal_status_counts
    ):
        raise SchemaSmokeError("predecessor is not the exact preserved 896-failure run")
    return receipt.receipt_sha256, _tree_sha256(run_dir)


def _require_clean_main(root: Path) -> str:
    state = inspect_repository_state(root)
    if not state.synchronized_main:
        raise SchemaSmokeError("smoke gate requires clean synchronized main")
    return state.head_commit


def _expected_plan(root: Path, predecessor: Path) -> dict[str, object]:
    source_commit = _require_clean_main(root)
    receipt_sha, tree_sha = _predecessor_facts(predecessor)
    return build_smoke_plan(
        root,
        source_commit_ref=source_commit,
        predecessor_receipt_sha256=receipt_sha,
        predecessor_tree_sha256=tree_sha,
    )


def _prepare(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    predecessor, smoke = _private_directories(root, args.predecessor_run, args.smoke_dir)
    if smoke.exists() and (smoke.is_symlink() or not smoke.is_dir() or any(smoke.iterdir())):
        raise SchemaSmokeError("smoke directory must be absent or empty")
    plan = _expected_plan(root, predecessor)
    if not smoke.exists():
        smoke.mkdir(parents=True)
    if publish_immutable_file(smoke / "plan.json", _serialized(plan)) != "created":
        raise SchemaSmokeError("smoke plan already exists")
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def _load_and_reconcile_plan(
    root: Path,
    predecessor: Path,
    smoke: Path,
) -> dict[str, object]:
    plan = _load_object(smoke / "plan.json")
    if plan.get("schema_version") != SMOKE_PLAN_SCHEMA_VERSION:
        raise SchemaSmokeError("smoke plan schema version changed")
    validate_self_hash(plan, "plan_sha256")
    if plan != _expected_plan(root, predecessor):
        raise SchemaSmokeError("smoke plan differs from current code or predecessor evidence")
    return plan


def _lease(plan: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "diagnosis-main-schema-smoke-lease/v1",
        "plan_sha256": plan["plan_sha256"],
        "provider_call_ceiling": 1,
        "sdk_retries": 0,
        "synthetic_payload_only": True,
    }
    return {**payload, "lease_sha256": canonical_execution_sha256(payload)}


def _receipt(
    *,
    plan: dict[str, object],
    lease: dict[str, object],
    status: str,
    tree_before: str,
    tree_after: str,
    provider_attempt_ref: str | None,
    response_sha256: str | None,
    response_bytes: int | None,
    usage: dict[str, object] | None,
    error_code: str | None,
) -> dict[str, object]:
    input_tokens = usage.get("input_tokens") if usage else None
    output_tokens = usage.get("output_tokens") if usage else None
    estimated_cost = None
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        estimated_cost = round((input_tokens * 2 + output_tokens * 8) / 1_000_000, 8)
    payload: dict[str, object] = {
        "schema_version": SMOKE_RECEIPT_SCHEMA_VERSION,
        "status": status,
        "plan_sha256": plan["plan_sha256"],
        "lease_sha256": lease["lease_sha256"],
        "provider_call_count": 1,
        "sdk_retries": 0,
        "synthetic_payload_only": True,
        "provider_attempt_ref": provider_attempt_ref,
        "response_sha256": response_sha256,
        "response_utf8_bytes": response_bytes,
        "usage": usage,
        "estimated_cost_usd_at_frozen_rates": estimated_cost,
        "error_code": error_code,
        "original_schema_local_validation_required": True,
        "raw_response_persisted": False,
        "predecessor_tree_sha256_before": tree_before,
        "predecessor_tree_sha256_after": tree_after,
        "predecessor_mutated": tree_before != tree_after,
        "recovery_authorized": False,
    }
    return {**payload, "receipt_sha256": canonical_execution_sha256(payload)}


def _execute(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    predecessor, smoke = _private_directories(root, args.predecessor_run, args.smoke_dir)
    if not smoke.is_dir() or {item.name for item in smoke.iterdir()} != {"plan.json"}:
        raise SchemaSmokeError("smoke directory is not fresh or has already been consumed")
    plan = _load_and_reconcile_plan(root, predecessor, smoke)
    if args.confirm_plan_sha256 != plan["plan_sha256"]:
        raise SchemaSmokeError("explicit plan confirmation does not match")

    request, policy = build_synthetic_main_schema_request(
        root, source_commit_ref=str(plan["source_commit_ref"])
    )
    adapter = OpenAIMainRecoveryAdapter.from_environment(
        model_policy=request.initial_attempt.model_policy,
        policy=policy,
    )
    lease = _lease(plan)
    if publish_immutable_file(smoke / "lease.json", _serialized(lease)) != "created":
        raise SchemaSmokeError("smoke lease is already consumed")

    tree_before = _tree_sha256(predecessor)
    status = "provider_failed"
    provider_attempt_ref = None
    response_sha = None
    response_bytes = None
    usage = None
    error_code = None
    try:
        envelope = adapter.invoke(provider_call(request))
        provider_attempt_ref = envelope.provider_attempt_ref
        response_sha = envelope.raw_response.content_sha256
        response_bytes = envelope.raw_response.byte_count
        usage = envelope.usage.model_dump(mode="json")
        validate_smoke_response(envelope.raw_response.content, request=request)
        status = "pass"
    except AdapterInvocationError as exc:
        provider_attempt_ref = exc.provider_attempt_ref
        error_code = exc.code
    except (UnicodeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        status = "response_invalid"
        error_code = type(exc).__name__

    tree_after = _tree_sha256(predecessor)
    if tree_after != tree_before or tree_before != plan["predecessor_tree_sha256"]:
        raise SchemaSmokeError("preserved predecessor changed during smoke execution")
    receipt = _receipt(
        plan=plan,
        lease=lease,
        status=status,
        tree_before=tree_before,
        tree_after=tree_after,
        provider_attempt_ref=provider_attempt_ref,
        response_sha256=response_sha,
        response_bytes=response_bytes,
        usage=usage,
        error_code=error_code,
    )
    publish_immutable_file(smoke / "receipt.json", _serialized(receipt))
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if status == "pass" else 1


def _verify(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    predecessor, smoke = _private_directories(root, args.predecessor_run, args.smoke_dir)
    if {item.name for item in smoke.iterdir()} != {
        "plan.json",
        "lease.json",
        "receipt.json",
    }:
        raise SchemaSmokeError("smoke directory is incomplete or has unknown artifacts")
    plan = _load_and_reconcile_plan(root, predecessor, smoke)
    lease = _load_object(smoke / "lease.json")
    receipt = _load_object(smoke / "receipt.json")
    validate_self_hash(lease, "lease_sha256")
    validate_self_hash(receipt, "receipt_sha256")
    current_tree = _tree_sha256(predecessor)
    if (
        lease != _lease(plan)
        or receipt.get("schema_version") != SMOKE_RECEIPT_SCHEMA_VERSION
        or receipt.get("plan_sha256") != plan["plan_sha256"]
        or receipt.get("lease_sha256") != lease["lease_sha256"]
        or receipt.get("provider_call_count") != 1
        or receipt.get("raw_response_persisted") is not False
        or receipt.get("predecessor_mutated") is not False
        or receipt.get("predecessor_tree_sha256_before") != current_tree
        or receipt.get("predecessor_tree_sha256_after") != current_tree
    ):
        raise SchemaSmokeError("smoke receipt does not reconcile")
    if receipt.get("status") != "pass":
        raise SchemaSmokeError("synthetic schema smoke did not pass; recovery remains blocked")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "prepare":
            return _prepare(args)
        if args.command == "execute":
            return _execute(args)
        return _verify(args)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        reason = str(exc) if isinstance(exc, SchemaSmokeError) else "local smoke gate failed closed"
        print(
            json.dumps(
                {
                    "status": "diagnosis_main_schema_smoke_failed_closed",
                    "error_type": type(exc).__name__,
                    "reason": reason,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
