"""Outcome-blind development runner for the frozen diagnosis variants.

The runner exercises orchestration, policy binding and artifact publication with
synthetic fixtures only.  It cannot call a live provider, open protected outcomes
or consume a registered execution attempt.  A completed run is an engineering
readiness artifact and never a scientific result.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Final, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    DiagnosisVariantRequestBinding,
    ResolvedDiagnosisVariant,
    VariantId,
    bind_variant_request,
    validate_variant_request_binding,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import (
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
)
from aletheia_lab.filesystem import publish_staged_directory
from aletheia_lab.project.identity import (
    SHA256_PATTERN,
    canonical_project_json,
    content_sha256,
    normalize_text,
)

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
_OBJECT_BUCKET = re.compile(r"^[0-9a-f]{2}$")
_OBJECT_NAME = re.compile(r"^[0-9a-f]{62}$")

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
    schema_version: Literal["diagnosis-development-case/v1"] = (
        DEVELOPMENT_CASE_SCHEMA_VERSION
    )
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
    schema_version: Literal["diagnosis-development-plan/v1"] = (
        DEVELOPMENT_PLAN_SCHEMA_VERSION
    )
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
    schema_version: Literal["diagnosis-development-request/v1"] = (
        DEVELOPMENT_REQUEST_SCHEMA_VERSION
    )
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
    schema_version: Literal["diagnosis-development-record/v1"] = (
        DEVELOPMENT_RECORD_SCHEMA_VERSION
    )
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
            (case_id, variant_id)
            for case_id in self.case_ids
            for variant_id in self.variant_ids
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
    schema_version: Literal["diagnosis-development-failure/v1"] = (
        DEVELOPMENT_FAILURE_SCHEMA_VERSION
    )
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
        return self.model_dump(
            mode="json", exclude={"failure_id", "failure_sha256"}
        )

    @model_validator(mode="after")
    def _failure_reconciles(self) -> Self:
        expected = canonical_execution_sha256(self.identity_payload())
        if self.failure_sha256 != expected or self.failure_id != f"devfail-{expected}":
            raise ValueError("development failure receipt identity does not match")
        return self


class DevelopmentVariantExecutor(Protocol):
    """Development executor boundary; production adapters are intentionally excluded."""

    @property
    def identity(self) -> str: ...

    @property
    def external_calls(self) -> bool: ...

    def execute(
        self,
        request: DevelopmentVariantRequest,
        variant: ResolvedDiagnosisVariant,
        case: DevelopmentCase,
    ) -> DevelopmentVariantResponse: ...


class DeterministicDevelopmentExecutor:
    """Pure deterministic executor used only to exercise all frozen paths."""

    @property
    def identity(self) -> str:
        return "deterministic-development-executor/1"

    @property
    def external_calls(self) -> bool:
        return False

    def execute(
        self,
        request: DevelopmentVariantRequest,
        variant: ResolvedDiagnosisVariant,
        case: DevelopmentCase,
    ) -> DevelopmentVariantResponse:
        citations = (
            (case.evidence[0].evidence_id,)
            if variant.capabilities.citation_required
            else ()
        )
        abstained = (
            variant.capabilities.abstention_required
            and case.expected_evidence_state == "insufficient"
        )
        missing = ("additional corroborating trace",) if abstained else ()
        rule_trace = (
            ("visible-feature-check", "bounded-rule-result")
            if variant.strategy == "deterministic_rules"
            else ()
        )
        native_result = (
            "native fixture contract exercised"
            if variant.strategy == "native_external"
            else None
        )
        diagnosis = (
            "Evidence is insufficient for a bounded diagnosis."
            if abstained
            else f"Synthetic {variant.variant_id} path produced a bounded development response."
        )
        payload = {
            "schema_version": DEVELOPMENT_RESPONSE_SCHEMA_VERSION,
            "mode": DEVELOPMENT_MODE,
            "request_sha256": request.request_sha256,
            "variant_id": variant.variant_id,
            "response_schema_ref": _response_schema_ref(variant),
            "diagnosis": diagnosis,
            "cited_evidence_ids": citations,
            "missing_evidence": missing,
            "abstained": abstained,
            "rule_trace": rule_trace,
            "native_result": native_result,
            "synthetic_fixture": True,
            "scientific_interpretation_permitted": False,
        }
        return DevelopmentVariantResponse.model_validate(
            {**payload, "response_sha256": canonical_execution_sha256(payload)}
        )


def build_development_evidence_item(
    *,
    evidence_id: str,
    kind: Literal["metric", "config", "log", "artifact", "lineage", "human_note"],
    title: str,
    content: str,
) -> DevelopmentEvidenceItem:
    """Build one synthetic visible item with a content-bound identity."""

    return DevelopmentEvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        title=title,
        content=content,
        content_sha256=content_sha256(content.encode("utf-8")),
    )


def build_development_case(
    *,
    case_id: str,
    expected_evidence_state: Literal["sufficient", "insufficient", "conflicting"],
    evidence: tuple[DevelopmentEvidenceItem, ...],
) -> DevelopmentCase:
    """Build an explicitly non-scientific synthetic development case."""

    identity_payload = {
        "schema_version": DEVELOPMENT_CASE_SCHEMA_VERSION,
        "case_id": case_id,
        "source": "synthetic_fixture",
        "protected_outcome_visible": False,
        "evaluator_metadata_visible": False,
        "expected_evidence_state": expected_evidence_state,
        "evidence": tuple(item.model_dump(mode="json") for item in evidence),
    }
    return DevelopmentCase(
        case_id=case_id,
        expected_evidence_state=expected_evidence_state,
        evidence=evidence,
        case_sha256=canonical_execution_sha256(identity_payload),
    )


def build_development_plan(
    *,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    cases: tuple[DevelopmentCase, ...],
) -> DevelopmentPilotPlan:
    """Bind the synthetic plan to the authoritative freeze and registry."""

    checked_freeze = DiagnosisVariantFairnessFreeze.model_validate(
        freeze.model_dump(mode="python")
    )
    checked_registry = DiagnosisVariantRegistry.model_validate(
        registry.model_dump(mode="python")
    )
    freeze_sha256 = canonical_execution_sha256(checked_freeze.model_dump(mode="json"))
    if checked_registry.freeze_sha256 != freeze_sha256:
        raise DevelopmentPilotError("registry does not belong to supplied freeze")
    identity_payload = {
        "schema_version": DEVELOPMENT_PLAN_SCHEMA_VERSION,
        "mode": DEVELOPMENT_MODE,
        "plan_version": "diagnosis-development/1",
        "freeze_sha256": freeze_sha256,
        "registry_sha256": checked_registry.registry_sha256,
        "variant_ids": REQUIRED_VARIANTS,
        "cases": tuple(item.model_dump(mode="json") for item in cases),
        "executor_identity": "deterministic-development-executor/1",
        "external_network_permitted": False,
        "live_provider_calls_permitted": False,
        "protected_outcomes_opened": False,
        "registered_attempts_consumed": 0,
        "scientific_interpretation_permitted": False,
    }
    return DevelopmentPilotPlan(
        plan_version="diagnosis-development/1",
        freeze_sha256=freeze_sha256,
        registry_sha256=checked_registry.registry_sha256,
        variant_ids=REQUIRED_VARIANTS,
        cases=cases,
        executor_identity="deterministic-development-executor/1",
        plan_sha256=canonical_execution_sha256(identity_payload),
    )


class DevelopmentArtifactStore:
    """Append-only run store with content-addressed objects and atomic terminals."""

    def __init__(self, root: str | Path) -> None:
        supplied = Path(root)
        supplied.mkdir(parents=True, exist_ok=True)
        if supplied.is_symlink() or not supplied.is_dir():
            raise DevelopmentPilotError("development store root must be a real directory")
        self.root = supplied.resolve()
        self.runs_root = self.root / "runs"
        self.failures_root = self.root / "failures"
        for directory in (self.runs_root, self.failures_root):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise DevelopmentPilotError("development store-owned paths must be real")
        self.verify_integrity()

    def publish(
        self,
        manifest: DevelopmentPilotManifest,
        objects: dict[str, bytes],
    ) -> DevelopmentTerminalReceipt:
        checked = DevelopmentPilotManifest.model_validate(
            manifest.model_dump(mode="python")
        )
        if set(objects) != set(checked.object_sha256s):
            raise DevelopmentPilotError("published object census differs from manifest")
        for digest, payload in objects.items():
            if content_sha256(payload) != digest:
                raise DevelopmentPilotError("published object bytes differ from their hash")

        manifest_bytes = _canonical_model_bytes(checked)
        manifest_object_sha256 = content_sha256(manifest_bytes)
        terminal_payload = {
            "schema_version": DEVELOPMENT_TERMINAL_SCHEMA_VERSION,
            "run_id": checked.run_id,
            "manifest_sha256": checked.manifest_sha256,
            "manifest_object_sha256": manifest_object_sha256,
            "object_count": len(objects) + 1,
            "status": "development_pilot_complete",
            "protected_outcomes_opened": False,
            "live_provider_calls": 0,
            "registered_attempts_consumed": 0,
            "scientific_interpretation_permitted": False,
        }
        terminal = DevelopmentTerminalReceipt.model_validate(
            {
                **terminal_payload,
                "terminal_sha256": canonical_execution_sha256(terminal_payload),
            }
        )

        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.runs_root))
        destination = self.runs_root / checked.run_id
        try:
            object_root = stage / "objects" / "sha256"
            for digest, payload in sorted(
                {**objects, manifest_object_sha256: manifest_bytes}.items()
            ):
                target = object_root / digest[:2] / digest[2:]
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_new_file(target, payload)
            _write_new_file(stage / "terminal.json", _canonical_model_bytes(terminal))
            _fsync_tree(stage)
            try:
                publish_staged_directory(stage, destination)
            except FileExistsError as exc:
                shutil.rmtree(stage)
                existing = self.load_terminal(checked.run_id)
                if existing != terminal:
                    raise DevelopmentPilotError(
                        "conflicting development run already exists"
                    ) from exc
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        self.verify_run(checked.run_id)
        return terminal

    def record_failure(
        self,
        *,
        plan_sha256: str,
        registry_sha256: str,
        stage: str,
        exception: BaseException,
    ) -> DevelopmentFailureReceipt:
        message_sha256 = content_sha256(str(exception).encode("utf-8", errors="strict"))
        payload = {
            "schema_version": DEVELOPMENT_FAILURE_SCHEMA_VERSION,
            "plan_sha256": plan_sha256,
            "registry_sha256": registry_sha256,
            "stage": stage,
            "exception_class": type(exception).__name__,
            "message_sha256": message_sha256,
            "partial_terminal_publication": False,
            "protected_outcomes_opened": False,
            "scientific_interpretation_permitted": False,
        }
        digest = canonical_execution_sha256(payload)
        receipt = DevelopmentFailureReceipt.model_validate(
            {
                **payload,
                "failure_id": f"devfail-{digest}",
                "failure_sha256": digest,
            }
        )
        _atomic_create(
            self.failures_root / f"{receipt.failure_id}.json",
            _canonical_model_bytes(receipt),
        )
        return receipt

    def load_terminal(self, run_id: str) -> DevelopmentTerminalReceipt:
        _validate_run_id(run_id)
        path = self.runs_root / run_id / "terminal.json"
        return DevelopmentTerminalReceipt.model_validate_json(_read_regular_file(path))

    def load_manifest(self, run_id: str) -> DevelopmentPilotManifest:
        terminal = self.load_terminal(run_id)
        raw = self.read_object(run_id, terminal.manifest_object_sha256)
        manifest = DevelopmentPilotManifest.model_validate_json(raw)
        if manifest.manifest_sha256 != terminal.manifest_sha256:
            raise DevelopmentPilotError("terminal references the wrong development manifest")
        return manifest

    def read_object(self, run_id: str, digest: str) -> bytes:
        _validate_run_id(run_id)
        _validate_sha256(digest)
        path = self.runs_root / run_id / "objects" / "sha256" / digest[:2] / digest[2:]
        payload = _read_regular_file(path)
        if content_sha256(payload) != digest:
            raise DevelopmentPilotError("development object content hash mismatch")
        return payload

    def list_runs(self) -> tuple[str, ...]:
        self.verify_integrity()
        return tuple(
            sorted(
                path.name
                for path in self.runs_root.iterdir()
                if path.is_dir() and not path.name.startswith(".stage-")
            )
        )

    def verify_run(self, run_id: str) -> None:
        _validate_run_id(run_id)
        run_root = self.runs_root / run_id
        if run_root.is_symlink() or not run_root.is_dir():
            raise DevelopmentPilotError("published development run is not a real directory")
        terminal = self.load_terminal(run_id)
        manifest = self.load_manifest(run_id)
        expected_objects = set(manifest.object_sha256s) | {
            terminal.manifest_object_sha256
        }
        actual_objects: set[str] = set()
        object_root = run_root / "objects" / "sha256"
        if object_root.is_symlink() or not object_root.is_dir():
            raise DevelopmentPilotError("development object root is invalid")
        for bucket in object_root.iterdir():
            if bucket.is_symlink() or not bucket.is_dir() or not _OBJECT_BUCKET.fullmatch(
                bucket.name
            ):
                raise DevelopmentPilotError("development object bucket is non-canonical")
            for item in bucket.iterdir():
                if item.is_symlink() or not item.is_file() or not _OBJECT_NAME.fullmatch(
                    item.name
                ):
                    raise DevelopmentPilotError("development object name is non-canonical")
                digest = bucket.name + item.name
                if content_sha256(item.read_bytes()) != digest:
                    raise DevelopmentPilotError("development object failed hash verification")
                actual_objects.add(digest)
        if actual_objects != expected_objects:
            raise DevelopmentPilotError("development object membership differs from manifest")
        expected_files = {"terminal.json"} | {
            str(path.relative_to(run_root))
            for path in object_root.rglob("*")
            if path.is_file()
        }
        actual_files = {
            str(path.relative_to(run_root)) for path in run_root.rglob("*") if path.is_file()
        }
        if actual_files != expected_files:
            raise DevelopmentPilotError("development run contains untracked files")

    def verify_integrity(self) -> None:
        for entry in self.root.iterdir():
            if entry.name not in {"runs", "failures"}:
                raise DevelopmentPilotError("development store contains an unknown root entry")
        for entry in self.runs_root.iterdir():
            if entry.name.startswith(".stage-"):
                if entry.is_symlink() or not entry.is_dir():
                    raise DevelopmentPilotError("development stage is not a real directory")
                continue
            self.verify_run(entry.name)
        for receipt_path in self.failures_root.iterdir():
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise DevelopmentPilotError("development failure entry is invalid")
            receipt = DevelopmentFailureReceipt.model_validate_json(
                _read_regular_file(receipt_path)
            )
            if receipt_path.name != f"{receipt.failure_id}.json":
                raise DevelopmentPilotError("development failure file name is non-canonical")


def load_development_plan(path: str | Path) -> DevelopmentPilotPlan:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise DevelopmentPilotError("development plan must be a real file")
    return DevelopmentPilotPlan.model_validate_json(candidate.read_bytes())


def run_development_pilot(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    store: DevelopmentArtifactStore,
    *,
    executor: DevelopmentVariantExecutor | None = None,
) -> DevelopmentTerminalReceipt:
    """Exercise the complete synthetic matrix and atomically publish its terminal."""

    checked_plan = DevelopmentPilotPlan.model_validate(plan.model_dump(mode="python"))
    checked_registry = DiagnosisVariantRegistry.model_validate(
        registry.model_dump(mode="python")
    )
    checked_freeze = DiagnosisVariantFairnessFreeze.model_validate(
        freeze.model_dump(mode="python")
    )
    active_executor = executor or DeterministicDevelopmentExecutor()
    stage = "preflight"
    try:
        _validate_plan_bindings(checked_plan, checked_freeze, checked_registry)
        if type(active_executor) is not DeterministicDevelopmentExecutor:
            raise DevelopmentPilotError(
                "development mode permits only the frozen deterministic executor"
            )
        if active_executor.identity != checked_plan.executor_identity:
            raise DevelopmentPilotError("development executor identity differs from plan")
        if active_executor.external_calls:
            raise DevelopmentPilotError("external-call executor is forbidden in development mode")
        by_spec = {item.variant_id: item for item in checked_freeze.variants}
        objects: dict[str, bytes] = {}
        pointers: list[DevelopmentRecordPointer] = []
        for case in checked_plan.cases:
            _store_model_object(objects, case)
            for variant_id in checked_plan.variant_ids:
                stage = "build_request"
                variant = checked_registry.require(variant_id)
                request = _build_request(
                    checked_plan,
                    checked_freeze,
                    checked_registry,
                    case,
                    variant,
                )
                stage = "execute_fixture"
                response = active_executor.execute(request, variant, case)
                stage = "validate_response"
                validate_response_against_authority(request, response, variant, case)
                request_object = _store_model_object(objects, request)
                response_object = _store_model_object(objects, response)
                ledger_object = (
                    _store_model_object(objects, request.tool_ledger)
                    if request.tool_ledger is not None
                    else None
                )
                budget = checked_freeze.information_budgets[
                    by_spec[variant_id].information_budget_ref
                ]
                resources = _resource_observation(request)
                if (
                    resources.context_tokens_upper_bound > budget.maximum_context_tokens
                    or resources.retrieved_items > budget.maximum_retrieved_items
                    or resources.turns > budget.maximum_turns
                ):
                    raise DevelopmentPilotError("development request exceeds frozen budget")
                record_identity_payload = {
                    "schema_version": DEVELOPMENT_RECORD_SCHEMA_VERSION,
                    "case_id": case.case_id,
                    "variant_id": variant_id,
                    "request_object_sha256": request_object,
                    "response_object_sha256": response_object,
                    "tool_ledger_object_sha256": ledger_object,
                    "request_sha256": request.request_sha256,
                    "response_sha256": response.response_sha256,
                    "binding_sha256": request.binding.binding_sha256,
                    "resources": resources.model_dump(mode="json"),
                    "validation_status": "validated",
                    "fallback_used": False,
                    "protected_outcome_visible": False,
                }
                record = DevelopmentRunRecord(
                    case_id=case.case_id,
                    variant_id=variant_id,
                    request_object_sha256=request_object,
                    response_object_sha256=response_object,
                    tool_ledger_object_sha256=ledger_object,
                    request_sha256=request.request_sha256,
                    response_sha256=response.response_sha256,
                    binding_sha256=request.binding.binding_sha256,
                    resources=resources,
                    record_sha256=canonical_execution_sha256(record_identity_payload),
                )
                record_object = _store_model_object(objects, record)
                pointers.append(
                    DevelopmentRecordPointer(
                        case_id=case.case_id,
                        variant_id=variant_id,
                        record_object_sha256=record_object,
                    )
                )
        stage = "publish_terminal"
        manifest_identity_payload = {
            "schema_version": DEVELOPMENT_MANIFEST_SCHEMA_VERSION,
            "mode": DEVELOPMENT_MODE,
            "plan_sha256": checked_plan.plan_sha256,
            "freeze_sha256": checked_registry.freeze_sha256,
            "registry_sha256": checked_registry.registry_sha256,
            "executor_identity": active_executor.identity,
            "case_ids": tuple(item.case_id for item in checked_plan.cases),
            "variant_ids": checked_plan.variant_ids,
            "records": tuple(item.model_dump(mode="json") for item in pointers),
            "object_sha256s": tuple(sorted(objects)),
            "protected_outcomes_opened": False,
            "live_provider_calls": 0,
            "registered_attempts_consumed": 0,
            "scientific_interpretation_permitted": False,
        }
        manifest = DevelopmentPilotManifest(
            plan_sha256=checked_plan.plan_sha256,
            freeze_sha256=checked_registry.freeze_sha256,
            registry_sha256=checked_registry.registry_sha256,
            executor_identity="deterministic-development-executor/1",
            case_ids=tuple(item.case_id for item in checked_plan.cases),
            variant_ids=checked_plan.variant_ids,
            records=tuple(pointers),
            object_sha256s=tuple(sorted(objects)),
            manifest_sha256=canonical_execution_sha256(manifest_identity_payload),
        )
        return store.publish(manifest, objects)
    except Exception as exc:
        store.record_failure(
            plan_sha256=checked_plan.plan_sha256,
            registry_sha256=checked_registry.registry_sha256,
            stage=stage,
            exception=exc,
        )
        if isinstance(exc, DevelopmentPilotError):
            raise
        raise DevelopmentPilotError("development pilot failed closed") from exc


def load_run_record(
    store: DevelopmentArtifactStore,
    run_id: str,
    digest: str,
) -> DevelopmentRunRecord:
    return DevelopmentRunRecord.model_validate_json(store.read_object(run_id, digest))


def load_run_request(
    store: DevelopmentArtifactStore,
    run_id: str,
    digest: str,
) -> DevelopmentVariantRequest:
    return DevelopmentVariantRequest.model_validate_json(store.read_object(run_id, digest))


def load_run_response(
    store: DevelopmentArtifactStore,
    run_id: str,
    digest: str,
) -> DevelopmentVariantResponse:
    return DevelopmentVariantResponse.model_validate_json(store.read_object(run_id, digest))


def validate_request_against_authority(
    request: DevelopmentVariantRequest,
    *,
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    case: DevelopmentCase,
) -> None:
    """Reconcile a persisted request against the plan, freeze and registry."""

    request = DevelopmentVariantRequest.model_validate(request.model_dump(mode="python"))
    if (
        request.plan_sha256 != plan.plan_sha256
        or request.case_id != case.case_id
        or request.case_sha256 != case.case_sha256
    ):
        raise DevelopmentPilotError("development request differs from plan or case")
    spec = next(item for item in freeze.variants if item.variant_id == request.variant_id)
    prompt = freeze.prompt_policies[spec.prompt_policy_ref]
    if request.prompt_text != prompt.instruction_contract:
        raise DevelopmentPilotError("development request prompt differs from freeze")
    expected_context = _context_payload(case)
    expected_evidence_hash = _evidence_sha256(case)
    expected_ledger = _build_tool_ledger(case, registry.require(request.variant_id))
    expected_ledger_hash = expected_ledger.ledger_sha256 if expected_ledger else None
    if request.context_payload != expected_context:
        raise DevelopmentPilotError("development request context differs from case")
    validate_variant_request_binding(
        registry,
        request.binding,
        context_sha256=canonical_execution_sha256(expected_context),
        evidence_content_sha256=expected_evidence_hash,
        tool_ledger_sha256=expected_ledger_hash,
    )
    if request.tool_ledger != expected_ledger:
        raise DevelopmentPilotError("development request tool ledger differs from policy")


def _validate_plan_bindings(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
) -> None:
    freeze_sha = canonical_execution_sha256(freeze.model_dump(mode="json"))
    if (
        plan.freeze_sha256 != freeze_sha
        or plan.freeze_sha256 != registry.freeze_sha256
        or plan.registry_sha256 != registry.registry_sha256
    ):
        raise DevelopmentPilotError("development plan differs from frozen registry")


def _build_request(
    plan: DevelopmentPilotPlan,
    freeze: DiagnosisVariantFairnessFreeze,
    registry: DiagnosisVariantRegistry,
    case: DevelopmentCase,
    variant: ResolvedDiagnosisVariant,
) -> DevelopmentVariantRequest:
    spec = next(item for item in freeze.variants if item.variant_id == variant.variant_id)
    prompt = freeze.prompt_policies[spec.prompt_policy_ref]
    context_payload = _context_payload(case)
    context_sha = canonical_execution_sha256(context_payload)
    evidence_sha = _evidence_sha256(case)
    ledger = _build_tool_ledger(case, variant)
    ledger_sha = ledger.ledger_sha256 if ledger else None
    binding = bind_variant_request(
        registry,
        variant_id=variant.variant_id,
        context_sha256=context_sha,
        evidence_content_sha256=evidence_sha,
        tool_ledger_sha256=ledger_sha,
    )
    identity_payload = {
        "schema_version": DEVELOPMENT_REQUEST_SCHEMA_VERSION,
        "mode": DEVELOPMENT_MODE,
        "plan_sha256": plan.plan_sha256,
        "case_id": case.case_id,
        "case_sha256": case.case_sha256,
        "variant_id": variant.variant_id,
        "binding": binding.model_dump(mode="json"),
        "prompt_text": prompt.instruction_contract,
        "prompt_content_sha256": content_sha256(prompt.instruction_contract.encode("utf-8")),
        "context_payload": context_payload,
        "context_sha256": context_sha,
        "evidence_content_sha256": evidence_sha,
        "tool_ledger": ledger.model_dump(mode="json") if ledger else None,
        "external_network_permitted": False,
        "live_provider_call": False,
        "protected_outcome_visible": False,
        "registered_attempt_consumed": False,
    }
    return DevelopmentVariantRequest(
        plan_sha256=plan.plan_sha256,
        case_id=case.case_id,
        case_sha256=case.case_sha256,
        variant_id=variant.variant_id,
        binding=binding,
        prompt_text=prompt.instruction_contract,
        prompt_content_sha256=content_sha256(prompt.instruction_contract.encode("utf-8")),
        context_payload=context_payload,
        context_sha256=context_sha,
        evidence_content_sha256=evidence_sha,
        tool_ledger=ledger,
        request_sha256=canonical_execution_sha256(identity_payload),
    )


def _build_tool_ledger(
    case: DevelopmentCase,
    variant: ResolvedDiagnosisVariant,
) -> DevelopmentToolLedger | None:
    if not variant.capabilities.tool_ledger_required:
        return None
    evidence_ids = tuple(item.evidence_id for item in case.evidence)
    if variant.strategy == "native_external":
        tools: tuple[DevelopmentTool, ...] = ("native_external_fixture",)
    elif variant.strategy == "codegraph_retrieval":
        tools = ("code_graph",)
    elif variant.strategy == "full_system":
        tools = ("retrieval", "code_graph")
    else:
        tools = ("retrieval",)
    events: list[DevelopmentToolEvent] = []
    for index, tool in enumerate(tools, start=1):
        selected = evidence_ids[index - 1 :: len(tools)] or evidence_ids[:1]
        omitted = tuple(item for item in evidence_ids if item not in selected)
        event_payload = {
            "turn": index,
            "tool": tool,
            "query": f"development query {index} for {case.case_id}",
            "selected_evidence_ids": selected,
            "omitted_evidence_ids": omitted,
        }
        events.append(
            DevelopmentToolEvent.model_validate(
                {
                    **event_payload,
                    "event_sha256": canonical_execution_sha256(event_payload),
                }
            )
        )
    ledger_identity_payload = {
        "schema_version": DEVELOPMENT_LEDGER_SCHEMA_VERSION,
        "variant_id": variant.variant_id,
        "case_id": case.case_id,
        "events": tuple(item.model_dump(mode="json") for item in events),
        "web_used": False,
        "shell_used": False,
        "project_execution_used": False,
        "fallback_used": False,
    }
    return DevelopmentToolLedger(
        variant_id=variant.variant_id,
        case_id=case.case_id,
        events=tuple(events),
        ledger_sha256=canonical_execution_sha256(ledger_identity_payload),
    )


def validate_response_against_authority(
    request: DevelopmentVariantRequest,
    response: DevelopmentVariantResponse,
    variant: ResolvedDiagnosisVariant,
    case: DevelopmentCase,
) -> None:
    response = DevelopmentVariantResponse.model_validate(
        response.model_dump(mode="python")
    )
    if (
        response.request_sha256 != request.request_sha256
        or response.variant_id != variant.variant_id
        or response.response_schema_ref != _response_schema_ref(variant)
    ):
        raise DevelopmentPilotError("development response differs from request or variant")
    visible_ids = {item.evidence_id for item in case.evidence}
    if not set(response.cited_evidence_ids).issubset(visible_ids):
        raise DevelopmentPilotError("development response cites non-visible evidence")
    if variant.capabilities.citation_required and not response.cited_evidence_ids:
        raise DevelopmentPilotError("citation-required variant omitted citations")
    if not variant.capabilities.citation_required and response.cited_evidence_ids:
        raise DevelopmentPilotError("non-citation variant gained unregistered citations")
    expected_abstention = (
        variant.capabilities.abstention_required
        and case.expected_evidence_state == "insufficient"
    )
    if response.abstained != expected_abstention:
        raise DevelopmentPilotError("development abstention behavior differs from contract")
    if (variant.strategy == "deterministic_rules") != bool(response.rule_trace):
        raise DevelopmentPilotError("development deterministic rule trace is inconsistent")
    if (variant.strategy == "native_external") != (response.native_result is not None):
        raise DevelopmentPilotError("development native output shape is inconsistent")


def resource_observation_for_request(
    request: DevelopmentVariantRequest,
) -> DevelopmentResourceObservation:
    """Recompute the deterministic resource envelope for one persisted request."""

    checked = DevelopmentVariantRequest.model_validate(request.model_dump(mode="python"))
    return _resource_observation(checked)


def _response_schema_ref(variant: ResolvedDiagnosisVariant) -> str:
    if variant.strategy == "deterministic_rules":
        return "deterministic-diagnosis/1"
    if variant.strategy == "native_external":
        return "logdx-native-output/1"
    return "diagnosis-output/2"


def _context_payload(case: DevelopmentCase) -> dict[str, object]:
    return {
        "schema_version": "diagnosis-development-context/v1",
        "case_id": case.case_id,
        "source": case.source,
        "evidence": [item.model_dump(mode="json") for item in case.evidence],
        "protected_outcome_visible": False,
        "evaluator_metadata_visible": False,
    }


def _evidence_sha256(case: DevelopmentCase) -> str:
    return canonical_execution_sha256(
        [item.model_dump(mode="json") for item in case.evidence]
    )


def _resource_observation(
    request: DevelopmentVariantRequest,
) -> DevelopmentResourceObservation:
    context_bytes = canonical_project_json(request.context_payload).encode("utf-8")
    ledger = request.tool_ledger
    return DevelopmentResourceObservation(
        # Any token representing the UTF-8 payload consumes at least one byte.
        # Counting bytes is deliberately conservative and cannot understate the
        # token count the way a characters-per-token heuristic can.
        context_tokens_upper_bound=len(context_bytes),
        retrieved_items=(
            sum(len(item.selected_evidence_ids) for item in ledger.events)
            if ledger
            else 0
        ),
        turns=len(ledger.events) if ledger else 1,
        tool_calls=len(ledger.events) if ledger else 0,
        provider_calls=0,
    )


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (canonical_project_json(model.model_dump(mode="json")) + "\n").encode("utf-8")


def _store_model_object(objects: dict[str, bytes], model: BaseModel) -> str:
    payload = _canonical_model_bytes(model)
    digest = content_sha256(payload)
    existing = objects.get(digest)
    if existing is not None and existing != payload:
        raise DevelopmentPilotError("development object hash collision")
    objects[digest] = payload
    return digest


def _write_new_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_stage = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    stage = Path(raw_stage)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(stage, path)
        except FileExistsError as exc:
            if _read_regular_file(path) != payload:
                raise DevelopmentPilotError(
                    "immutable development receipt conflict"
                ) from exc
    finally:
        stage.unlink(missing_ok=True)


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    if hasattr(os, "O_DIRECTORY"):
        for directory in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ) + [root]:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def _read_regular_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise DevelopmentPilotError("development artifact must be a regular file")
    return path.read_bytes()


def _validate_sha256(value: str) -> None:
    if re.fullmatch(SHA256_PATTERN, value) is None:
        raise DevelopmentPilotError("development object hash is invalid")


def _validate_run_id(value: str) -> None:
    if re.fullmatch(_RUN_ID_PATTERN, value) is None:
        raise DevelopmentPilotError("development run ID is invalid")


__all__ = [
    "DEVELOPMENT_MODE",
    "DevelopmentArtifactStore",
    "DevelopmentCase",
    "DevelopmentEvidenceItem",
    "DevelopmentFailureReceipt",
    "DevelopmentPilotError",
    "DevelopmentPilotManifest",
    "DevelopmentPilotPlan",
    "DevelopmentResourceObservation",
    "DevelopmentRunRecord",
    "DevelopmentTerminalReceipt",
    "DevelopmentToolEvent",
    "DevelopmentToolLedger",
    "DevelopmentVariantExecutor",
    "DevelopmentVariantRequest",
    "DevelopmentVariantResponse",
    "DeterministicDevelopmentExecutor",
    "build_development_case",
    "build_development_evidence_item",
    "build_development_plan",
    "load_development_plan",
    "load_run_record",
    "load_run_request",
    "load_run_response",
    "run_development_pilot",
    "resource_observation_for_request",
    "validate_request_against_authority",
    "validate_response_against_authority",
]
