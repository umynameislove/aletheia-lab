"""Execute a bounded new method audit without touching registered P2 stores."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from aletheia_lab.benchmark.p2 import ground_truth_study, ground_truth_validity
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.ground_truth_study import (
    run_source,
    sampling_checks,
    study_design,
    summarize,
)
from aletheia_lab.filesystem import write_new_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("design", "sampling", "development", "transfer"))
    parser.add_argument("--expected-design-sha256")
    parser.add_argument("--seal", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    design = study_design()
    digest = canonical_sha256(design)
    if args.phase == "transfer" and args.expected_design_sha256 != digest:
        parser.error("transfer requires the previously recorded design SHA-256")
    if args.output is not None and (args.output.exists() or args.output.is_symlink()):
        parser.error("output already exists; preserve the earlier attempt")
    sources = {
        "study": Path(ground_truth_study.__file__),
        "validity": Path(ground_truth_validity.__file__),
        "entrypoint": Path(__file__),
    }
    report = {
        "design_sha256": digest,
        "phase": args.phase,
        "code_sha256": {
            key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in sources.items()
        },
        "historical_registered_runs_executed": False,
    }
    if args.phase == "transfer":
        if args.seal is None:
            parser.error("transfer requires the saved design record with code digests")
        sealed = json.loads(args.seal.read_bytes())
        if (
            sealed.get("phase") != "design"
            or sealed.get("design_sha256") != digest
            or sealed.get("code_sha256") != report["code_sha256"]
        ):
            parser.error("current design/code differs from saved pre-transfer record")
    if args.phase == "design":
        report["design"] = design
    elif args.phase == "sampling":
        report["sampling"] = sampling_checks()
    else:
        results = [run_source(source, phase=args.phase) for source in design[args.phase]]
        report["sources"] = results
        report["summary"] = summarize(results)
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    if args.output is not None:
        write_new_file(args.output, payload)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "phase": args.phase,
                }
            )
        )
    else:
        print(payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
