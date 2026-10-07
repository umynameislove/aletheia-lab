"""Opt-in controlled HTTP cache/reload transfer with explicit audit services."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _archived_verify(root: Path, directory: Path) -> dict[str, Any]:
    """CLI child only: load the bound analysis rather than an editable package."""
    from aletheia_lab.evaluation.cache_lifecycle_study import FILES, check_bindings, read_sealed
    from aletheia_lab.project.identity import content_sha256

    plan = read_sealed(directory / "plan.json")
    check_bindings(root, plan)
    live = Path(__file__).resolve().parents[1]
    for relative in FILES:
        if relative.startswith(("scripts/", "configs/")) or "cache_lifecycle_" in relative:
            continue
        if content_sha256((live / relative).read_bytes()) != plan["bindings"][relative]:
            raise ValueError("shared replay helper differs; use its executed environment")
    for short in ("cache_lifecycle_analysis", "cache_lifecycle_study"):
        name = f"aletheia_lab.evaluation.{short}"
        spec = importlib.util.spec_from_file_location(
            name, root / "src/aletheia_lab/evaluation" / f"{short}.py"
        )
        if spec is None or spec.loader is None:
            raise ValueError("bound replay implementation unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    result: dict[str, Any] = sys.modules[name].verify(root, directory)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "run", "verify", "closeout", "worker", "adequacy", "verify-adequacy"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--native-python", type=Path)
    parser.add_argument("--native-site", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--prototype-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.action in {"adequacy", "verify-adequacy"}:
            from aletheia_lab.evaluation import cache_lifecycle_adequacy as extension

            if args.action == "adequacy":
                result = extension.run(
                    args.root.resolve(), args.study_dir.resolve(), args.prototype_dir
                )
            else:
                result = extension.verify(args.root.resolve(), args.study_dir.resolve())
        elif args.action == "worker":
            from aletheia_lab.evaluation.cache_lifecycle_source import run_cell

            if args.config is None:
                raise ValueError("owned cell configuration required")
            run_cell(json.loads(args.config.read_bytes()), args.study_dir.resolve())
            result = {"status": "cache_lifecycle_worker_complete"}
        else:
            from aletheia_lab.evaluation.cache_lifecycle_study import closeout, prepare, run

            if args.action in {"verify", "closeout"}:
                check = closeout if args.action == "closeout" else _archived_verify
                result = check(args.root.resolve(), args.study_dir.resolve())
            else:
                if args.native_python is None or args.native_site is None:
                    raise ValueError("explicit local native environment required")
                method = prepare if args.action == "prepare" else run
                result = method(
                    args.root.resolve(),
                    args.study_dir.resolve(),
                    args.native_python.absolute(),
                    args.native_site.resolve(),
                )
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {"status": "cache_lifecycle_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
