"""Resolved, fail-closed implementations for the frozen diagnosis variants.

This module is deliberately execution-free.  It binds each variant to its
registered information, tool, evidence, prompt and model policies, then emits
an immutable request binding for the development runner to consume later.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
from pathlib import Path
from typing import Annotated, Final, Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import (
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
    DiagnosisVariantSpec,
    load_diagnosis_variant_freeze,
)
from aletheia_lab.project.identity import SHA256_PATTERN

VARIANT_KERNEL_SCHEMA_VERSION: Final = "diagnosis-variant-kernel/v1"
VARIANT_REGISTRY_SCHEMA_VERSION: Final = "diagnosis-variant-registry/v1"
VARIANT_BINDING_SCHEMA_VERSION: Final = "diagnosis-variant-request-binding/v1"

VariantId = Literal["A1", "A2", "A3", "B0", "B1", "B2", "B3", "CodeGraph", "FULL"]
VariantStrategy = Literal[
    "deterministic_rules",
    "single_turn_llm",
    "multi_turn_rag",
    "native_external",
    "codegraph_retrieval",
    "full_system",
]
EvidenceStructure = Literal["plain", "structured", "native_external"]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

_EXPECTED_STRATEGIES: Final[dict[str, VariantStrategy]] = {
    "A1": "single_turn_llm",
    "A2": "single_turn_llm",
    "A3": "single_turn_llm",
    "B0": "deterministic_rules",
    "B1": "single_turn_llm",
    "B2": "multi_turn_rag",
    "B3": "native_external",
    "CodeGraph": "codegraph_retrieval",
    "FULL": "full_system",
}


class VariantRegistryError(ValueError):
    """Raised when an implementation or request binding diverges from the freeze."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class VariantCapabilities(_StrictFrozenModel):
    """Declared behavior that must exactly equal the referenced frozen policies."""

    uses_model: bool
    structure: EvidenceStructure
    retrieval: bool
    code_graph: bool
    web: Literal[False] = False
    shell: Literal[False] = False
    project_execution: Literal[False] = False
    tool_ledger_required: bool
    citation_required: bool
    abstention_required: bool
    provenance_required: bool
    lineage_visible: bool
    conversation_audit_visible: bool


class DiagnosisVariantKernel(_StrictFrozenModel):
    """One concrete variant strategy independent of any case or outcome."""

    schema_version: Literal["diagnosis-variant-kernel/v1"] = (
        VARIANT_KERNEL_SCHEMA_VERSION
    )
    variant_id: VariantId
    implementation_version: str = Field(pattern=r"^diagnosis-[a-z0-9-]+/[1-9][0-9]*$")
    strategy: VariantStrategy
    response_schema_ref: str
    capabilities: VariantCapabilities

    @model_validator(mode="after")
    def _strategy_matches_variant(self) -> Self:
        if self.strategy != _EXPECTED_STRATEGIES[self.variant_id]:
            raise ValueError("variant strategy differs from its registered implementation")
        if not self.response_schema_ref.strip():
            raise ValueError("variant response schema reference must be explicit")
        return self


class VariantImplementationFactory(Protocol):
    def __call__(self) -> DiagnosisVariantKernel: ...


class ResolvedDiagnosisVariant(_StrictFrozenModel):
    """A kernel reconciled against every referenced frozen policy."""

    variant_id: VariantId
    implementation_reference: str
    implementation_version: str
    implementation_source_sha256: Sha256
    strategy: VariantStrategy
    comparison_class: str
    pooling_policy: str
    model_policy_ref: str | None
    model_policy_sha256: Sha256 | None
    information_budget_ref: str
    information_budget_sha256: Sha256
    tool_policy_ref: str
    tool_policy_sha256: Sha256
    evidence_policy_ref: str
    evidence_policy_sha256: Sha256
    prompt_policy_ref: str
    prompt_policy_sha256: Sha256
    capabilities: VariantCapabilities
    variant_content_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"variant_content_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.variant_content_sha256 != canonical_execution_sha256(
            self.identity_payload()
        ):
            raise ValueError("variant_content_sha256 does not match resolved policies")
        return self


