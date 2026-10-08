"""Source-qualified narrow protocol comparison, with exclusive execution."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.serving_protocol_transfer import (
    check_transfer_plan,
    check_transfer_result,
    execute_transfer,
    prepare_transfer,
    summarize_transfer,
)
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    for name in ("root", "reserved", "dependencies", "design", "output", "plan"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        if None in (args.root, args.reserved, args.dependencies, args.design, args.output):
            parser.error("prepare requires root/reserved/dependencies/design/output")
        plan = prepare_transfer(
            args.root, args.reserved, args.dependencies, args.design, args.output
        )
        print(
            json.dumps(
                {
                    "status": "protocol_execution_seal_prepared",
                    "plan_sha256": plan["plan_sha256"],
                    "planned_conditions": plan["planned_conditions"],
                    "outcomes_executed": False,
                }
            )
        )
        return 0
    if args.plan is None:
        parser.error("plan required")
    plan = check_transfer_plan(args.plan)
    output = args.plan.parent / "results.json"
    if args.command == "run":
        write_new_file(
            args.plan.parent / "execution-started.json",
            encode({"plan_sha256": plan["plan_sha256"]}).encode(),
        )
        rows = asyncio.run(execute_transfer(plan))
        check_transfer_plan(args.plan)
        result = {
            "plan_sha256": plan["plan_sha256"],
            "rows": rows,
            "analysis": summarize_transfer(rows),
        }
        result["results_sha256"] = content_sha256(encode(result).encode())
        check_transfer_result(result, plan)
        write_new_file(output, encode(result).encode())
    else:
        result = json.loads(output.read_bytes())
        check_transfer_result(result, plan)
    print(
        json.dumps(
            {
                "status": "protocol_transfer_complete"
                if args.command == "run"
                else "read_only_protocol_consistency_pass",
                "results_sha256": result["results_sha256"],
                "analysis": result["analysis"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
