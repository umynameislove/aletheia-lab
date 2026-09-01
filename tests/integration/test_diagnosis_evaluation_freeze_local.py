"""Tracked diagnosis freezes must verify without opening protected outcomes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEASIBILITY_PLAN = ROOT / "configs/evaluation/diagnosis_protocol_feasibility_plan.json"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_feasibility_blockers() -> list[str]:
    """Compute blockers for either a source-only checkout or a local data checkout."""

    raw = json.loads(FEASIBILITY_PLAN.read_text(encoding="utf-8"))
    blockers: set[str] = set()
    for artifact in raw["artifacts"]:
        relative = Path(artifact["relative_path"])
        candidate = ROOT / relative
        current = ROOT
        has_symlink_component = False
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                has_symlink_component = True
                break
        present = candidate.is_file() and not has_symlink_component
        if artifact["role"] != "dataset":
            assert present, f"versioned repository artifact is unavailable: {relative}"
            assert _file_sha256(candidate) == artifact["expected_sha256"], (
                f"versioned repository artifact hash changed: {relative}"
            )
            continue
        if not present:
            blockers.update(
                {
                    f"artifact.{artifact['artifact_id']}.present",
                    f"artifact.{artifact['artifact_id']}.hash",
                }
            )
        elif _file_sha256(candidate) != artifact["expected_sha256"]:
            blockers.add(f"artifact.{artifact['artifact_id']}.hash")
    return sorted(blockers)


def _assert_reconciled_blockers(payload: dict[str, object]) -> None:
    feasibility = payload["feasibility"]
    fairness = payload["fairness"]
    assert isinstance(feasibility, dict)
    assert isinstance(fairness, dict)
    expected_feasibility = _expected_feasibility_blockers()
    expected_fairness: list[str] = []
    assert feasibility["blocker_codes"] == expected_feasibility
    assert fairness["blocker_codes"] == expected_fairness
    assert payload["blocker_count"] == len(expected_feasibility) + len(expected_fairness)


def test_versioned_diagnosis_freezes_are_outcome_blind_and_fail_closed() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/diagnosis_evaluation_freeze.py", "all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    expected_status = (
        "diagnosis_freezes_verified_with_execution_blockers"
        if _expected_feasibility_blockers()
        else "diagnosis_freezes_verified_ready_for_registration"
    )
    assert payload["status"] == expected_status
    assert payload["protected_outcomes_opened"] is False
    assert payload["execution_authorized"] is False
    _assert_reconciled_blockers(payload)


def test_require_ready_exit_code_matches_local_environment_readiness() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/diagnosis_evaluation_freeze.py", "all", "--require-ready"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == (2 if _expected_feasibility_blockers() else 0)
    payload = json.loads(completed.stdout)
    _assert_reconciled_blockers(payload)
