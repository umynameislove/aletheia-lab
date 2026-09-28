"""Prepare offline; execute paid development only with the displayed plan digest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.warrant_development_io import (
    analyze_private_development,
    checked_plan,
    compile_review_references,
    execute_warrant_development,
    prepare_warrant_development,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute", "finalize-references", "analyze"))
    parser.add_argument("--memory-root", type=Path, default=Path("../memory"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--references", type=Path)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    directory = args.directory or args.memory_root / "warrant-development-v1"
    if args.action == "prepare":
        report = prepare_warrant_development(memory_root=args.memory_root, output=directory)
    elif args.action == "execute":
        if not args.confirm_plan_sha256:
            parser.error("execute requires the freshly approved plan digest")
        plan, _ = checked_plan(directory, confirm_sha256=args.confirm_plan_sha256)
        if (directory / "lease.json").exists() or (directory / "results").exists():
            parser.error("paid attempt already exists; no automatic replay")
        from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller

        caller = OpenAIDevelopmentCaller(maximum_calls=plan["maximum_provider_calls"])
        report = execute_warrant_development(
            directory=directory,
            confirm_sha256=args.confirm_plan_sha256,
            caller=caller,
            progress=lambda value: print(json.dumps(value), flush=True),
        )
    elif args.action == "finalize-references":
        if args.review is None:
            parser.error("finalize-references requires --review")
        report = compile_review_references(directory=directory, reviewed_packet_path=args.review)
    else:
        report = analyze_private_development(directory=directory, references_path=args.references)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
