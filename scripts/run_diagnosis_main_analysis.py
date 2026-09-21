#!/usr/bin/env python3
"""Run the frozen diagnosis analysis over an exact sealed census and input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.diagnosis_main_analysis import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisError,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    analyse_diagnosis_main,
)

DEFAULT_PLAN = Path("configs/evaluation/diagnosis_main_analysis_plan.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional create-new report path; stdout is always emitted",
    )
    return parser


def _read(
    model: type[DiagnosisMainAnalysisPlan]
    | type[DiagnosisMainAnalysisCensus]
    | type[DiagnosisMainAnalysisInput],
    path: Path,
):  # type: ignore[no-untyped-def]
    if path.is_symlink() or not path.is_file():
        raise DiagnosisMainAnalysisError(f"required regular file is unavailable: {path}")
    return model.model_validate_json(path.read_bytes())


def main() -> int:
    args = _parser().parse_args()
    try:
        plan = _read(DiagnosisMainAnalysisPlan, args.plan)
        census = _read(DiagnosisMainAnalysisCensus, args.census)
        analysis_input = _read(DiagnosisMainAnalysisInput, args.input)
        report = analyse_diagnosis_main(plan, census, analysis_input)
        serialized = (
            json.dumps(report.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        )
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as handle:
                handle.write(serialized.encode("utf-8"))
        print(serialized, end="")
        return 0 if report.status == "valid_registered_analysis" else 2
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ValidationError,
        DiagnosisMainAnalysisError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "analysis_failed_closed",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
