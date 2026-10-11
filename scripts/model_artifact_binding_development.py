"""Execute or verify one offline M4 model-artifact binding development cell."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.model_artifact_binding_development import (
    ModelArtifactBindingError,
    run_development_cell,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import (
    execute_forward_cell,
    prepare_forward_cell,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward_verify import verify_forward_cell
from aletheia_lab.benchmark.p2.model_artifact_binding_verify import verify_development_cell


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("execute", "verify", "prepare-forward", "execute-forward", "verify-forward"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--predecessor-output", type=Path)
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()
    if args.action.endswith("-forward") and args.predecessor_output is None:
        parser.error("forward operations require --predecessor-output")
    if args.action == "execute-forward" and args.confirm_plan_sha256 is None:
        parser.error("execute-forward requires --confirm-plan-sha256")
    try:
        if args.action.endswith("-forward"):
            options = {
                "root": args.root,
                "archive": args.archive,
                "output": args.output,
                "predecessor": args.predecessor_output,
            }
            if args.action == "prepare-forward":
                result = prepare_forward_cell(**options)
            else:
                if args.action == "execute-forward":
                    execute_forward_cell(**options, confirm_plan_sha256=args.confirm_plan_sha256)
                result = verify_forward_cell(**options)
        else:
            if args.action == "execute":
                run_development_cell(root=args.root, archive=args.archive, output=args.output)
            result = verify_development_cell(
                root=args.root, archive=args.archive, output=args.output
            )
    except (ModelArtifactBindingError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