class DiagnosisVariantRegistry(_StrictFrozenModel):
    """Canonical complete registry; missing, duplicate and aliased variants fail closed."""

    schema_version: Literal["diagnosis-variant-registry/v1"] = (
        VARIANT_REGISTRY_SCHEMA_VERSION
    )
    freeze_sha256: Sha256
    variants: tuple[ResolvedDiagnosisVariant, ...]
    registry_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "freeze_sha256": self.freeze_sha256,
            "variants": [item.model_dump(mode="json") for item in self.variants],
        }

    @model_validator(mode="after")
    def _census_and_identity_reconcile(self) -> Self:
        identifiers = tuple(item.variant_id for item in self.variants)
        if identifiers != REQUIRED_VARIANTS:
            raise ValueError("resolved registry lost the canonical nine-variant census")
        references = tuple(item.implementation_reference for item in self.variants)
        if len(references) != len(set(references)):
            raise ValueError("variant implementation references must be unique")
        content_hashes = tuple(item.variant_content_sha256 for item in self.variants)
        if len(content_hashes) != len(set(content_hashes)):
            raise ValueError("resolved variant content hashes must be unique")
        if self.registry_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("registry_sha256 does not match resolved registry content")
        return self

    def require(self, variant_id: VariantId) -> ResolvedDiagnosisVariant:
        for variant in self.variants:
            if variant.variant_id == variant_id:
                return variant
        raise VariantRegistryError("requested variant is absent from the canonical registry")


class DiagnosisVariantRequestBinding(_StrictFrozenModel):
    """Case-independent policy envelope carried by every future provider request."""

    schema_version: Literal["diagnosis-variant-request-binding/v1"] = (
        VARIANT_BINDING_SCHEMA_VERSION
    )
    registry_sha256: Sha256
    variant_id: VariantId
    variant_content_sha256: Sha256
    implementation_version: str
    implementation_source_sha256: Sha256
    model_policy_sha256: Sha256 | None
    information_budget_sha256: Sha256
    tool_policy_sha256: Sha256
    evidence_policy_sha256: Sha256
    prompt_policy_sha256: Sha256
    context_sha256: Sha256
    evidence_content_sha256: Sha256
    tool_ledger_sha256: Sha256 | None
    binding_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"binding_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.binding_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("binding_sha256 does not match variant request policy")
        return self


def _capabilities(
    *,
    uses_model: bool,
    structure: EvidenceStructure,
    retrieval: bool = False,
    code_graph: bool = False,
    tool_ledger_required: bool = False,
    citation_required: bool = False,
    abstention_required: bool = False,
    provenance_required: bool = False,
    lineage_visible: bool = False,
    conversation_audit_visible: bool = False,
) -> VariantCapabilities:
    return VariantCapabilities(
        uses_model=uses_model,
        structure=structure,
        retrieval=retrieval,
        code_graph=code_graph,
        tool_ledger_required=tool_ledger_required,
        citation_required=citation_required,
        abstention_required=abstention_required,
        provenance_required=provenance_required,
        lineage_visible=lineage_visible,
        conversation_audit_visible=conversation_audit_visible,
    )


def build_a1_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="A1",
        implementation_version="diagnosis-a1/1",
        strategy="single_turn_llm",
        response_schema_ref="diagnosis-output/2",
        capabilities=_capabilities(uses_model=True, structure="structured"),
    )


def build_a2_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="A2",
        implementation_version="diagnosis-a2/1",
        strategy="single_turn_llm",
        response_schema_ref="diagnosis-output/2",
        capabilities=_capabilities(
            uses_model=True, structure="structured", citation_required=True
        ),
    )


def build_a3_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="A3",
        implementation_version="diagnosis-a3/1",
        strategy="single_turn_llm",
        response_schema_ref="diagnosis-output/2",
        capabilities=_capabilities(
            uses_model=True,
            structure="structured",
            citation_required=True,
            abstention_required=True,
        ),
    )


