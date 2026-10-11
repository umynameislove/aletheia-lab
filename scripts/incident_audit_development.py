"""Run bounded native incident-audit development or read-only verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.cache_lifecycle_study import seal
from aletheia_lab.evaluation.incident_audit_controls import crash_worker, run_controls
from aletheia_lab.evaluation.incident_audit_study import PROTOCOL, native_control, run, worker
from aletheia_lab.evaluation.incident_audit_verification import verify
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "worker", "verify", "controls", "crash-worker"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--policy", choices=("static", "union_density", "lru", "size_cost"), default="static"
    )
    parser.add_argument("--phase", choices=("before_commit", "after_ack"), default="after_ack")
    parser.add_argument(
        "--source-arm",
        choices=("observed", "route_repair", "complete_capture", "native_floor"),
        default="observed",
    )
    args = parser.parse_args()
    try:
        if args.command == "crash-worker":
            crash_worker(args.study_dir.resolve(), args.policy, args.phase)
            return
        if args.command == "controls":
            result = run_controls(args.root.resolve(), args.study_dir.resolve())
        elif args.command == "worker":
            if args.output is None:
                parser.error("worker requires an owned output path")
            config = json.loads((args.root / PROTOCOL).read_bytes())
            config["source_arm"] = args.source_arm
            result = (
                native_control(config)
                if args.source_arm == "native_floor"
                else worker(config, args.study_dir)
            )
            write_new_file(args.output, encode(seal(result)).encode())
            result = {"status": result["status"], "provider_calls": 0}
        elif args.command == "verify":
            result = verify(args.root.resolve(), args.study_dir.resolve())
        else:
            result = run(args.root.resolve(), args.study_dir.resolve())
    except (ValueError, OSError, RuntimeError) as exc:
        print(
            json.dumps({"status": "incident_audit_failed_closed", "error_type": type(exc).__name__})
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
