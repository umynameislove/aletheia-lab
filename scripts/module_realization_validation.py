"""Opt-in source-informed native MLflow realization/capture/cost study."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path
from unittest.mock import patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "prepare",
            "run",
            "verify",
            "build",
            "worker",
            "floor-worker",
            "forward",
            "verify-forward",
            "closeout",
            "verify-closeout",
            "retention",
            "verify-retention",
            "binding",
            "binding-worker",
            "verify-binding",
            "amortization",
            "verify-amortization",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--native-python", type=Path)
    parser.add_argument("--native-site", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    try:
        if args.action in {"binding", "binding-worker", "verify-binding"}:
            from aletheia_lab.evaluation import module_realization_binding_study as binding

            if args.action == "verify-binding":
                result = binding.verify(args.study_dir.resolve())
            elif args.action == "binding-worker":
                if args.config is None or args.artifacts is None:
                    raise ValueError("owned config/artifacts required")
                result = binding.native_worker(
                    json.loads(args.config.read_bytes()),
                    args.study_dir.resolve(),
                    args.artifacts.resolve(),
                )
            else:
                if args.artifacts is None or args.native_python is None or args.native_site is None:
                    raise ValueError("explicit owned native environment/artifacts required")
                result = binding.run(
                    args.root.resolve(),
                    args.study_dir.resolve(),
                    args.artifacts.resolve(),
                    args.native_python.absolute(),
                    args.native_site.resolve(),
                )
        elif args.action == "build":
            from aletheia_lab.evaluation.module_realization_source import create_models

            def deny(*values: object, **kwargs: object) -> None:
                raise PermissionError("owned artifact builder cannot use network")

            with (
                patch.object(socket.socket, "connect", deny),
                patch.object(socket.socket, "connect_ex", deny),
                patch.object(socket, "create_connection", deny),
            ):
                create_models(args.study_dir.resolve())
            result = {"status": "owned_artifacts_created"}
        elif args.action in {"worker", "floor-worker"}:
            if args.action == "worker":
                from aletheia_lab.evaluation.module_realization_runtime import serve
            else:
                from aletheia_lab.evaluation.module_realization_floor import serve

            if args.config is None or args.artifacts is None:
                raise ValueError("owned configuration/artifacts required")
            serve(
                json.loads(args.config.read_bytes()),
                args.study_dir.resolve(),
                args.artifacts.resolve(),
            )
            result = {"status": "owned_worker_closed"}
        elif args.action in {"forward", "verify-forward", "amortization", "verify-amortization"}:
            from aletheia_lab.evaluation import module_realization_forward as forward

            if args.action.startswith("verify-"):
                result = forward.verify(
                    args.study_dir.resolve(), amortization=args.action == "verify-amortization"
                )
            else:
                if args.native_python is None or args.native_site is None or args.artifacts is None:
                    raise ValueError("explicit owned native environment/artifacts required")
                result = forward.run(
                    args.root.resolve(),
                    args.study_dir.resolve(),
                    args.artifacts.resolve(),
                    args.native_python.absolute(),
                    args.native_site.resolve(),
                    amortization=args.action == "amortization",
                )
        elif args.action in {"retention", "verify-retention"}:
            from aletheia_lab.evaluation import module_realization_retention as retention

            if args.artifacts is None or args.study_dir.resolve().is_relative_to(
                args.root.resolve()
            ):
                raise ValueError("explicit source study and private destination required")
            retention_method = retention.run if args.action == "retention" else retention.verify
            result = retention_method(args.artifacts.resolve(), args.study_dir.resolve())
        else:
            from aletheia_lab.evaluation import module_realization_study as study

            root, directory = args.root.resolve(), args.study_dir.resolve()
            if args.action == "run":
                if args.native_python is None or args.native_site is None:
                    raise ValueError("explicit owned native environment required")
                result = study.run(
                    root, directory, args.native_python.absolute(), args.native_site.resolve()
                )
            else:
                if args.action in {"closeout", "verify-closeout"}:
                    result = study.closeout(root, directory, save=args.action == "closeout")
                else:
                    method = study.prepare if args.action == "prepare" else study.verify
                    result = method(root, directory)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {"status": "module_realization_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 1 if result.get("verification") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
