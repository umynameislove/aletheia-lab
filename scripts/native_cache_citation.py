"""Replay cached citations or run a separately approved canonical-locator check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation import native_cache_citation_experiment as experiment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("replay", "prepare", "preflight", "execute", "verify"))
    parser.add_argument("--predecessor-dir", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path)
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()
    try:
        if args.command == "replay":
            result = experiment.replay(predecessor=args.predecessor_dir)
        else:
            if args.study_dir is None:
                raise ValueError("a separate study directory is required")
            kwargs = {"predecessor": args.predecessor_dir, "directory": args.study_dir}
            if args.command in {"prepare", "preflight", "verify"}:
                result = getattr(experiment, args.command)(**kwargs)
            else:
                if not args.confirm_plan_sha256:
                    raise ValueError("the current plan identity is required")
                plan, _ = experiment.checked_plan(**kwargs, confirm_sha256=args.confirm_plan_sha256)
                if (args.study_dir / "lease.json").exists() or (
                    args.study_dir / "results"
                ).exists():
                    raise ValueError("attempt exists; do not construct a network client")
                from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller

                caller = OpenAIDevelopmentCaller(maximum_calls=plan["maximum_provider_calls"])
                result = experiment.execute(
                    **kwargs,
                    confirm_sha256=args.confirm_plan_sha256,
                    caller=caller,
                    progress=lambda value: print(json.dumps(value), flush=True),
                )
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {"status": "native_cache_citation_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
