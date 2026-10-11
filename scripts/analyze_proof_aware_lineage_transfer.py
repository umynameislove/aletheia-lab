"""Read-only diagnostics of verified transfer results; no paid execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.proof_aware_lineage_diagnostics import replay_diagnostics
from aletheia_lab.evaluation.warrant_development_io import _private_dir, _write


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--pilot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.output is not None:
            _private_dir(args.output.parent)
            if args.output.resolve().is_relative_to(args.pilot_dir.resolve()):
                raise ValueError("diagnostics cannot enter the immutable source pilot")
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("diagnostic output already exists")
        report = replay_diagnostics(
            root=args.root.resolve(), memory_root=args.memory_root, directory=args.pilot_dir
        )
        if args.output is not None:
            _write(args.output, report)
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {"status": "lineage_diagnostics_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
