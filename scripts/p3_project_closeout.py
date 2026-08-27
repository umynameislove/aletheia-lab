"""Reconcile and print one persisted P3 import-to-lineage generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.project import ProjectStore, build_project_closeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store", type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--before-bundle-id", required=True)
    parser.add_argument("--after-bundle-id", required=True)
    parser.add_argument("--before-snapshot-id", required=True)
    parser.add_argument("--after-snapshot-id", required=True)
    parser.add_argument("--comparison-id", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--evidence-bundle-id", required=True)
    parser.add_argument("--lineage-graph-id", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.store.is_dir() or not (args.store / "project-store.sqlite3").is_file():
        raise SystemExit("project store does not exist")
    with ProjectStore(args.store) as store:
        receipt = build_project_closeout(
            store,
            project_id=args.project_id,
            before_bundle_id=args.before_bundle_id,
            after_bundle_id=args.after_bundle_id,
            before_snapshot_id=args.before_snapshot_id,
            after_snapshot_id=args.after_snapshot_id,
            comparison_id=args.comparison_id,
            event_id=args.event_id,
            evidence_bundle_id=args.evidence_bundle_id,
            lineage_graph_id=args.lineage_graph_id,
        )
    print(json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
