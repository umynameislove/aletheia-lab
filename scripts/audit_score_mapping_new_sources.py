"""Audit frozen M5 source archives and split membership without model fitting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.score_mapping_new_source_protocol import (
    NewSourceProtocolError,
    audit_new_source_protocol,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--verify", action="store_true", help="compare with the existing private inventory"
    )
    args = parser.parse_args()
    try:
        inventory = audit_new_source_protocol(root=args.root, sources=args.sources)
        output = args.output.absolute()
        if (
            output.is_symlink()
            or output.parent.is_symlink()
            or not output.parent.is_dir()
            or output.resolve().is_relative_to(args.root.resolve())
        ):
            raise NewSourceProtocolError("inventory must remain outside the repository")
        serialized = json.dumps(inventory, sort_keys=True, indent=2) + "\n"
        if args.verify:
            if not output.is_file() or output.read_text(encoding="utf-8") != serialized:
                raise NewSourceProtocolError("private inventory differs from current source audit")
        elif output.exists():
            raise NewSourceProtocolError("inventory output must be a new private file")
        else:
            output.write_text(serialized, encoding="utf-8")
    except (NewSourceProtocolError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__}))
        return 1
    print(
        json.dumps(
            {
                "status": inventory["status"],
                "protocol_sha256": inventory["protocol_sha256"],
                "source_cluster_count": inventory["source_cluster_count"],
                "cell_count": inventory["cell_count"],
                "model_fitted": inventory["model_fitted"],
                "final_predictions_or_metrics_computed": inventory[
                    "final_predictions_or_metrics_computed"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
