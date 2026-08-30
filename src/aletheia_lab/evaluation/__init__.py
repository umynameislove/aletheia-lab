"""Evaluation metrics and matched-pilot scoring."""

import importlib
from typing import TYPE_CHECKING, cast

from aletheia_lab.evaluation.execution_contracts import (
    ATTEMPT_IDENTITY_SCHEMA_VERSION,
    CASE_REFERENCE_SCHEMA_VERSION,
    EXECUTION_CANONICAL_SCHEMA_VERSION,
    MANIFEST_REFERENCE_SCHEMA_VERSION,
    MODEL_POLICY_REFERENCE_SCHEMA_VERSION,
    TECHNICAL_ISSUE_SCHEMA_VERSION,
    AttemptIdentity,
    EvaluationCaseReference,
    EvaluationContractError,
    EvaluationManifestReference,
    ModelPolicyReference,
    TechnicalIssue,
    canonical_execution_json,
    canonical_execution_sha256,
    validate_unique_case_references,
)
from aletheia_lab.evaluation.pilot import (
    evaluate_matched_pilot,
    write_evaluation_report,
)

if TYPE_CHECKING:
    from aletheia_lab.evaluation.attempt_store import (
        FAILURE_RECEIPT_SCHEMA_VERSION,
        STORE_ENTRY_SCHEMA_VERSION,
        STORE_RECEIPT_SCHEMA_VERSION,
        AttemptStoreConflictError,
        AttemptStoreError,
        AttemptStoreIntegrityError,
        AttemptStoreTransitionError,
        ImmutableAttemptStore,
        StoreClock,
        StoreLedgerEntry,
        StoreWriteReceipt,
        TechnicalFailureReceipt,
    )

_ATTEMPT_STORE_EXPORTS = frozenset(
    {
        "FAILURE_RECEIPT_SCHEMA_VERSION",
        "STORE_ENTRY_SCHEMA_VERSION",
        "STORE_RECEIPT_SCHEMA_VERSION",
        "AttemptStoreConflictError",
        "AttemptStoreError",
        "AttemptStoreIntegrityError",
        "AttemptStoreTransitionError",
        "ImmutableAttemptStore",
        "StoreClock",
        "StoreLedgerEntry",
        "StoreWriteReceipt",
        "TechnicalFailureReceipt",
    }
)


def __getattr__(name: str) -> object:
    """Load attempt-store exports lazily to avoid a model-gateway import cycle."""

    if name not in _ATTEMPT_STORE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module("aletheia_lab.evaluation.attempt_store")
    return cast(object, getattr(module, name))


__all__ = [
    "ATTEMPT_IDENTITY_SCHEMA_VERSION",
    "CASE_REFERENCE_SCHEMA_VERSION",
    "EXECUTION_CANONICAL_SCHEMA_VERSION",
    "MANIFEST_REFERENCE_SCHEMA_VERSION",
    "MODEL_POLICY_REFERENCE_SCHEMA_VERSION",
    "TECHNICAL_ISSUE_SCHEMA_VERSION",
    "AttemptIdentity",
    "AttemptStoreConflictError",
    "AttemptStoreError",
    "AttemptStoreIntegrityError",
    "AttemptStoreTransitionError",
    "EvaluationCaseReference",
    "EvaluationContractError",
    "EvaluationManifestReference",
    "FAILURE_RECEIPT_SCHEMA_VERSION",
    "ImmutableAttemptStore",
    "ModelPolicyReference",
    "STORE_ENTRY_SCHEMA_VERSION",
    "STORE_RECEIPT_SCHEMA_VERSION",
    "StoreClock",
    "StoreLedgerEntry",
    "StoreWriteReceipt",
    "TechnicalIssue",
    "TechnicalFailureReceipt",
    "canonical_execution_json",
    "canonical_execution_sha256",
    "evaluate_matched_pilot",
    "validate_unique_case_references",
    "write_evaluation_report",
]
