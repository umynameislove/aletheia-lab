"""Fail-closed diagnosis evaluation variant census and information-path fairness freeze."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

VARIANT_FREEZE_SCHEMA_VERSION: Final = "diagnosis-variant-fairness-freeze/v1"
VARIANT_RECEIPT_SCHEMA_VERSION: Final = "diagnosis-variant-fairness-receipt/v1"
REQUIRED_VARIANTS: Final = (
    "A1",
    "A2",
    "A3",
    "B0",
    "B1",
    "B2",
    "B3",
    "CodeGraph",
    "FULL",
)
MATCHED_MODEL_VARIANTS: Final = ("A1", "A2", "A3", "B1", "B2")
_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"


class DiagnosisVariantFairnessError(ValueError):
    """Raised when a diagnosis evaluation variant freeze is incomplete or internally unfair."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DiagnosisModelPolicy(_StrictFrozenModel):
    provider: str
    model: str
    model_version: str
    temperature: float = Field(ge=0.0, le=2.0, allow_inf_nan=False)
    top_p: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)
    seed: int
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0.0, allow_inf_nan=False)
    provider_attempt_ceiling: int = Field(ge=1, le=3)
    silent_provider_or_model_switch: Literal[False]


class DiagnosisInformationBudget(_StrictFrozenModel):
    observable_corpus_ref: str
    maximum_context_tokens: int = Field(gt=0)
    maximum_retrieved_items: int = Field(ge=0)
    maximum_turns: int = Field(ge=1)
    diagnosis_question_ref: str
    hidden_ground_truth_visible: Literal[False]
    evaluator_metadata_visible: Literal[False]


class DiagnosisToolPolicy(_StrictFrozenModel):
    retrieval: bool
    code_graph: bool
    web: Literal[False]
    shell: Literal[False]
    project_execution: Literal[False]
    tool_ledger_required: bool


class DiagnosisEvidencePolicy(_StrictFrozenModel):
    structure: Literal["plain", "structured", "native_external"]
    citation_required: bool
    abstention_required: bool
    provenance_required: bool
    lineage_visible: bool
    conversation_audit_visible: bool


class DiagnosisPromptPolicy(_StrictFrozenModel):
    prompt_version: str
    instruction_contract: str
    response_schema_ref: str
    prompt_content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _prompt_hash_reconciles(self) -> Self:
        payload = {
            "prompt_version": self.prompt_version,
            "instruction_contract": self.instruction_contract,
            "response_schema_ref": self.response_schema_ref,
        }
        if self.prompt_content_sha256 != canonical_execution_sha256(payload):
            raise ValueError("prompt_content_sha256 does not match prompt policy")
        return self


class DiagnosisVariantSpec(_StrictFrozenModel):
    variant_id: Literal["B0", "B1", "B2", "B3", "A1", "A2", "A3", "FULL", "CodeGraph"]
    role: str
    comparison_class: Literal[
        "matched_main",
        "deterministic_reference",
        "external_transfer",
        "system_configuration",
        "retrieval_component",
    ]
    implementation_state: Literal["ready", "pending"]
    implementation_reference: str | None
    model_policy_ref: str | None
    information_budget_ref: str
    tool_policy_ref: str
    evidence_policy_ref: str
    prompt_policy_ref: str
    fallback_policy: Literal["forbidden"]
    pooling_policy: Literal["matched_primary", "separate", "external_only"]
    effect_attribution: str

    @model_validator(mode="after")
    def _implementation_reference_matches_state(self) -> Self:
        if self.implementation_state == "ready" and self.implementation_reference is None:
            raise ValueError("ready variant requires an implementation reference")
        if self.implementation_reference is not None:
            parts = self.implementation_reference.split(":")
            if len(parts) != 2 or not all(parts):
                raise ValueError("implementation reference must use module:attribute form")
        if not self.role.strip() or not self.effect_attribution.strip():
            raise ValueError("variant role and effect attribution must be explicit")
        return self


