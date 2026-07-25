"""Regression tests for cross-platform handling of frozen repository artifacts."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN_REPORTS = ROOT / "reports" / "p1"


def _git_attribute(attribute: str, relative_path: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-attr", attribute, "--", relative_path],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.rstrip("\n").rsplit(": ", maxsplit=1)[-1]


def test_frozen_json_artifacts_are_exempt_from_eol_conversion() -> None:
    relative_path = "reports/p1/p1-machine-result.json"

    assert _git_attribute("text", relative_path) == "unset"
    assert _git_attribute("eol", relative_path) == "unspecified"


def test_frozen_json_artifacts_are_checked_out_without_crlf() -> None:
    artifacts = sorted(FROZEN_REPORTS.rglob("*.json"))
    assert artifacts

    for artifact in artifacts:
        assert b"\r\n" not in artifact.read_bytes(), artifact.relative_to(ROOT)
