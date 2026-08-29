"""Evaluation metrics and matched-pilot scoring."""

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

__all__ = [
    "ATTEMPT_IDENTITY_SCHEMA_VERSION",
    "CASE_REFERENCE_SCHEMA_VERSION",
    "EXECUTION_CANONICAL_SCHEMA_VERSION",
    "MANIFEST_REFERENCE_SCHEMA_VERSION",
    "MODEL_POLICY_REFERENCE_SCHEMA_VERSION",
    "TECHNICAL_ISSUE_SCHEMA_VERSION",
    "AttemptIdentity",
    "EvaluationCaseReference",
    "EvaluationContractError",
    "EvaluationManifestReference",
    "ModelPolicyReference",
    "TechnicalIssue",
    "canonical_execution_json",
    "canonical_execution_sha256",
    "evaluate_matched_pilot",
    "validate_unique_case_references",
    "write_evaluation_report",
]