class DiagnosisVariantFairnessFreeze(_StrictFrozenModel):
    schema_version: Literal["diagnosis-variant-fairness-freeze/v1"]
    freeze_status: Literal["outcome_blind_policy_freeze"]
    protected_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    registered_execution_attempts: Literal[1]
    model_policies: dict[str, DiagnosisModelPolicy]
    information_budgets: dict[str, DiagnosisInformationBudget]
    tool_policies: dict[str, DiagnosisToolPolicy]
    evidence_policies: dict[str, DiagnosisEvidencePolicy]
    prompt_policies: dict[str, DiagnosisPromptPolicy]
    variants: tuple[DiagnosisVariantSpec, ...]
    cross_variant_rules: tuple[str, ...]

    @model_validator(mode="after")
    def _complete_and_referentially_closed(self) -> Self:
        identifiers = tuple(item.variant_id for item in self.variants)
        if identifiers != REQUIRED_VARIANTS:
            raise ValueError("variant census must contain the canonical nine variants")
        for variant in self.variants:
            if (
                variant.model_policy_ref is not None
                and variant.model_policy_ref not in self.model_policies
            ):
                raise ValueError(f"variant {variant.variant_id} has unresolved model reference")
            if variant.information_budget_ref not in self.information_budgets:
                raise ValueError(
                    f"variant {variant.variant_id} has unresolved information reference"
                )
            if variant.tool_policy_ref not in self.tool_policies:
                raise ValueError(f"variant {variant.variant_id} has unresolved tool reference")
            if variant.evidence_policy_ref not in self.evidence_policies:
                raise ValueError(f"variant {variant.variant_id} has unresolved evidence reference")
            if variant.prompt_policy_ref not in self.prompt_policies:
                raise ValueError(f"variant {variant.variant_id} has unresolved prompt reference")
        referenced_models = {
            item.model_policy_ref for item in self.variants if item.model_policy_ref is not None
        }
        referenced_information = {item.information_budget_ref for item in self.variants}
        referenced_tools = {item.tool_policy_ref for item in self.variants}
        referenced_evidence = {item.evidence_policy_ref for item in self.variants}
        referenced_prompts = {item.prompt_policy_ref for item in self.variants}
        if referenced_models != set(self.model_policies):
            raise ValueError("model policies must be exactly reference-closed")
        if referenced_information != set(self.information_budgets):
            raise ValueError("information budgets must be exactly reference-closed")
        if referenced_tools != set(self.tool_policies):
            raise ValueError("tool policies must be exactly reference-closed")
        if referenced_evidence != set(self.evidence_policies):
            raise ValueError("evidence policies must be exactly reference-closed")
        if referenced_prompts != set(self.prompt_policies):
            raise ValueError("prompt policies must be exactly reference-closed")

        expected_classes = {
            "A1": ("matched_main", "matched_primary"),
            "A2": ("matched_main", "matched_primary"),
            "A3": ("matched_main", "matched_primary"),
            "B0": ("deterministic_reference", "separate"),
            "B1": ("matched_main", "matched_primary"),
            "B2": ("matched_main", "matched_primary"),
            "B3": ("external_transfer", "external_only"),
            "CodeGraph": ("retrieval_component", "separate"),
            "FULL": ("system_configuration", "separate"),
        }
        for variant in self.variants:
            expected_class, expected_pool = expected_classes[variant.variant_id]
            if (
                variant.comparison_class != expected_class
                or variant.pooling_policy != expected_pool
            ):
                raise ValueError(f"variant {variant.variant_id} has an invalid comparison class")
            expects_model = variant.variant_id not in {"B0", "B3"}
            if expects_model != (variant.model_policy_ref is not None):
                raise ValueError(
                    f"variant {variant.variant_id} has an invalid model-policy binding"
                )
        if len(self.cross_variant_rules) < 6 or len(set(self.cross_variant_rules)) != len(
            self.cross_variant_rules
        ):
            raise ValueError("cross-variant fairness rules are incomplete or duplicated")
        return self


class DiagnosisFairnessFinding(_StrictFrozenModel):
    code: str
    status: Literal["pass", "block", "separate"]
    variants: tuple[str, ...]
    evidence: str


class DiagnosisVariantFairnessReceipt(_StrictFrozenModel):
    schema_version: Literal["diagnosis-variant-fairness-receipt/v1"]
    freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal[
        "fairness_policy_frozen_execution_blocked",
        "fairness_policy_frozen_ready_for_registration",
    ]
    protected_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    variant_ids: tuple[str, ...]
    findings: tuple[DiagnosisFairnessFinding, ...]
    blocker_codes: tuple[str, ...]
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _status_and_hash_reconcile(self) -> Self:
        blockers = tuple(item.code for item in self.findings if item.status == "block")
        if blockers != self.blocker_codes:
            raise ValueError("variant fairness blocker census does not reconcile")
        expected_status = (
            "fairness_policy_frozen_execution_blocked"
            if blockers
            else "fairness_policy_frozen_ready_for_registration"
        )
        if self.status != expected_status:
            raise ValueError("variant fairness status is not derived")
        if self.variant_ids != REQUIRED_VARIANTS:
            raise ValueError("variant fairness receipt lost the canonical census")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_execution_sha256(payload):
            raise ValueError("variant fairness receipt hash does not reconcile")
        return self


def _finding(
    code: str,
    status: Literal["pass", "block", "separate"],
    variants: tuple[str, ...],
    evidence: str,
) -> DiagnosisFairnessFinding:
    return DiagnosisFairnessFinding(
        code=code,
        status=status,
        variants=variants,
        evidence=evidence,
    )


def load_diagnosis_variant_freeze(path: str | Path) -> DiagnosisVariantFairnessFreeze:
    """Load the strict diagnosis evaluation policy freeze without executing a model."""

    raw_text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise DiagnosisVariantFairnessError(
            "diagnosis evaluation variant freeze must be a JSON object"
        )
    return DiagnosisVariantFairnessFreeze.model_validate_json(raw_text)


