"""Opt-in application auditability and audit-obligation admission development run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.application_audit_study import execute_worker, run, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify", "worker"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--bento-dependencies", type=Path)
    parser.add_argument("--mlflow-dependencies", type=Path)
    parser.add_argument("--mlflow-asgi-dependencies", type=Path)
    parser.add_argument("--mlflow-python", type=Path)
    parser.add_argument("--stack", choices=("bentoml", "mlflow"))
    args = parser.parse_args()
    try:
        if args.action == "worker":
            if args.stack is None:
                raise ValueError("private worker requires its source stack")
            execute_worker(args.stack, args.study_dir.resolve())
            result = {"status": "application_audit_worker_complete", "stack": args.stack}
        elif args.action == "verify":
            result = verify(args.root.resolve(), args.study_dir.resolve())
        else:
            if any(
                value is None
                for value in (
                    args.bento_dependencies,
                    args.mlflow_dependencies,
                    args.mlflow_asgi_dependencies,
                    args.mlflow_python,
                )
            ):
                raise ValueError("run requires explicit isolated serving dependencies")
            result = run(
                args.root.resolve(),
                args.study_dir.resolve(),
                args.bento_dependencies,
                args.mlflow_dependencies,
                args.mlflow_asgi_dependencies,
                args.mlflow_python,
            )
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {
                    "status": "application_audit_development_failed_closed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
