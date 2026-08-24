"""Tests for immutable v3.2 failure preservation and bounded diagnosis."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_2_failure import (
    V3_2_TERMINAL_STORE_SHA256,
    V32TechnicalFailureAudit,
    load_v3_2_failure_audit,
    verify_v3_2_failure_audit,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError


def test_tracked_failure_audit_is_strict_and_outcome_bounded() -> None:
    audit = load_v3_2_failure_audit()
    assert audit.terminal_store_sha256 == V3_2_TERMINAL_STORE_SHA256
    assert audit.failure_stage == "build_closeout"
    assert audit.exception_class == "ValidationError"
    assert audit.root_cause_classification == "implementation_contract_defect"
    assert audit.outcome_artifacts_available is False
    assert audit.scientific_disposition_generated is False
    assert audit.rerun_forbidden is True
    assert audit.causal_attribution == "high_confidence_not_exception_preimage_verified"


def test_failure_audit_rejects_rebinding_to_another_terminal_store() -> None:
    payload = load_v3_2_failure_audit().model_dump()
    payload["terminal_store_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="another terminal store"):
        V32TechnicalFailureAudit.model_validate(payload)


def test_local_terminal_store_reconciles_with_tracked_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    store = root / "experiments/p2/outputs/label-noise-shift-factorial-v3.2"
    if not store.exists():
        pytest.skip("ignored immutable v3.2 terminal store is unavailable")
    assert verify_v3_2_failure_audit(load_v3_2_failure_audit(), root=root)


def test_failure_audit_detects_terminal_file_substitution(tmp_path: Path) -> None:
    store = tmp_path / "terminal-store"
    store.mkdir()
    (store / "store-manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(V3RuntimeError, match="cannot load"):
        verify_v3_2_failure_audit(
            load_v3_2_failure_audit(),
            root=tmp_path,
            terminal_store_path=store,
        )
