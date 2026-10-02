"""Replay scoped native reads offline, without API calls or protected studies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.source_evidence_acquisition import replay_acquisition
from aletheia_lab.filesystem import write_new_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.output is not None:
            output = args.output.absolute()
            if (
                any(item.is_symlink() for item in (output, *output.parents))
                or not output.parent.is_dir()
                or output.exists()
                or output.parent.resolve() != args.memory_root.resolve()
                or output.resolve().is_relative_to(args.root.resolve())
            ):
                raise ValueError("output must be a new file directly in private memory")
        report = replay_acquisition(root=args.root, memory_root=args.memory_root)
        if args.output is not None:
            write_new_file(
                args.output, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
            )
    except (OSError, ValueError, UnicodeError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {"status": "source_acquisition_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    private = {"rows", "source_sha256_before", "source_sha256_after", "executed_module_sha256"}
    print(json.dumps({key: value for key, value in report.items() if key not in private}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
