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
        TERMINAL_INVENTORY_SCHEMA_VERSION,
        AttemptStoreConflictError,
        AttemptStoreError,
        AttemptStoreIntegrityError,
        AttemptStoreTransitionError,
        ImmutableAttemptStore,
        StoreClock,
        StoreLedgerEntry,
        StoreWriteReceipt,
        TechnicalFailureReceipt,
        TerminalExecutionInventory,
    )
    from aletheia_lab.evaluation.development_audit import (
        DEVELOPMENT_AUDIT_FINDING_SCHEMA_VERSION,
        DEVELOPMENT_AUDIT_SCHEMA_VERSION,
        DevelopmentAuditFinding,
        DevelopmentPilotAuditError,
        DevelopmentPilotAuditReceipt,
        audit_development_pilot,
        require_development_pilot_ready,
    )
    from aletheia_lab.evaluation.protocol_feasibility import (
        FEASIBILITY_PLAN_SCHEMA_VERSION,
        FEASIBILITY_RECEIPT_SCHEMA_VERSION,
        DiagnosisArtifactBinding,
        DiagnosisAttemptPolicy,
        DiagnosisCloseoutPolicy,
        DiagnosisFeasibilityCheck,
        DiagnosisFeasibilityError,
        DiagnosisFeasibilityReceipt,
        DiagnosisOutputPathPolicy,
        DiagnosisProtocolFeasibilityPlan,
        DiagnosisRuntimeCapability,
        audit_diagnosis_feasibility,
        load_diagnosis_feasibility_plan,
    )
    from aletheia_lab.evaluation.structural_closeout import (
        AUTHORIZATION_CHECK_SCHEMA_VERSION,
        REQUEST_EXPECTATION_SCHEMA_VERSION,
        STRUCTURAL_PLAN_SCHEMA_VERSION,
        STRUCTURAL_RECEIPT_SCHEMA_VERSION,
        StructuralAuthorizationCheck,
        StructuralCloseoutPlan,
        StructuralCloseoutReceipt,
        StructuralFinding,
        StructuralRequestExpectation,
        StructuralRequestReceipt,
        assert_no_scientific_closeout_fields,
        reduce_structural_closeout,
    )
    from aletheia_lab.evaluation.variant_fairness import (
        MATCHED_MODEL_VARIANTS,
        REQUIRED_VARIANTS,
        VARIANT_FREEZE_SCHEMA_VERSION,
        VARIANT_RECEIPT_SCHEMA_VERSION,
        DiagnosisEvidencePolicy,
        DiagnosisFairnessFinding,
        DiagnosisInformationBudget,
        DiagnosisModelPolicy,
        DiagnosisPromptPolicy,
        DiagnosisToolPolicy,
        DiagnosisVariantFairnessError,
        DiagnosisVariantFairnessFreeze,
        DiagnosisVariantFairnessReceipt,
        DiagnosisVariantSpec,
        audit_diagnosis_variant_fairness,
        load_diagnosis_variant_freeze,
    )

_ATTEMPT_STORE_EXPORTS = frozenset(
    {
        "FAILURE_RECEIPT_SCHEMA_VERSION",
        "STORE_ENTRY_SCHEMA_VERSION",
        "STORE_RECEIPT_SCHEMA_VERSION",
        "TERMINAL_INVENTORY_SCHEMA_VERSION",
        "AttemptStoreConflictError",
        "AttemptStoreError",
        "AttemptStoreIntegrityError",
        "AttemptStoreTransitionError",
        "ImmutableAttemptStore",
        "StoreClock",
        "StoreLedgerEntry",
        "StoreWriteReceipt",
        "TechnicalFailureReceipt",
        "TerminalExecutionInventory",
    }
)

_DEVELOPMENT_AUDIT_EXPORTS = frozenset(
    {
        "DEVELOPMENT_AUDIT_FINDING_SCHEMA_VERSION",
        "DEVELOPMENT_AUDIT_SCHEMA_VERSION",
        "DevelopmentAuditFinding",
        "DevelopmentPilotAuditError",
        "DevelopmentPilotAuditReceipt",
        "audit_development_pilot",
        "require_development_pilot_ready",
    }
)

_STRUCTURAL_CLOSEOUT_EXPORTS = frozenset(
    {
        "AUTHORIZATION_CHECK_SCHEMA_VERSION",
        "REQUEST_EXPECTATION_SCHEMA_VERSION",
        "STRUCTURAL_PLAN_SCHEMA_VERSION",
        "STRUCTURAL_RECEIPT_SCHEMA_VERSION",
        "StructuralAuthorizationCheck",
        "StructuralCloseoutPlan",
        "StructuralCloseoutReceipt",
        "StructuralFinding",
        "StructuralRequestExpectation",
        "StructuralRequestReceipt",
        "assert_no_scientific_closeout_fields",
        "reduce_structural_closeout",
    }
)

_PROTOCOL_FEASIBILITY_EXPORTS = frozenset(
    {
        "FEASIBILITY_PLAN_SCHEMA_VERSION",
        "FEASIBILITY_RECEIPT_SCHEMA_VERSION",
        "DiagnosisArtifactBinding",
        "DiagnosisAttemptPolicy",
        "DiagnosisCloseoutPolicy",
        "DiagnosisFeasibilityCheck",
        "DiagnosisFeasibilityError",
        "DiagnosisFeasibilityReceipt",
        "DiagnosisOutputPathPolicy",
        "DiagnosisProtocolFeasibilityPlan",
        "DiagnosisRuntimeCapability",
        "audit_diagnosis_feasibility",
        "load_diagnosis_feasibility_plan",
    }
)

