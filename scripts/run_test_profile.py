"""Run documented pytest profiles without weakening the default full suite."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[1]
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(_PROFILE_ARGS))
    parser.add_argument(
        "--show-command",
        action="store_true",
        help="print the resolved command without executing it",
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
    if args.show_command:
        print(" ".join(command))
        return 0
    return subprocess.run(command, cwd=_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
