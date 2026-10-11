"""Audit already-exposed loader sources offline; never call a provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.source_evidence_headroom import audit_development_sources
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
            if any(item.is_symlink() for item in (output, *output.parents)):
                raise ValueError("output must not traverse a symlink")
            if not output.parent.is_dir() or output.exists():
                raise ValueError("output creation state is invalid")
            if output.resolve().is_relative_to(args.root.resolve()) or (
                output.parent.resolve() != args.memory_root.resolve()
            ):
                raise ValueError("output must be a new file directly in the private memory root")
        report = audit_development_sources(root=args.root, memory_root=args.memory_root)
        if args.output is not None:
            write_new_file(
                args.output, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
            )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {"status": "source_evidence_audit_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "summary",
                    "source_cluster_count",
                    "producer_family_count",
                    "native_runtime_event_count",
                    "aggregate_path_record_count",
                    "provider_calls",
                    "sources_unchanged",
                    "semantic_llm_trial_ready",
                )
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
