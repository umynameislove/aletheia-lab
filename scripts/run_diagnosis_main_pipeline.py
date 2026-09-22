#!/usr/bin/env python3
"""Preflight, authorize, execute/resume, or verify the diagnosis main pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aletheia_lab.diagnosis.main_pipeline import (
    DiagnosisMainPipelineAuthorization,
    DiagnosisMainPipelineError,
    DiagnosisMainPipelinePreflight,
    build_pipeline_authorization,
    checked_pipeline_run_directory,
    execute_authorized_main_pipeline,
    load_private_main_packet,
    publish_pipeline_authorization,
    rehearse_main_pipeline,
    verify_completed_pipeline,
)
from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import checked_private_path
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json

ModelT = TypeVar("ModelT", bound=BaseModel)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--root", type=Path, default=Path("."))
    preflight.add_argument("--private-packet", type=Path, required=True)
    preflight.add_argument("--workspace", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)

    authorize = subparsers.add_parser("authorize")
    authorize.add_argument("--root", type=Path, default=Path("."))
    authorize.add_argument("--private-packet", type=Path, required=True)
    authorize.add_argument("--preflight", type=Path, required=True)
    authorize.add_argument("--run-dir", type=Path, required=True)
    authorize.add_argument("--authorized-at", required=True)
    authorize.add_argument("--operator-cost-ceiling-usd", type=float, required=True)
    authorize.add_argument("--confirm-preflight-sha256", required=True)

    execute = subparsers.add_parser("execute")
    execute.add_argument("--root", type=Path, default=Path("."))
    execute.add_argument("--private-packet", type=Path, required=True)
    execute.add_argument("--preflight", type=Path, required=True)
    execute.add_argument("--run-dir", type=Path, required=True)
    execute.add_argument("--confirm-authorization-sha256", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, default=Path("."))
    verify.add_argument("--run-dir", type=Path, required=True)
    return parser


def _outside_repository(root: Path, path: Path) -> Path:
    try:
        return checked_private_path(path.expanduser(), root)
    except ValueError as exc:
        raise DiagnosisMainPipelineError(
            "private pipeline artifacts must remain outside git"
        ) from exc


def _read(model: type[ModelT], path: Path) -> ModelT:
    if path.is_symlink() or not path.is_file():
        raise DiagnosisMainPipelineError("pipeline input artifact is unavailable")
    return model.model_validate_json(path.read_bytes())


def _emit(model: BaseModel) -> None:
    payload = model.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def _preflight(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    state = inspect_repository_state(root)
    if not state.synchronized_main:
        raise DiagnosisMainPipelineError("preflight requires clean synchronized main")
    packet = load_private_main_packet(root, args.private_packet)
    workspace = _outside_repository(root, args.workspace)
    output = _outside_repository(root, args.output)
    if output == workspace or output.is_relative_to(workspace):
        raise DiagnosisMainPipelineError("preflight receipt must remain outside its raw workspace")
    receipt = rehearse_main_pipeline(
        root=root,
        packet=packet,
        workspace=workspace,
        source_commit_ref=state.head_commit,
    )
    publish_immutable_file(
        output,
        (canonical_project_json(receipt.model_dump(mode="json")) + "\n").encode(),
    )
    _emit(receipt)
    return 0


def _authorize(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    packet = load_private_main_packet(root, args.private_packet)
    preflight_path = _outside_repository(root, args.preflight)
    preflight = _read(DiagnosisMainPipelinePreflight, preflight_path)
    state = inspect_repository_state(root)
    run_dir = checked_pipeline_run_directory(root, args.run_dir)
    authorization = build_pipeline_authorization(
        root=root,
        packet=packet,
        preflight=preflight,
        repository_state=state,
        run_dir=run_dir,
        authorized_at=args.authorized_at,
        operator_cost_ceiling_usd=args.operator_cost_ceiling_usd,
        confirmed_preflight_sha256=args.confirm_preflight_sha256,
    )
    publish_pipeline_authorization(run_dir, authorization)
    _emit(authorization)
    return 0


def _execute(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    packet = load_private_main_packet(root, args.private_packet)
    preflight = _read(
        DiagnosisMainPipelinePreflight,
        _outside_repository(root, args.preflight),
    )
    run_dir = checked_pipeline_run_directory(root, args.run_dir)
    authorization = _read(
        DiagnosisMainPipelineAuthorization,
        run_dir / "authorization.json",
    )
    receipt = execute_authorized_main_pipeline(
        root=root,
        packet=packet,
        preflight=preflight,
        authorization=authorization,
        repository_state=inspect_repository_state(root),
        run_dir=run_dir,
        confirmed_authorization_sha256=args.confirm_authorization_sha256,
    )
    _emit(receipt)
    return 0


def _verify(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    run_dir = checked_pipeline_run_directory(root, args.run_dir)
    authorization = _read(
        DiagnosisMainPipelineAuthorization,
        run_dir / "authorization.json",
    )
    receipt = verify_completed_pipeline(run_dir=run_dir, authorization=authorization)
    _emit(receipt)
    return 0


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "preflight":
            return _preflight(args)
        if args.command == "authorize":
            return _authorize(args)
        if args.command == "execute":
            return _execute(args)
        return _verify(args)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        print(
            json.dumps(
                {
                    "status": "diagnosis_main_pipeline_failed_closed",
                    "error_type": type(exc).__name__,
                    "reason": (
                        str(exc)
                        if isinstance(exc, DiagnosisMainPipelineError)
                        else "local input or frozen-runtime validation failed"
                    ),
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
