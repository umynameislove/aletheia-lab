"""Execute only the newly sealed local auditability closeout frame."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Existing tooling is optional; the native maintainer test imports pytest.
tooling = (
    Path(__file__).resolve().parents[2] / "working-baseline/.venv/lib/python3.12/site-packages"
)
if tooling.is_dir():
    sys.path.append(str(tooling))

from aletheia_lab.evaluation.auditability_closeout import (  # noqa: E402
    analyze,
    prepare,
    run,
    worker,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "run",
            "worker",
            "analyze",
            "crash-worker",
            "run-extension",
            "closeout",
            "verify-closeout",
        ),
    )
    parser.add_argument("--phase", choices=("before_commit", "after_ack"), default="after_ack")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--kind", choices=("transfer", "cost"), default="transfer")
    parser.add_argument("--arm", default="original")
    parser.add_argument("--mode", default="native")
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--repeat", type=int, default=0)
    parser.add_argument("--seed", type=int, choices=(0, 59, 61), default=0)
    args = parser.parse_args()
    root, study = args.root.resolve(), args.study_dir.resolve()
    if args.command == "crash-worker":
        from aletheia_lab.evaluation.auditability_closeout_controls import crash_worker

        crash_worker(study, args.mode, args.phase)
        return
    if args.command == "prepare":
        result = prepare(root, study)
    elif args.command in {"closeout", "verify-closeout"}:
        from aletheia_lab.evaluation.auditability_closeout import read
        from aletheia_lab.evaluation.auditability_closeout_extension import scientific_closeout
        from aletheia_lab.evaluation.model_load_retention import encode
        from aletheia_lab.filesystem import write_new_file

        report = scientific_closeout(study)
        path = study / "scientific-closeout.json"
        if args.command == "closeout":
            write_new_file(path, encode(report).encode())
        elif read(path) != report:
            raise ValueError("scientific closeout differs from immutable inputs")
        result = {
            "status": "scientific_closeout_verified"
            if args.command == "verify-closeout"
            else "scientific_closeout_written",
            "sha256": report["sha256"],
            "census": report["corrected_and_fresh"]["totals"],
            "parent_status": report["parent_status"],
            "provider_calls": 0,
        }
    elif args.command == "run-extension":
        from aletheia_lab.evaluation.auditability_closeout_extension import run_extension

        result = run_extension(root, study)
    elif args.command == "run":
        result = run(root, study)
    elif args.command == "analyze":
        result = analyze(study)
    else:
        if args.output is None:
            parser.error("worker requires output")
        result = worker(
            root,
            study,
            args.output.resolve(),
            args.kind,
            args.arm,
            args.mode,
            args.count,
            args.repeat,
            args.seed,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