_VARIANT_FAIRNESS_EXPORTS = frozenset(
    {
        "MATCHED_MODEL_VARIANTS",
        "REQUIRED_VARIANTS",
        "VARIANT_FREEZE_SCHEMA_VERSION",
        "VARIANT_RECEIPT_SCHEMA_VERSION",
        "DiagnosisEvidencePolicy",
        "DiagnosisFairnessFinding",
        "DiagnosisInformationBudget",
        "DiagnosisModelPolicy",
        "DiagnosisPromptPolicy",
        "DiagnosisToolPolicy",
        "DiagnosisVariantFairnessError",
        "DiagnosisVariantFairnessFreeze",
        "DiagnosisVariantFairnessReceipt",
        "DiagnosisVariantSpec",
        "audit_diagnosis_variant_fairness",
        "load_diagnosis_variant_freeze",
    }
)


def __getattr__(name: str) -> object:
    """Load attempt-store exports lazily to avoid a model-gateway import cycle."""

    if name in _ATTEMPT_STORE_EXPORTS:
        module_name = "aletheia_lab.evaluation.attempt_store"
    elif name in _DEVELOPMENT_AUDIT_EXPORTS:
        module_name = "aletheia_lab.evaluation.development_audit"
    elif name in _STRUCTURAL_CLOSEOUT_EXPORTS:
        module_name = "aletheia_lab.evaluation.structural_closeout"
    elif name in _PROTOCOL_FEASIBILITY_EXPORTS:
        module_name = "aletheia_lab.evaluation.protocol_feasibility"
    elif name in _VARIANT_FAIRNESS_EXPORTS:
        module_name = "aletheia_lab.evaluation.variant_fairness"
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    return cast(object, getattr(module, name))


__all__ = [
    "ATTEMPT_IDENTITY_SCHEMA_VERSION",
    "AUTHORIZATION_CHECK_SCHEMA_VERSION",
    "CASE_REFERENCE_SCHEMA_VERSION",
    "EXECUTION_CANONICAL_SCHEMA_VERSION",
    "MANIFEST_REFERENCE_SCHEMA_VERSION",
    "MODEL_POLICY_REFERENCE_SCHEMA_VERSION",
    "REQUEST_EXPECTATION_SCHEMA_VERSION",
    "STRUCTURAL_PLAN_SCHEMA_VERSION",
    "STRUCTURAL_RECEIPT_SCHEMA_VERSION",
    "TECHNICAL_ISSUE_SCHEMA_VERSION",
    "AttemptIdentity",
    "AttemptStoreConflictError",
    "AttemptStoreError",
    "AttemptStoreIntegrityError",
    "AttemptStoreTransitionError",
    "EvaluationCaseReference",
    "EvaluationContractError",
    "EvaluationManifestReference",
    "FEASIBILITY_PLAN_SCHEMA_VERSION",
    "FEASIBILITY_RECEIPT_SCHEMA_VERSION",
    "FAILURE_RECEIPT_SCHEMA_VERSION",
    "ImmutableAttemptStore",
    "ModelPolicyReference",
    "MATCHED_MODEL_VARIANTS",
    "DiagnosisArtifactBinding",
    "DEVELOPMENT_AUDIT_FINDING_SCHEMA_VERSION",
    "DEVELOPMENT_AUDIT_SCHEMA_VERSION",
    "DevelopmentAuditFinding",
    "DevelopmentPilotAuditError",
    "DevelopmentPilotAuditReceipt",
    "DiagnosisAttemptPolicy",
    "DiagnosisCloseoutPolicy",
    "DiagnosisEvidencePolicy",
    "DiagnosisFairnessFinding",
    "DiagnosisFeasibilityCheck",
    "DiagnosisFeasibilityError",
    "DiagnosisFeasibilityReceipt",
    "DiagnosisInformationBudget",
    "DiagnosisModelPolicy",
    "DiagnosisOutputPathPolicy",
    "DiagnosisPromptPolicy",
    "DiagnosisProtocolFeasibilityPlan",
    "DiagnosisRuntimeCapability",
    "DiagnosisToolPolicy",
    "DiagnosisVariantFairnessError",
    "DiagnosisVariantFairnessFreeze",
    "DiagnosisVariantFairnessReceipt",
    "DiagnosisVariantSpec",
    "REQUIRED_VARIANTS",
    "STORE_ENTRY_SCHEMA_VERSION",
    "STORE_RECEIPT_SCHEMA_VERSION",
    "TERMINAL_INVENTORY_SCHEMA_VERSION",
    "StoreClock",
    "StoreLedgerEntry",
    "StoreWriteReceipt",
    "StructuralAuthorizationCheck",
    "StructuralCloseoutPlan",
    "StructuralCloseoutReceipt",
    "StructuralFinding",
    "StructuralRequestExpectation",
    "StructuralRequestReceipt",
    "TechnicalIssue",
    "TechnicalFailureReceipt",
    "TerminalExecutionInventory",
    "VARIANT_FREEZE_SCHEMA_VERSION",
    "VARIANT_RECEIPT_SCHEMA_VERSION",
    "assert_no_scientific_closeout_fields",
    "audit_diagnosis_feasibility",
    "audit_diagnosis_variant_fairness",
    "audit_development_pilot",
    "canonical_execution_json",
    "canonical_execution_sha256",
    "evaluate_matched_pilot",
    "load_diagnosis_feasibility_plan",
    "load_diagnosis_variant_freeze",
    "reduce_structural_closeout",
    "require_development_pilot_ready",
    "validate_unique_case_references",
    "write_evaluation_report",
]
