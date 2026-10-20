"""Inspect a prospective design; this command cannot run a serving study."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path

from aletheia_lab.evaluation.serving_contract_design import inspect_design


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, required=True)
    args = parser.parse_args()
    try:
        info = args.design.stat()
        if args.design.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
            raise ValueError("bounded regular design file required")
        plan = json.loads(args.design.read_bytes())
        if not isinstance(plan, dict):
            raise ValueError("design object required")
        report = inspect_design(plan)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"status": "design_rejected", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
