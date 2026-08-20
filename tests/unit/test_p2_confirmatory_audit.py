"""Fail-closed tests for the outcome-aware confirmatory root-cause audit."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_audit import (
    BankRootCauseAudit,
    audit_bank_replication,
)
from aletheia_lab.benchmark.p2.confirmatory_execution import labelled_targets_sha256
from aletheia_lab.benchmark.p2.confirmatory_protocol import load_confirmatory_protocol
from aletheia_lab.benchmark.p2.confirmatory_registered import (
    DatasetOutcome,
    RegisteredDataset,
    RegisteredDatasetReceipt,
    execute_registered_dataset,
)
from aletheia_lab.benchmark.p2.confirmatory_runtime import feature_frame_sha256

_STORE_SHA256 = "0" * 64
_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def external_registered() -> RegisteredDataset:
    protocol = load_confirmatory_protocol()
    dataset = next(item for item in protocol.datasets if item.role == "external_replication")
    size = 120
    record_ids = tuple(f"external-audit-{index:03d}" for index in range(size))
    targets = tuple(index % 2 for index in range(size))
    features = pd.DataFrame(
        {
            "signal": [
                float(target * 2 + (index % 5) / 20) for index, target in enumerate(targets)
            ],
            "auxiliary": [float(index % 7) for index in range(size)],
            "category": ["a" if index % 3 else "b" for index in range(size)],
        }
    )
    receipt = RegisteredDatasetReceipt(
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        snapshot_sha256=dataset.snapshot_sha256,
        archive_sha256=dataset.archive_sha256,
        source_path_name="external-audit.csv",
        row_count=size,
        feature_columns=tuple(features.columns),
        excluded_features=dataset.excluded_features,
        target_column=dataset.target_column,
        positive_label=dataset.positive_label,
        negative_label="no",
        record_membership_sha256=canonical_sha256(
            {
                "schema_version": "p2-confirmatory-registered-dataset/1",
                "record_ids": sorted(record_ids),
            }
        ),
        feature_matrix_sha256=feature_frame_sha256(features, record_ids),
        target_artifact_sha256=labelled_targets_sha256(record_ids, targets),
    )
    return RegisteredDataset(
        binding=dataset,
        receipt=receipt,
        record_ids=record_ids,
        targets=targets,
        features=features,
    )


@pytest.fixture(scope="module")
def external_outcome(external_registered: RegisteredDataset) -> DatasetOutcome:
    return execute_registered_dataset(
        protocol=load_confirmatory_protocol(),
        registered=external_registered,
    )


@pytest.fixture()
def structural_audit(
    external_registered: RegisteredDataset,
    external_outcome: DatasetOutcome,
) -> Iterator[BankRootCauseAudit]:
    yield audit_bank_replication(
        protocol=load_confirmatory_protocol(),
        registered=external_registered,
        outcome=external_outcome,
        result_store_sha256=_STORE_SHA256,
        verify_convergence=False,
    )


def test_structural_audit_reconciles_frozen_contracts_without_overclaiming(
    structural_audit: BankRootCauseAudit,
) -> None:
    assert structural_audit.disposition == "inconclusive"
    assert not structural_audit.implementation_defects
    assert structural_audit.split_reproduced_exactly
    assert structural_audit.encoding.numeric_mapping == {"no": 0, "yes": 1}
    assert structural_audit.encoding.yes_to_no_mapping == (1, 0)
    assert structural_audit.encoding.no_to_yes_mapping == (0, 1)
    assert structural_audit.encoding.labels_match_registered_bank_contract
    assert structural_audit.encoding.all_mutations_reproduced_from_frozen_specs
    assert structural_audit.features.duration_declared_excluded
    assert structural_audit.features.duration_absent_from_model_frame
    assert all(item.seed_census_matches_protocol for item in structural_audit.mutations)
    assert all(item.every_seed_reproduced_exactly for item in structural_audit.mutations)
    assert all(len(item.seeds) == 30 for item in structural_audit.mutations)
    assert all(
        seed.reproduced_exactly
        for mutation in structural_audit.mutations
        for seed in mutation.seeds
    )
    assert structural_audit.inference.exact_match
    assert structural_audit.inference.bootstrap_resamples == 10_000
    assert structural_audit.inference.sign_flip_resamples == 100_000
    assert structural_audit.inference.multiplicity_method == "holm_two_co_primary_directions"
    assert tuple(item.direction for item in structural_audit.inference.directions) == (
        "yes_to_no",
        "no_to_yes",
    )
    assert not structural_audit.convergence.performed
    assert structural_audit.registered_decision_unchanged


def test_full_refit_reproduces_every_prediction_without_convergence_warnings(
    external_registered: RegisteredDataset,
    external_outcome: DatasetOutcome,
) -> None:
    audit = audit_bank_replication(
        protocol=load_confirmatory_protocol(),
        registered=external_registered,
        outcome=external_outcome,
        result_store_sha256=_STORE_SHA256,
        verify_convergence=True,
    )

    assert audit.convergence.performed
    assert audit.convergence.requested_refits == 181
    assert audit.convergence.exact_prediction_vector_matches == 181
    assert audit.convergence.convergence_warning_count == 0
    assert not audit.convergence.mismatched_cells_and_seeds
    assert "prediction_or_convergence_reproduction_failure" not in (audit.implementation_defects)


def test_inference_mismatch_is_classified_as_an_implementation_defect(
    monkeypatch: pytest.MonkeyPatch,
    external_registered: RegisteredDataset,
    external_outcome: DatasetOutcome,
) -> None:
    tampered_analysis = external_outcome.analysis.model_copy(
        update={"dataset_id": "tampered-analysis"}
    )
    monkeypatch.setattr(
        "aletheia_lab.benchmark.p2.confirmatory_audit.analyze_dataset",
        lambda **_: tampered_analysis,
    )

    audit = audit_bank_replication(
        protocol=load_confirmatory_protocol(),
        registered=external_registered,
        outcome=external_outcome,
        result_store_sha256=_STORE_SHA256,
        verify_convergence=False,
    )

    assert not audit.inference.exact_match
    assert audit.implementation_defects == ("inference_reproduction_mismatch",)
    assert audit.disposition == "implementation_defect_detected"


def test_audit_schema_rejects_a_non_defect_disposition_when_defects_exist(
    structural_audit: BankRootCauseAudit,
) -> None:
    payload = structural_audit.model_dump()
    payload["implementation_defects"] = ("concrete defect evidence",)
    payload["disposition"] = "inconclusive"

    with pytest.raises(ValidationError, match="implementation defect must dominate"):
        BankRootCauseAudit.model_validate(payload)


def test_audit_schema_cannot_claim_prior_shift_without_full_refits(
    structural_audit: BankRootCauseAudit,
) -> None:
    payload = structural_audit.model_dump()
    payload["disposition"] = "temporal_prior_shift_supported"

    with pytest.raises(ValidationError, match="requires every validity gate"):
        BankRootCauseAudit.model_validate(payload)


def test_published_summary_preserves_the_failed_replication_boundary() -> None:
    summary = json.loads(
        (
            _ROOT / "configs/benchmark/provenance/"
            "p2_label_noise_confirmatory_v2_bank_root_cause_summary.json"
        ).read_text(encoding="utf-8")
    )

    assert summary["result_store_sha256"] == (
        "7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c"
    )
    assert summary["registered_decision_unchanged"] is True
    assert summary["disposition"] == "temporal_prior_shift_supported"
    assert summary["implementation_defects"] == []
    assert summary["validity_checks"]["all_180_mutations_reproduced_exactly"] is True
    assert summary["validity_checks"]["all_181_prediction_vectors_reproduced_exactly"] is True
    assert (
        summary["validity_checks"]["stored_analysis_sha256"]
        == (summary["validity_checks"]["reproduced_analysis_sha256"])
    )
    assert summary["registered_inference"]["yes_to_no"]["direction_pass"] is False
    assert summary["registered_inference"]["no_to_yes"]["direction_pass"] is False
    assert "cross-dataset claim" in summary["interpretation_boundary"]