def build_b0_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="B0",
        implementation_version="diagnosis-b0/1",
        strategy="deterministic_rules",
        response_schema_ref="deterministic-diagnosis/1",
        capabilities=_capabilities(uses_model=False, structure="plain"),
    )


def build_b1_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="B1",
        implementation_version="diagnosis-b1/1",
        strategy="single_turn_llm",
        response_schema_ref="diagnosis-output/2",
        capabilities=_capabilities(uses_model=True, structure="plain"),
    )


def build_b2_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="B2",
        implementation_version="diagnosis-b2/1",
        strategy="multi_turn_rag",
        response_schema_ref="diagnosis-output/2",
        capabilities=_capabilities(
            uses_model=True,
            structure="plain",
            retrieval=True,
            tool_ledger_required=True,
        ),
    )


def build_b3_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="B3",
        implementation_version="diagnosis-b3/1",
        strategy="native_external",
        response_schema_ref="logdx-native-output/1",
        capabilities=_capabilities(
            uses_model=False,
            structure="native_external",
            provenance_required=True,
            tool_ledger_required=True,
        ),
    )


def build_codegraph_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="CodeGraph",
        implementation_version="diagnosis-codegraph/1",
        strategy="codegraph_retrieval",
        response_schema_ref="diagnosis-output/2",
        capabilities=_capabilities(
            uses_model=True,
            structure="structured",
            retrieval=True,
            code_graph=True,
            tool_ledger_required=True,
            citation_required=True,
        ),
    )


def build_full_variant() -> DiagnosisVariantKernel:
    return DiagnosisVariantKernel(
        variant_id="FULL",
        implementation_version="diagnosis-full/1",
        strategy="full_system",
        response_schema_ref="diagnosis-output/2",
        capabilities=_capabilities(
            uses_model=True,
            structure="structured",
            retrieval=True,
            code_graph=True,
            tool_ledger_required=True,
            citation_required=True,
            abstention_required=True,
            provenance_required=True,
            lineage_visible=True,
            conversation_audit_visible=True,
        ),
    )


def _factory(reference: str) -> tuple[VariantImplementationFactory, str]:
    if not reference.startswith("aletheia_lab.") or reference.count(":") != 1:
        raise VariantRegistryError("implementation reference is outside the package boundary")
    module_name, attribute = reference.split(":")
    try:
        module = importlib.import_module(module_name)
        candidate = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise VariantRegistryError("implementation reference does not resolve") from exc
    if not callable(candidate):
        raise VariantRegistryError("implementation reference is not callable")
    signature = inspect.signature(candidate)
    if any(
        parameter.default is inspect.Parameter.empty
        and parameter.kind
        not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        for parameter in signature.parameters.values()
    ):
        raise VariantRegistryError("implementation factory must require no arguments")
    source_file = inspect.getsourcefile(candidate)
    if source_file is None:
        raise VariantRegistryError("implementation source cannot be content-addressed")
    source_path = Path(source_file).resolve()
    package_root = Path(__file__).resolve().parents[1]
    if not source_path.is_relative_to(package_root) or not source_path.is_file():
        raise VariantRegistryError("implementation source is outside aletheia_lab")
    return cast(VariantImplementationFactory, candidate), hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()


def _policy_sha256(policy: BaseModel) -> str:
    return canonical_execution_sha256(policy.model_dump(mode="json"))


