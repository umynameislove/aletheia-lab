"""Archive-readiness and immutable-failure regression contracts for P2R."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DatasetBindingAudit,
    V3DatasetBindingError,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    P2R_V1_TERMINAL_STORE_SHA256,
    P2RArchiveReadinessReceipt,
    P2RRecoveryError,
    P2RV1TechnicalFailureAudit,
    build_p2r_archive_readiness,
    load_archive_readiness,
    load_p2r_v1_failure_audit,
    verify_p2r_archive_readiness,
    write_archive_readiness_exclusive,
)

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT_PATH = ROOT / "scripts" / "p2r_confirmatory.py"
READINESS_SHA256 = "528e5d1d25f905c450faeafe6c35c87b7cc09f25f4f9fe77666f85da5c36403c"
FAILURE_AUDIT_SHA256 = "b5d8701cbc50f2eab32cfe0a1d880126907510778cb58edea2f1273397caec24"


def _entrypoint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p2r_confirmatory_entrypoint", ENTRYPOINT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("P2R entrypoint cannot be loaded from its repository path")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pinned_audits() -> dict[str, DatasetBindingAudit]:
    receipt = load_v3_dataset_binding_receipt()
    return {item.dataset_id: item for item in receipt.datasets}


def _patch_archive_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    audits = _pinned_audits()

    def inspect(**kwargs: object) -> DatasetBindingAudit:
        dataset = kwargs["dataset"]
        dataset_id = cast(object, dataset).__getattribute__("dataset_id")
        return audits[cast(str, dataset_id)]

    monkeypatch.setattr(
        "aletheia_lab.benchmark.p2.p2r_recovery.inspect_v3_dataset_archive",
        inspect,
    )


def test_readiness_reproduces_both_pinned_dataset_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_archive_inspection(monkeypatch)
    readiness = build_p2r_archive_readiness(
        manifest=load_v3_dataset_binding_manifest(),
        pinned_receipt=load_v3_dataset_binding_receipt(),
        archive_directory="ignored-by-test-double",
    )

    assert readiness.canonical_sha256() == READINESS_SHA256
    assert readiness.dataset_ids == (
        "uci_default_of_credit_card_clients",
        "uci_online_shoppers_purchasing_intention",
    )
    assert tuple(item.role for item in readiness.items) == (
        "primary",
        "external_replication",
    )
    assert readiness.all_pinned_archives_reproduced is True
    assert readiness.split_membership_compiled is False
    assert readiness.sealed_partition_opened is False
    assert readiness.model_fitted is False
    assert readiness.predictive_metrics_generated is False
    assert readiness.execution_attempt_consumed is False


def test_missing_archives_fail_before_a_readiness_receipt_exists(tmp_path: Path) -> None:
    readiness_path = tmp_path / "readiness.json"
    with pytest.raises(V3DatasetBindingError, match="cannot inspect"):
        build_p2r_archive_readiness(
            manifest=load_v3_dataset_binding_manifest(),
            pinned_receipt=load_v3_dataset_binding_receipt(),
            archive_directory=tmp_path / "missing",
        )
    assert not readiness_path.exists()


def test_cross_dataset_audit_reuse_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits = _pinned_audits()
    replication = audits["uci_online_shoppers_purchasing_intention"]
    monkeypatch.setattr(
        "aletheia_lab.benchmark.p2.p2r_recovery.inspect_v3_dataset_archive",
        lambda **_kwargs: replication,
    )
    with pytest.raises(P2RRecoveryError, match="does not reproduce"):
        build_p2r_archive_readiness(
            manifest=load_v3_dataset_binding_manifest(),
            pinned_receipt=load_v3_dataset_binding_receipt(),
            archive_directory="ignored-by-test-double",
        )


def test_readiness_is_idempotent_but_not_replaceable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_archive_inspection(monkeypatch)
    readiness = build_p2r_archive_readiness(
        manifest=load_v3_dataset_binding_manifest(),
        pinned_receipt=load_v3_dataset_binding_receipt(),
        archive_directory="ignored-by-test-double",
    )
    path = tmp_path / "readiness.json"
    write_archive_readiness_exclusive(path, readiness)
    write_archive_readiness_exclusive(path, readiness)
    assert load_archive_readiness(path) == readiness

    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(P2RRecoveryError, match="existing archive readiness"):
        write_archive_readiness_exclusive(path, readiness)


def test_readiness_loader_rejects_symlinked_evidence(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "readiness.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(P2RRecoveryError, match="unavailable or invalid"):
        load_archive_readiness(link)


def test_readiness_hash_or_current_archive_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_archive_inspection(monkeypatch)
    manifest = load_v3_dataset_binding_manifest()
    pinned = load_v3_dataset_binding_receipt()
    readiness = build_p2r_archive_readiness(
        manifest=manifest,
        pinned_receipt=pinned,
        archive_directory="ignored-by-test-double",
    )
    payload = readiness.model_dump()
    payload["receipt_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="receipt hash"):
        P2RArchiveReadinessReceipt.model_validate(payload)

    audits = _pinned_audits()
    monkeypatch.setattr(
        "aletheia_lab.benchmark.p2.p2r_recovery.inspect_v3_dataset_archive",
        lambda **_kwargs: audits["uci_online_shoppers_purchasing_intention"],
    )
    with pytest.raises(P2RRecoveryError, match="does not reproduce"):
        verify_p2r_archive_readiness(
            readiness,
            manifest=manifest,
            pinned_receipt=pinned,
            archive_directory="ignored-by-test-double",
        )


def test_tracked_failure_audit_binds_the_terminal_failure_identity() -> None:
    audit = load_p2r_v1_failure_audit()
    assert audit.canonical_sha256() == FAILURE_AUDIT_SHA256
    assert audit.terminal_store_sha256 == P2R_V1_TERMINAL_STORE_SHA256
    assert audit.failure_stage == "load_primary"
    assert audit.root_cause_classification == "preflight_archive_readiness_defect"
    assert audit.model_fitted is False
    assert audit.scientific_disposition_generated is False
    assert audit.rerun_forbidden is True

    payload = audit.model_dump()
    payload["exception_message_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="preimage"):
        P2RV1TechnicalFailureAudit.model_validate(payload)


def test_entrypoint_enforces_readiness_before_registration_and_marker() -> None:
    module = _entrypoint()
    preflight = inspect.getsource(module._preflight)  # type: ignore[attr-defined]
    execute = inspect.getsource(module._execute)  # type: ignore[attr-defined]

    assert preflight.index("build_p2r_archive_readiness") < preflight.index(
        "_write_registration_exclusive"
    )
    assert preflight.index("write_archive_readiness_exclusive") < preflight.index(
        "_write_registration_exclusive"
    )
    assert execute.index("verify_p2r_archive_readiness") < execute.index("_open_marker")
