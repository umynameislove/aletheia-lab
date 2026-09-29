"""Execute or verify one offline M4 model-artifact binding development cell."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.model_artifact_binding_development import (
    ModelArtifactBindingError,
    run_development_cell,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_verify import verify_development_cell


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "execute":
            run_development_cell(root=args.root, archive=args.archive, output=args.output)
        result = verify_development_cell(root=args.root, archive=args.archive, output=args.output)
    except (ModelArtifactBindingError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
