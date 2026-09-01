"""Immutable contracts for the diagnosis development pilot."""

from __future__ import annotations

from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.diagnosis.variant_registry import DiagnosisVariantRequestBinding, VariantId
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import REQUIRED_VARIANTS
from aletheia_lab.project.identity import SHA256_PATTERN, content_sha256, normalize_text

DEVELOPMENT_PLAN_SCHEMA_VERSION: Final = "diagnosis-development-plan/v1"
DEVELOPMENT_CASE_SCHEMA_VERSION: Final = "diagnosis-development-case/v1"
DEVELOPMENT_LEDGER_SCHEMA_VERSION: Final = "diagnosis-development-tool-ledger/v1"
DEVELOPMENT_REQUEST_SCHEMA_VERSION: Final = "diagnosis-development-request/v1"
DEVELOPMENT_RESPONSE_SCHEMA_VERSION: Final = "diagnosis-development-response/v1"
DEVELOPMENT_RECORD_SCHEMA_VERSION: Final = "diagnosis-development-record/v1"
DEVELOPMENT_MANIFEST_SCHEMA_VERSION: Final = "diagnosis-development-manifest/v1"
DEVELOPMENT_TERMINAL_SCHEMA_VERSION: Final = "diagnosis-development-terminal/v1"
DEVELOPMENT_FAILURE_SCHEMA_VERSION: Final = "diagnosis-development-failure/v1"

DEVELOPMENT_MODE: Final = "development_synthetic"
_CASE_ID_PATTERN: Final = r"^devcase-[a-z0-9][a-z0-9-]{0,62}$"
_EVIDENCE_ID_PATTERN: Final = r"^devev-[a-z0-9][a-z0-9-]{0,62}$"
_RUN_ID_PATTERN: Final = r"^devrun-[0-9a-f]{64}$"
_FAILURE_ID_PATTERN: Final = r"^devfail-[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
DevelopmentMode = Literal["development_synthetic"]
DevelopmentTool = Literal["retrieval", "code_graph", "native_external_fixture"]


