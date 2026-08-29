"""Local byte-level reconciliation of the retired P2R v1.1 attempt."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.p2r_replication_failure import (
    load_intervention_feasibility_receipt,
    load_p2r_v1_1_replication_failure_audit,
    verify_p2r_v1_1_replication_failure_audit,
)


@pytest.mark.integration
def test_local_pinned_archives_and_terminal_store_reproduce_the_failure_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    required = (
        root / "data/raw/p2-v3/uci-default-of-credit-card-clients.zip",
        root / "data/raw/p2-v3/uci-online-shoppers-purchasing-intention.zip",
        root / "experiments/p2/outputs/p2r-confirmatory-v1-1/manifest.json",
    )
    if not all(path.is_file() for path in required):
        pytest.skip("large local P2R evidence is unavailable")

    completed = subprocess.run(
        (
            sys.executable,
            "scripts/p2r_v1_1_failure_audit.py",
            "readiness",
        ),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "p2r_v1_2_methodological_amendment_ready_to_design"
    assert result["root_cause_classification"] == (
        "registered_intervention_capacity_defect"
    )
    assert result["capacity_shortfall"] == 150
    assert result["replacement_feature"] == "OperatingSystems"
    assert result["predictive_outcomes_inspected_for_repair"] is False
    assert result["rerun_forbidden"] is True

    audit = verify_p2r_v1_1_replication_failure_audit(
        load_p2r_v1_1_replication_failure_audit(
            root
            / "configs/benchmark/provenance/p2r_v1_1_replication_failure_audit.json"
        ),
        root=root,
        feasibility=load_intervention_feasibility_receipt(
            root
            / "configs/benchmark/provenance/p2r_v1_1_intervention_feasibility.json"
        ),
    )
    assert audit.canonical_sha256() == result["audit_sha256"]