def _resolve_variant(
    freeze: DiagnosisVariantFairnessFreeze,
    spec: DiagnosisVariantSpec,
) -> ResolvedDiagnosisVariant:
    if spec.implementation_state != "ready" or spec.implementation_reference is None:
        raise VariantRegistryError("variant implementation is not marked ready")
    factory, source_sha256 = _factory(spec.implementation_reference)
    try:
        kernel = DiagnosisVariantKernel.model_validate(factory().model_dump(mode="python"))
    except Exception as exc:
        raise VariantRegistryError("variant implementation factory returned invalid data") from exc
    if kernel.variant_id != spec.variant_id:
        raise VariantRegistryError("implementation factory returned the wrong variant")

    tool = freeze.tool_policies[spec.tool_policy_ref]
    evidence = freeze.evidence_policies[spec.evidence_policy_ref]
    prompt = freeze.prompt_policies[spec.prompt_policy_ref]
    expected_capabilities = VariantCapabilities(
        uses_model=spec.model_policy_ref is not None,
        structure=evidence.structure,
        retrieval=tool.retrieval,
        code_graph=tool.code_graph,
        web=tool.web,
        shell=tool.shell,
        project_execution=tool.project_execution,
        tool_ledger_required=tool.tool_ledger_required,
        citation_required=evidence.citation_required,
        abstention_required=evidence.abstention_required,
        provenance_required=evidence.provenance_required,
        lineage_visible=evidence.lineage_visible,
        conversation_audit_visible=evidence.conversation_audit_visible,
    )
    if kernel.capabilities != expected_capabilities:
        raise VariantRegistryError("implementation capabilities differ from frozen policies")
    if kernel.implementation_version != prompt.prompt_version:
        raise VariantRegistryError("implementation version differs from prompt policy")
    if kernel.response_schema_ref != prompt.response_schema_ref:
        raise VariantRegistryError("implementation response schema differs from prompt policy")

    model_sha = (
        _policy_sha256(freeze.model_policies[spec.model_policy_ref])
        if spec.model_policy_ref is not None
        else None
    )
    payload = {
        "variant_id": spec.variant_id,
        "implementation_reference": spec.implementation_reference,
        "implementation_version": kernel.implementation_version,
        "implementation_source_sha256": source_sha256,
        "strategy": kernel.strategy,
        "comparison_class": spec.comparison_class,
        "pooling_policy": spec.pooling_policy,
        "model_policy_ref": spec.model_policy_ref,
        "model_policy_sha256": model_sha,
        "information_budget_ref": spec.information_budget_ref,
        "information_budget_sha256": _policy_sha256(
            freeze.information_budgets[spec.information_budget_ref]
        ),
        "tool_policy_ref": spec.tool_policy_ref,
        "tool_policy_sha256": _policy_sha256(tool),
        "evidence_policy_ref": spec.evidence_policy_ref,
        "evidence_policy_sha256": _policy_sha256(evidence),
        "prompt_policy_ref": spec.prompt_policy_ref,
        "prompt_policy_sha256": _policy_sha256(prompt),
        "capabilities": kernel.capabilities.model_dump(mode="json"),
    }
    return ResolvedDiagnosisVariant.model_validate(
        {
            **payload,
            "variant_content_sha256": canonical_execution_sha256(payload),
        }
    )


def build_variant_registry(
    freeze: DiagnosisVariantFairnessFreeze,
) -> DiagnosisVariantRegistry:
    """Resolve all factories and prove exact policy/capability agreement."""

    checked = DiagnosisVariantFairnessFreeze.model_validate(
        freeze.model_dump(mode="python")
    )
    variants = tuple(_resolve_variant(checked, item) for item in checked.variants)
    freeze_sha256 = canonical_execution_sha256(checked.model_dump(mode="json"))
    payload: dict[str, object] = {
        "schema_version": VARIANT_REGISTRY_SCHEMA_VERSION,
        "freeze_sha256": freeze_sha256,
        "variants": [item.model_dump(mode="json") for item in variants],
    }
    return DiagnosisVariantRegistry(
        freeze_sha256=freeze_sha256,
        variants=variants,
        registry_sha256=canonical_execution_sha256(payload),
    )


def load_variant_registry(path: str | Path) -> DiagnosisVariantRegistry:
    return build_variant_registry(load_diagnosis_variant_freeze(path))


