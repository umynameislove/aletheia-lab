"""Prepare, execute once, or verify a local qualified serving cost study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.serving_audit_cost_analysis import analyze, rebuild
from aletheia_lab.evaluation.serving_audit_cost_study import check_plan, execute, prepare
from aletheia_lab.evaluation.serving_audit_cost_workload import run_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run", "worker", "verify"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--qualified", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    if args.command == "prepare":
        if None in (args.root, args.qualified, args.design, args.output):
            parser.error("prepare requires root/qualified/design/output")
        plan = prepare(args.root, args.qualified, args.design, args.output)
        print(
            json.dumps(
                {
                    "status": "execution_seal_prepared",
                    "plan_sha256": plan["plan_sha256"],
                    "workers": len(plan["configs"]),
                    "native_calls_executed": 0,
                }
            )
        )
        return 0
    if args.plan is None:
        parser.error("plan required")
    plan = check_plan(args.plan)
    if args.command == "worker":
        config = next(row for row in plan["configs"] if row["worker_id"] == args.worker_id)
        result = run_worker(plan, config, args.output)
        print(json.dumps({"status": result["terminal"], "native_calls": result["native_calls"]}))
        return 0 if result["terminal"] == "complete" else 1
    if args.command == "run":
        execute(args.plan)
        result = analyze(args.plan.parent, plan)
    else:
        result = rebuild(args.plan.parent, plan)
        if result != json.loads((args.plan.parent / "analysis.json").read_bytes()):
            raise ValueError("read-only analysis differs")
    print(
        json.dumps(
            {
                "status": "complete" if args.command == "run" else "read_only_verification_pass",
                "analysis_sha256": result["analysis_sha256"],
                "service_census": result["service_census"],
                "known_native_calls": result["known_native_calls"],
                "forecasts": result["forecasts"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
