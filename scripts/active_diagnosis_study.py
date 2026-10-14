"""Run new local actual-probe experiments; never old diagnosis/main studies."""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
from typing import Any, cast

import numpy as np
import sklearn  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.diagnosis import active_native, active_policy, active_replay, active_study
from aletheia_lab.diagnosis.active_replay import rebuild_saved_study
from aletheia_lab.diagnosis.study_artifacts import (
    checked_artifact_path,
    checked_output,
    read_artifact,
    write_artifact,
)
from aletheia_lab.project.identity import content_sha256


def code_bindings() -> dict[str, str]:
    return {
        name: file_sha256(Path(cast(str, module.__file__)))
        for name, module in (
            ("native", active_native),
            ("policy", active_policy),
            ("study", active_study),
        )
    } | {"entrypoint": file_sha256(Path(__file__))}


def checked_calibration(path: Path) -> dict[str, Any]:
    data, _ = read_artifact(path)
    if data.get("phase") != "calibrate" or data.get("design_sha256") != canonical_sha256(
        active_study.study_design()
    ):
        raise ValueError("calibration/design identity differs")
    if data.get("code_sha256") != code_bindings():
        raise ValueError("calibration code differs")
    return dict(data["calibration"])


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=("design", "calibrate", "development", "transfer", "analyze")
    )
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--expected-input-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    checked_output(args.output, repository)
    design = active_study.study_design()
    report: dict[str, Any] = {
        "phase": args.phase,
        "design_sha256": canonical_sha256(design),
        "code_sha256": code_bindings(),
        "historical_runs_executed": False,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "sklearn": sklearn.__version__,
        },
    }
    if args.phase == "design":
        report["design"] = design
        if args.calibration is not None:
            checked_calibration(args.calibration)
            report["calibration_sha256"] = file_sha256(args.calibration)
    elif args.phase == "calibrate":
        report["calibration"] = active_study.calibrate_costs()
    elif args.phase in {"development", "transfer"}:
        if args.calibration is None:
            parser.error("execution requires saved actual development cost calibration")
        calibration = checked_calibration(args.calibration)
        if args.phase == "transfer":
            if args.design is None:
                parser.error("transfer requires pre-outcome design/code binding")
            saved, _ = read_artifact(args.design)
            if (
                saved.get("phase") != "design"
                or saved.get("design_sha256") != report["design_sha256"]
                or saved.get("code_sha256") != report["code_sha256"]
                or saved.get("calibration_sha256") != file_sha256(args.calibration)
            ):
                parser.error("current design/code differs from pre-outcome record")
        report["calibration_sha256"] = file_sha256(args.calibration)
        report["study"] = active_study.run_study(args.phase, calibration)
    else:
        if args.input is None:
            parser.error("analysis requires a saved execution; analysis does not remeasure timers")
        source, raw = read_artifact(args.input)
        report["input_sha256"] = content_sha256(raw)
        if args.expected_input_sha256 != report["input_sha256"]:
            raise ValueError("analysis requires a trusted matching input byte pin")
        report["summary"] = rebuild_saved_study(source)
        if read_artifact(args.input)[1] != raw:
            raise ValueError("input changed during analysis")
        report["source_code_sha256"] = source["code_sha256"]
        report["replay_code_sha256"] = file_sha256(Path(active_replay.__file__))
        report["verification_scope"] = (
            "saved_census_proof_grade_consistency_not_host_attestation_or_remeasurement"
        )
    write_artifact(args.output, repository, report)
    print(
        json.dumps({"phase": args.phase, "sha256": file_sha256(checked_artifact_path(args.output))})
    )
    return 0


def main() -> int:
    try:
        return _main()
    except (OSError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        print(json.dumps({"status": "failed_closed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
