"""Cross-process reproducibility for Phase 2 canonical identity.

A family ID that depends on dictionary iteration order, locale or interpreter
start-up randomness would silently split one experimental unit into several. The
only way to demonstrate that it does not is to compute it in separate
interpreter processes started with different hash seeds.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = """
import json
import sys

from aletheia_lab.benchmark.p2 import (
    DataDriftParameters,
    FamilyIdentity,
    candidate_id_for,
    canonical_json,
    family_id_for,
    proposed_family_sha256,
)

identity = FamilyIdentity(
    dataset_snapshot_id="telco_customer_churn@2026-07",
    dataset_sha256="a" * 64,
    model_data_split_manifest_sha256="b" * 64,
    fault_type="data_drift",
    intervention_type="categorical_distribution_shift",
    canonical_intervention_parameters=DataDriftParameters(
        feature="Contract",
        target_distribution={
            "Two year": 0.08,
            "Month-to-month": 0.80,
            "One year": 0.12,
        },
        output_size=1409,
    ),
    seed=1,
    reference_construction_id="clean-test-reference/v1",
    injector_contract_version="categorical-drift/v1",
    model_specification_sha256="c" * 64,
    preprocessing_specification_sha256="d" * 64,
    identity_schema_version="p2-family-identity/v1",
)

fingerprint = proposed_family_sha256(identity)
payload = {
    "canonical": canonical_json(identity.identity_payload()),
    "fingerprint": fingerprint,
    "family_id": family_id_for(identity),
    "candidate_id": candidate_id_for(slot_id="M1-F1", family_fingerprint=fingerprint),
}
sys.stdout.write(json.dumps(payload, sort_keys=True))
"""


def _run_with_hash_seed(seed: str, repo_root: Path) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = str(repo_root / "src")
    completed = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        check=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    return completed.stdout


def test_identity_is_byte_identical_across_hash_seeds() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    first = _run_with_hash_seed("1", repo_root)
    second = _run_with_hash_seed("999", repo_root)
    assert first == second
    assert '"family_id": "p2-family-' in first


def test_identity_survives_a_third_independent_process() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    seeds = ("0", "12345", "65535")
    outputs = {_run_with_hash_seed(seed, repo_root) for seed in seeds}
    assert len(outputs) == 1
