"""Run documented pytest profiles without weakening the default full suite."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[1]
_EVALUATION_TIMEOUT_SECONDS: Final = 300
_REPRODUCIBILITY_HASH_SEEDS: Final = ("1", "104729", "209759")
_PROFILE_ARGS: Final[dict[str, tuple[str, ...]]] = {
    "fast": (
        "-m",
        "not integration and not property and not research_regression and not large_artifact",
    ),
    "project": (
        "tests/unit",
        "tests/integration",
        "tests/property",
        "-k",
        "project",
    ),
    "research": ("-m", "research_regression"),
    "evaluation": (
        "tests/unit/test_evaluation_execution_contracts.py",
        "tests/property/test_evaluation_contract_properties.py",
        "tests/unit/test_context_evaluation_boundary.py",
        "tests/property/test_context_visibility_properties.py",
        "tests/integration/test_context_p3_projection.py",
        "tests/unit/test_model_gateway_runtime.py",
        "tests/unit/test_openai_gateway_adapter.py",
        "tests/integration/test_model_gateway_hash_seed.py",
        "tests/unit/test_evaluation_attempt_store.py",
        "tests/integration/test_evaluation_attempt_store_reproducibility.py",
        "tests/unit/test_evaluation_structural_closeout.py",
        "tests/integration/test_evaluation_structural_closeout_reproducibility.py",
        "tests/integration/test_evaluation_pipeline_reproducibility.py",
        "tests/integration/test_evaluation_readiness_flow.py",
        "tests/unit/test_diagnosis_protocol_feasibility.py",
        "tests/unit/test_diagnosis_variant_fairness.py",
        "tests/unit/test_diagnosis_variant_registry.py",
        "tests/property/test_diagnosis_freeze_properties.py",
        "tests/property/test_diagnosis_variant_registry_properties.py",
        "tests/unit/test_diagnosis_development_architecture.py",
        "tests/unit/test_diagnosis_development_runner.py",
        "tests/property/test_diagnosis_development_properties.py",
        "tests/integration/test_diagnosis_development_pilot_local.py",
        "tests/integration/test_diagnosis_evaluation_freeze_local.py",
        "tests/unit/test_leakage_guard.py",
        "tests/unit/test_project_bundle_contract.py",
        "tests/unit/test_evidence_contract_v2.py",
        "tests/unit/test_ci_quality_contract.py",
        "tests/unit/test_security_ci_contract.py",
        "tests/unit/test_test_runtime_contract.py",
        "tests/unit/test_evaluation_runtime_gates.py",
        "--durations=20",
    ),
    "full": (
        "--durations=20",
        "--cov=aletheia_lab",
        "--cov-report=term-missing",
        "--cov-fail-under=88",
    ),
}


def profile_command(profile: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Build the interpreter-stable command for a named test profile."""

    return (
        sys.executable,
        "-m",
        "pytest",
        *_PROFILE_ARGS[profile],
        *extra,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _hash_seed(run_index: int) -> str:
    if run_index < len(_REPRODUCIBILITY_HASH_SEEDS):
        return _REPRODUCIBILITY_HASH_SEEDS[run_index]
    return str(209759 + (run_index - 2) * 104729)


def run_profile(
    profile: str,
    extra: tuple[str, ...] = (),
    *,
    repeat: int = 1,
) -> int:
    """Run a profile, enforcing the evaluation budget and repeat seeds."""

    command = profile_command(profile, extra)
    timeout = _EVALUATION_TIMEOUT_SECONDS if profile == "evaluation" else None
    for run_index in range(repeat):
        environment = None
        if repeat > 1:
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = _hash_seed(run_index)
            print(
                f"evaluation run {run_index + 1}/{repeat} "
                f"with PYTHONHASHSEED={environment['PYTHONHASHSEED']}",
                flush=True,
            )
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=_ROOT,
                check=False,
                env=environment,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            print(
                f"{profile} profile exceeded its {timeout}-second runtime budget",
                file=sys.stderr,
            )
            return 124
        elapsed = time.monotonic() - started
        if repeat > 1:
            print(
                f"evaluation run {run_index + 1}/{repeat} completed in {elapsed:.3f}s",
                flush=True,
            )
        if completed.returncode != 0:
            return completed.returncode
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(_PROFILE_ARGS))
    parser.add_argument(
        "--show-command",
        action="store_true",
        help="print the resolved command without executing it",
    )
    parser.add_argument(
        "--repeat",
        type=_positive_int,
        default=1,
        help="repeat the evaluation profile with distinct process hash seeds",
    )
    return parser


def main() -> int:
    raw = sys.argv[1:]
    if "--" in raw:
        separator = raw.index("--")
        profile_args = raw[:separator]
        extra = tuple(raw[separator + 1 :])
    else:
        profile_args = raw
        extra = ()
    args = _parser().parse_args(profile_args)
    command = profile_command(args.profile, extra)
    if args.repeat != 1 and args.profile != "evaluation":
        _parser().error("--repeat is supported only by the evaluation profile")
    if args.show_command:
        print(json.dumps(command, ensure_ascii=True, separators=(",", ":")))
        return 0
    return run_profile(args.profile, extra, repeat=args.repeat)


if __name__ == "__main__":
    raise SystemExit(main())
