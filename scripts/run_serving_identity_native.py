"""Import qualification/preparation; worker execution requires the lead's seal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.serving_identity_native import (
    native_worker,
    prepare_fixtures,
    qualify_native,
)
from aletheia_lab.filesystem import write_new_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    qualify = commands.add_parser("qualify")
    qualify.add_argument("--source-root", type=Path, required=True)
    qualify.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--fixture-root", type=Path, required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--source-path", type=Path, required=True)
    worker.add_argument("--fixture-root", type=Path, required=True)
    worker.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "qualify":
        result = qualify_native(args.source_root)
        write_new_file(args.output, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    elif args.command == "prepare":
        result = prepare_fixtures(args.fixture_root)
    else:
        result = native_worker(args.source_path, args.fixture_root, args.output_dir)
    print(
        json.dumps(
            {
                "command": args.command,
                "schema": result["schema"],
                "request_count": len(result.get("requests", [])),
            }
        )
    )


if __name__ == "__main__":
    main()
