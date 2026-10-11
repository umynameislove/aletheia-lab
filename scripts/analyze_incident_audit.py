"""Read-only native receipt reconstruction and same-service development analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.incident_audit_analysis import analyze
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.study_dir.resolve())
        if args.output is not None:
            output = args.output.resolve()
            if output.is_relative_to(Path(__file__).resolve().parents[1]):
                raise ValueError("private aggregate output outside repository required")
            write_new_file(output, encode(result).encode())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "incident_audit_analysis_failed_closed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "status": "incident_audit_development_analyzed",
                "sha256": result["sha256"],
                "source_arms": {
                    name: data.get("same_service_envelope", data)
                    for name, data in result["arms"].items()
                },
                "A_disposition": result["A_disposition"],
                "B_disposition": result["B_disposition"],
                "provider_calls": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