class DevelopmentPilotError(ValueError):
    """Raised when development execution or its immutable store fails closed."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class DevelopmentEvidenceItem(_StrictFrozenModel):
    evidence_id: str = Field(pattern=_EVIDENCE_ID_PATTERN)
    kind: Literal["metric", "config", "log", "artifact", "lineage", "human_note"]
    title: str
    content: str
    content_sha256: Sha256

    @field_validator("title", "content")
    @classmethod
    def _visible_text_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="development evidence text", max_length=2048)

    @model_validator(mode="after")
    def _content_hash_reconciles(self) -> Self:
        if self.content_sha256 != content_sha256(self.content.encode("utf-8")):
            raise ValueError("development evidence content hash does not match")
        return self


class DevelopmentCase(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-case/v1"] = DEVELOPMENT_CASE_SCHEMA_VERSION
    case_id: str = Field(pattern=_CASE_ID_PATTERN)
    source: Literal["synthetic_fixture"] = "synthetic_fixture"
    protected_outcome_visible: Literal[False] = False
    evaluator_metadata_visible: Literal[False] = False
    expected_evidence_state: Literal["sufficient", "insufficient", "conflicting"]
    evidence: tuple[DevelopmentEvidenceItem, ...] = Field(min_length=1, max_length=32)
    case_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"case_sha256"})

    @model_validator(mode="after")
    def _case_identity_reconciles(self) -> Self:
        identifiers = tuple(item.evidence_id for item in self.evidence)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("development case evidence IDs must be unique")
        if self.case_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development case hash does not match visible content")
        return self


class DevelopmentPilotPlan(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-plan/v1"] = DEVELOPMENT_PLAN_SCHEMA_VERSION
    mode: DevelopmentMode = DEVELOPMENT_MODE
    plan_version: str = Field(pattern=r"^diagnosis-development/[1-9][0-9]*$")
    freeze_sha256: Sha256
    registry_sha256: Sha256
    variant_ids: tuple[VariantId, ...]
    cases: tuple[DevelopmentCase, ...] = Field(min_length=1)
    executor_identity: Literal["deterministic-development-executor/1"]
    external_network_permitted: Literal[False] = False
    live_provider_calls_permitted: Literal[False] = False
    protected_outcomes_opened: Literal[False] = False
    registered_attempts_consumed: Literal[0] = 0
    scientific_interpretation_permitted: Literal[False] = False
    plan_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_sha256"})

    @model_validator(mode="after")
    def _scope_and_identity_reconcile(self) -> Self:
        if self.variant_ids != REQUIRED_VARIANTS:
            raise ValueError("development plan requires the canonical nine variants")
        case_ids = tuple(item.case_id for item in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("development plan case IDs must be unique")
        if self.plan_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development plan hash does not match its content")
        return self


class DevelopmentToolEvent(_StrictFrozenModel):
    turn: int = Field(gt=0)
    tool: DevelopmentTool
    query: str
    selected_evidence_ids: tuple[str, ...]
    omitted_evidence_ids: tuple[str, ...]
    event_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"event_sha256"})

    @field_validator("query")
    @classmethod
    def _query_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="development tool query", max_length=512)

    @model_validator(mode="after")
    def _event_reconciles(self) -> Self:
        selected = self.selected_evidence_ids
        omitted = self.omitted_evidence_ids
        if not selected or len(selected) != len(set(selected)):
            raise ValueError("development tool selection must be non-empty and unique")
        if len(omitted) != len(set(omitted)) or set(selected) & set(omitted):
            raise ValueError("development tool selected and omitted IDs must be disjoint")
        if self.event_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development tool event hash does not match")
        return self


class DevelopmentToolLedger(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-tool-ledger/v1"] = (
        DEVELOPMENT_LEDGER_SCHEMA_VERSION
    )
    variant_id: VariantId
    case_id: str = Field(pattern=_CASE_ID_PATTERN)
    events: tuple[DevelopmentToolEvent, ...] = Field(min_length=1)
    web_used: Literal[False] = False
    shell_used: Literal[False] = False
    project_execution_used: Literal[False] = False
    fallback_used: Literal[False] = False
    ledger_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"ledger_sha256"})

    @model_validator(mode="after")
    def _ledger_reconciles(self) -> Self:
        turns = tuple(item.turn for item in self.events)
        if turns != tuple(range(1, len(turns) + 1)):
            raise ValueError("development tool turns must be contiguous and ordered")
        if self.ledger_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development tool ledger hash does not match")
        return self


class DevelopmentVariantRequest(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-request/v1"] = DEVELOPMENT_REQUEST_SCHEMA_VERSION
    mode: DevelopmentMode = DEVELOPMENT_MODE
    plan_sha256: Sha256
    case_id: str = Field(pattern=_CASE_ID_PATTERN)
    case_sha256: Sha256
    variant_id: VariantId
    binding: DiagnosisVariantRequestBinding
    prompt_text: str
    prompt_content_sha256: Sha256
    context_payload: dict[str, object]
    context_sha256: Sha256
    evidence_content_sha256: Sha256
    tool_ledger: DevelopmentToolLedger | None
    external_network_permitted: Literal[False] = False
    live_provider_call: Literal[False] = False
    protected_outcome_visible: Literal[False] = False
    registered_attempt_consumed: Literal[False] = False
    request_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"request_sha256"})

    @field_validator("prompt_text")
    @classmethod
    def _prompt_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="development prompt", max_length=4096)

    @model_validator(mode="after")
    def _request_reconciles(self) -> Self:
        if self.binding.variant_id != self.variant_id:
            raise ValueError("development request binding has the wrong variant")
        if self.prompt_content_sha256 != content_sha256(self.prompt_text.encode("utf-8")):
            raise ValueError("development prompt hash does not match")
        if self.context_sha256 != canonical_execution_sha256(self.context_payload):
            raise ValueError("development context hash does not match")
        if self.binding.context_sha256 != self.context_sha256:
            raise ValueError("development binding has the wrong context hash")
        if self.binding.evidence_content_sha256 != self.evidence_content_sha256:
            raise ValueError("development binding has the wrong evidence hash")
        ledger_hash = self.tool_ledger.ledger_sha256 if self.tool_ledger else None
        if self.binding.tool_ledger_sha256 != ledger_hash:
            raise ValueError("development binding has the wrong tool ledger hash")
        if self.request_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development request hash does not match")
        return self


class DevelopmentVariantResponse(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-response/v1"] = (
        DEVELOPMENT_RESPONSE_SCHEMA_VERSION
    )
    mode: DevelopmentMode = DEVELOPMENT_MODE
    request_sha256: Sha256
    variant_id: VariantId
    response_schema_ref: str
    diagnosis: str
    cited_evidence_ids: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    abstained: bool
    rule_trace: tuple[str, ...]
    native_result: str | None
    synthetic_fixture: Literal[True] = True
    scientific_interpretation_permitted: Literal[False] = False
    response_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"response_sha256"})

    @field_validator("response_schema_ref", "diagnosis")
    @classmethod
    def _response_text_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="development response text", max_length=2048)

    @model_validator(mode="after")
    def _response_reconciles(self) -> Self:
        if len(self.cited_evidence_ids) != len(set(self.cited_evidence_ids)):
            raise ValueError("development response citations must be unique")
        if self.response_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development response hash does not match")
        return self


class DevelopmentResourceObservation(_StrictFrozenModel):
    context_tokens_upper_bound: int = Field(ge=0)
    retrieved_items: int = Field(ge=0)
    turns: int = Field(ge=1)
    tool_calls: int = Field(ge=0)
    provider_calls: Literal[0] = 0


class DevelopmentRunRecord(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-record/v1"] = DEVELOPMENT_RECORD_SCHEMA_VERSION
    case_id: str = Field(pattern=_CASE_ID_PATTERN)
    variant_id: VariantId
    request_object_sha256: Sha256
    response_object_sha256: Sha256
    tool_ledger_object_sha256: Sha256 | None
    request_sha256: Sha256
    response_sha256: Sha256
    binding_sha256: Sha256
    resources: DevelopmentResourceObservation
    validation_status: Literal["validated"] = "validated"
    fallback_used: Literal[False] = False
    protected_outcome_visible: Literal[False] = False
    record_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"record_sha256"})

    @model_validator(mode="after")
    def _record_reconciles(self) -> Self:
        if self.record_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development record hash does not match")
        return self


class DevelopmentRecordPointer(_StrictFrozenModel):
    case_id: str = Field(pattern=_CASE_ID_PATTERN)
    variant_id: VariantId
    record_object_sha256: Sha256


class DevelopmentPilotManifest(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-manifest/v1"] = (
        DEVELOPMENT_MANIFEST_SCHEMA_VERSION
    )
    mode: DevelopmentMode = DEVELOPMENT_MODE
    plan_sha256: Sha256
    freeze_sha256: Sha256
    registry_sha256: Sha256
    executor_identity: Literal["deterministic-development-executor/1"]
    case_ids: tuple[str, ...]
    variant_ids: tuple[VariantId, ...]
    records: tuple[DevelopmentRecordPointer, ...]
    object_sha256s: tuple[Sha256, ...]
    protected_outcomes_opened: Literal[False] = False
    live_provider_calls: Literal[0] = 0
    registered_attempts_consumed: Literal[0] = 0
    scientific_interpretation_permitted: Literal[False] = False
    manifest_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_sha256"})

    @model_validator(mode="after")
    def _manifest_reconciles(self) -> Self:
        if self.variant_ids != REQUIRED_VARIANTS:
            raise ValueError("development manifest lost the canonical variant census")
        expected_pairs = tuple(
            (case_id, variant_id) for case_id in self.case_ids for variant_id in self.variant_ids
        )
        actual_pairs = tuple((item.case_id, item.variant_id) for item in self.records)
        if actual_pairs != expected_pairs:
            raise ValueError("development manifest matrix is incomplete or out of order")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("development manifest case IDs must be unique")
        if tuple(sorted(self.object_sha256s)) != self.object_sha256s:
            raise ValueError("development manifest object hashes must be unique and sorted")
        if len(self.object_sha256s) != len(set(self.object_sha256s)):
            raise ValueError("development manifest object hashes must be unique")
        if self.manifest_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development manifest hash does not match")
        return self

    @property
    def run_id(self) -> str:
        return f"devrun-{self.manifest_sha256}"


class DevelopmentTerminalReceipt(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-terminal/v1"] = (
        DEVELOPMENT_TERMINAL_SCHEMA_VERSION
    )
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    manifest_sha256: Sha256
    manifest_object_sha256: Sha256
    object_count: int = Field(gt=0)
    status: Literal["development_pilot_complete"] = "development_pilot_complete"
    protected_outcomes_opened: Literal[False] = False
    live_provider_calls: Literal[0] = 0
    registered_attempts_consumed: Literal[0] = 0
    scientific_interpretation_permitted: Literal[False] = False
    terminal_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"terminal_sha256"})

    @model_validator(mode="after")
    def _terminal_reconciles(self) -> Self:
        if self.run_id != f"devrun-{self.manifest_sha256}":
            raise ValueError("development terminal run ID does not match manifest")
        if self.terminal_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("development terminal hash does not match")
        return self


class DevelopmentFailureReceipt(_StrictFrozenModel):
    schema_version: Literal["diagnosis-development-failure/v1"] = DEVELOPMENT_FAILURE_SCHEMA_VERSION
    failure_id: str = Field(pattern=_FAILURE_ID_PATTERN)
    plan_sha256: Sha256
    registry_sha256: Sha256
    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    exception_class: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    message_sha256: Sha256
    partial_terminal_publication: Literal[False] = False
    protected_outcomes_opened: Literal[False] = False
    scientific_interpretation_permitted: Literal[False] = False
    failure_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"failure_id", "failure_sha256"})

    @model_validator(mode="after")
    def _failure_reconciles(self) -> Self:
        expected = canonical_execution_sha256(self.identity_payload())
        if self.failure_sha256 != expected or self.failure_id != f"devfail-{expected}":
            raise ValueError("development failure receipt identity does not match")
        return self