def audit_diagnosis_variant_fairness(
    freeze: DiagnosisVariantFairnessFreeze,
) -> DiagnosisVariantFairnessReceipt:
    """Reconcile completeness, matched budgets and non-comparable paths."""

    by_id = {item.variant_id: item for item in freeze.variants}
    matched = tuple(by_id[item] for item in MATCHED_MODEL_VARIANTS)
    findings: list[DiagnosisFairnessFinding] = []
    findings.append(
        _finding(
            "variant_census_complete",
            "pass",
            REQUIRED_VARIANTS,
            "all nine prespecified variants are represented exactly once",
        )
    )

    model_values = tuple(
        canonical_execution_sha256(
            freeze.model_policies[item.model_policy_ref].model_dump(mode="json")
        )
        if item.model_policy_ref is not None
        else "none"
        for item in matched
    )
    findings.append(
        _finding(
            "matched_model_policy",
            "pass" if len(set(model_values)) == 1 else "block",
            MATCHED_MODEL_VARIANTS,
            "B1/B2 and A1/A2/A3 must share the exact model and generation policy",
        )
    )

    information_values = tuple(
        canonical_execution_sha256(
            freeze.information_budgets[item.information_budget_ref].model_dump(mode="json")
        )
        for item in matched
    )
    findings.append(
        _finding(
            "matched_information_budget",
            "pass" if len(set(information_values)) == 1 else "block",
            MATCHED_MODEL_VARIANTS,
            "observable corpus, context tokens, turns and hidden-field visibility are matched",
        )
    )

    no_fallbacks = all(item.fallback_policy == "forbidden" for item in freeze.variants)
    findings.append(
        _finding(
            "fallbacks_forbidden",
            "pass" if no_fallbacks else "block",
            REQUIRED_VARIANTS,
            "no variant may silently change provider, model, prompt, retrieval or tool path",
        )
    )

    provider_safe = all(
        not freeze.tool_policies[item.tool_policy_ref].web
        and not freeze.tool_policies[item.tool_policy_ref].shell
        and not freeze.tool_policies[item.tool_policy_ref].project_execution
        for item in freeze.variants
    )
    findings.append(
        _finding(
            "provider_features_bounded",
            "pass" if provider_safe else "block",
            REQUIRED_VARIANTS,
            "web, shell and project execution are forbidden for every path",
        )
    )

    pending = tuple(
        item.variant_id for item in freeze.variants if item.implementation_state != "ready"
    )
    findings.append(
        _finding(
            "implementation_artifacts_resolve",
            "block" if pending else "pass",
            pending or REQUIRED_VARIANTS,
            (
                "pending implementations: " + ",".join(pending)
                if pending
                else "every implementation reference is present"
            ),
        )
    )

    findings.extend(
        (
            _finding(
                "b0_non_llm_reference",
                "separate",
                ("B0",),
                "B0 has no model budget and is not pooled as a matched LLM estimate",
            ),
            _finding(
                "b3_external_transfer",
                "separate",
                ("B3",),
                "B3 preserves native cases and metrics and is never pooled into the main benchmark",
            ),
            _finding(
                "full_system_path",
                "separate",
                ("FULL",),
                "FULL may expose provenance/lineage and is reported as a system configuration",
            ),
            _finding(
                "codegraph_component_path",
                "separate",
                ("CodeGraph",),
                "CodeGraph is a retrieval/index component baseline, not evidence for universal component superiority",
            ),
        )
    )

    canonical_findings = tuple(sorted(findings, key=lambda item: item.code))
    blockers = tuple(item.code for item in canonical_findings if item.status == "block")
    status: Literal[
        "fairness_policy_frozen_execution_blocked",
        "fairness_policy_frozen_ready_for_registration",
    ] = (
        "fairness_policy_frozen_execution_blocked"
        if blockers
        else "fairness_policy_frozen_ready_for_registration"
    )
    hash_payload = {
        "schema_version": VARIANT_RECEIPT_SCHEMA_VERSION,
        "freeze_sha256": canonical_execution_sha256(freeze.model_dump(mode="json")),
        "status": status,
        "protected_outcomes_opened": False,
        "execution_authorized": False,
        "variant_ids": list(REQUIRED_VARIANTS),
        "findings": [item.model_dump(mode="json") for item in canonical_findings],
        "blocker_codes": list(blockers),
    }
    return DiagnosisVariantFairnessReceipt(
        schema_version=VARIANT_RECEIPT_SCHEMA_VERSION,
        freeze_sha256=canonical_execution_sha256(freeze.model_dump(mode="json")),
        status=status,
        protected_outcomes_opened=False,
        execution_authorized=False,
        variant_ids=REQUIRED_VARIANTS,
        findings=canonical_findings,
        blocker_codes=blockers,
        receipt_sha256=canonical_execution_sha256(hash_payload),
    )
