"""Validate and identify the frozen label-noise confirmatory protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    DEFAULT_CONFIRMATORY_PROTOCOL_PATH,
    load_confirmatory_protocol,
    verify_confirmatory_predecessor,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_CONFIRMATORY_PROTOCOL_PATH)
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def main() -> int:
    args = _parser().parse_args()
    protocol = load_confirmatory_protocol(args.protocol)
    verify_confirmatory_predecessor(protocol, root=args.root)
    summary = {
        "schema_version": protocol.schema_version,
        "status": protocol.status,
        "protocol_sha256": protocol.canonical_sha256(),
        "primary_dataset": protocol.datasets[0].dataset_id,
        "external_replication": protocol.datasets[1].dataset_id,
        "intervention_cell_count": len(protocol.intervention_cells),
        "replicates_per_cell": protocol.inference.replicate_count_per_cell,
        "outcomes_generated": False,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