def bind_variant_request(
    registry: DiagnosisVariantRegistry,
    *,
    variant_id: VariantId,
    context_sha256: str,
    evidence_content_sha256: str,
    tool_ledger_sha256: str | None = None,
) -> DiagnosisVariantRequestBinding:
    """Bind future request content to the resolved implementation and budgets."""

    checked = DiagnosisVariantRegistry.model_validate(registry.model_dump(mode="python"))
    variant = checked.require(variant_id)
    ledger_required = variant.capabilities.tool_ledger_required
    if ledger_required != (tool_ledger_sha256 is not None):
        raise VariantRegistryError(
            "tool ledger presence differs from the frozen variant capability"
        )
    payload = {
        "schema_version": VARIANT_BINDING_SCHEMA_VERSION,
        "registry_sha256": checked.registry_sha256,
        "variant_id": variant.variant_id,
        "variant_content_sha256": variant.variant_content_sha256,
        "implementation_version": variant.implementation_version,
        "implementation_source_sha256": variant.implementation_source_sha256,
        "model_policy_sha256": variant.model_policy_sha256,
        "information_budget_sha256": variant.information_budget_sha256,
        "tool_policy_sha256": variant.tool_policy_sha256,
        "evidence_policy_sha256": variant.evidence_policy_sha256,
        "prompt_policy_sha256": variant.prompt_policy_sha256,
        "context_sha256": context_sha256,
        "evidence_content_sha256": evidence_content_sha256,
        "tool_ledger_sha256": tool_ledger_sha256,
    }
    binding = DiagnosisVariantRequestBinding.model_validate(
        {
            **payload,
            "binding_sha256": canonical_execution_sha256(payload),
        }
    )
    return validate_variant_request_binding(
        checked,
        binding,
        context_sha256=context_sha256,
        evidence_content_sha256=evidence_content_sha256,
        tool_ledger_sha256=tool_ledger_sha256,
    )


def validate_variant_request_binding(
    registry: DiagnosisVariantRegistry,
    binding: DiagnosisVariantRequestBinding,
    *,
    context_sha256: str,
    evidence_content_sha256: str,
    tool_ledger_sha256: str | None,
) -> DiagnosisVariantRequestBinding:
    """Reconcile a serialized request envelope with the authoritative registry."""

    checked_registry = DiagnosisVariantRegistry.model_validate(
        registry.model_dump(mode="python")
    )
    checked_binding = DiagnosisVariantRequestBinding.model_validate(
        binding.model_dump(mode="python")
    )
    variant = checked_registry.require(checked_binding.variant_id)
    expected = {
        "registry_sha256": checked_registry.registry_sha256,
        "variant_content_sha256": variant.variant_content_sha256,
        "implementation_version": variant.implementation_version,
        "implementation_source_sha256": variant.implementation_source_sha256,
        "model_policy_sha256": variant.model_policy_sha256,
        "information_budget_sha256": variant.information_budget_sha256,
        "tool_policy_sha256": variant.tool_policy_sha256,
        "evidence_policy_sha256": variant.evidence_policy_sha256,
        "prompt_policy_sha256": variant.prompt_policy_sha256,
        "context_sha256": context_sha256,
        "evidence_content_sha256": evidence_content_sha256,
        "tool_ledger_sha256": tool_ledger_sha256,
    }
    actual = {
        field: getattr(checked_binding, field) for field in expected
    }
    if actual != expected:
        raise VariantRegistryError(
            "variant request binding differs from authoritative registry or content"
        )
    if variant.capabilities.tool_ledger_required != (tool_ledger_sha256 is not None):
        raise VariantRegistryError(
            "tool ledger presence differs from the frozen variant capability"
        )
    return checked_binding


__all__ = [
    "DiagnosisVariantKernel",
    "DiagnosisVariantRegistry",
    "DiagnosisVariantRequestBinding",
    "ResolvedDiagnosisVariant",
    "VariantCapabilities",
    "VariantRegistryError",
    "bind_variant_request",
    "build_a1_variant",
    "build_a2_variant",
    "build_a3_variant",
    "build_b0_variant",
    "build_b1_variant",
    "build_b2_variant",
    "build_b3_variant",
    "build_codegraph_variant",
    "build_full_variant",
    "build_variant_registry",
    "load_variant_registry",
    "validate_variant_request_binding",
]
