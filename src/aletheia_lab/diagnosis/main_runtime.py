"""Outcome-eligible, fail-closed runtime for the controlled diagnosis matrix.

This module turns one frozen logical request into its declared interaction
path.  B2, CodeGraph and FULL perform real provider-mediated selection turns;
the tool layer then retrieves only IDs from the original model-visible corpus.
Every gateway request is persisted before invocation, every gateway result is
persisted afterwards, and an incomplete turn is never replayed automatically.

Construction and preflight are local and outcome-blind.  Execution requires a
separate, self-hashed :class:`MainExecutionAuthority`; this module never mints
that authority and performs no provider call by itself.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.diagnosis.main_response import (
    DiagnosisMainResponseError,
    validate_main_provider_output,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    CONTROLLED_VARIANTS,
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainFamily,
)
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainPrivateCensusPacket,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import (
    DiagnosisVariantFairnessFreeze,
    load_diagnosis_variant_freeze,
)
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    CancellationProbe,
    Clock,
    GatewayExecutionResult,
    GatewayRequest,
    OpenAIGatewayPolicy,
    ProviderAdapter,
    RuntimePolicyReference,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import (
    SHA256_PATTERN,
    canonical_project_json,
    content_sha256,
)

RUNTIME_CONTRACT_SCHEMA_VERSION: Final = "diagnosis-main-runtime-contract/v1"
EXECUTION_AUTHORITY_SCHEMA_VERSION: Final = "diagnosis-main-execution-authority/v1"
RUNTIME_PREFLIGHT_SCHEMA_VERSION: Final = "diagnosis-main-runtime-preflight/v1"
TURN_RECEIPT_SCHEMA_VERSION: Final = "diagnosis-main-turn-receipt/v1"
LOGICAL_TERMINAL_SCHEMA_VERSION: Final = "diagnosis-main-logical-terminal/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProviderVariant = Literal["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"]
TurnKind = Literal["selection", "final"]
LogicalStatus = Literal[
    "completed",
    "deterministic_completed",
    "technical_failure",
    "selection_failure",
    "semantic_failure",
]

_ROUTE_TURNS: Final = {
    "A1": ("final",),
    "A2": ("final",),
    "A3": ("final",),
    "B0": (),
    "B1": ("final",),
    "B2": ("selection", "final"),
    "CodeGraph": ("selection", "final"),
    "FULL": ("selection", "selection", "final"),
}
_PROVIDER_TURN_COUNT: Final = 1_408


class MainRuntimeError(ValueError):
    """Raised when the main runtime would diverge, leak, or replay a call."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class MainRuntimeContract(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-runtime-contract/v1"]
    status: Literal["outcome_blind_implementation_ready_execution_not_authorized"]
    created_on: Literal["2026-09-20"]
    protected_main_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    analysis_census_sha256: Sha256
    analysis_plan_sha256: Sha256
    response_contract_sha256: Sha256
    controlled_logical_request_count: Literal[1024]
    provider_backed_logical_request_count: Literal[896]
    deterministic_logical_request_count: Literal[128]
    provider_turn_count_if_all_routes_complete: Literal[1408]
    main_model_policy: dict[str, object]
    route_contracts: tuple[dict[str, object], ...]
    retrieval_policy: dict[str, object]
    persistence_policy: dict[str, object]
    semantic_validation: dict[str, object]
    non_pooling: dict[str, object]
    runtime_contract_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"runtime_contract_sha256"})

    @model_validator(mode="after")
    def _contract_reconciles(self) -> Self:
        variants = tuple(item.get("variant") for item in self.route_contracts)
        turns = tuple(item.get("provider_turns") for item in self.route_contracts)
        if variants != CONTROLLED_VARIANTS:
            raise ValueError("runtime routes differ from the controlled matrix")
        if turns != tuple(len(_ROUTE_TURNS[variant]) for variant in CONTROLLED_VARIANTS):
            raise ValueError("runtime route turn counts changed")
        if self.runtime_contract_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("runtime contract identity does not match")
        if self.retrieval_policy.get("selection_quality_fallback_permitted") is not False:
            raise ValueError("runtime contract permits an outcome-sensitive fallback")
        if self.persistence_policy.get("incomplete_turn_automatic_replay_permitted") is not False:
            raise ValueError("runtime contract permits duplicate provider work")
        return self


class MainExecutionAuthority(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-execution-authority/v1"] = (
        EXECUTION_AUTHORITY_SCHEMA_VERSION
    )
    execution_authorized: Literal[True]
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    analysis_census_sha256: Sha256
    analysis_plan_sha256: Sha256
    response_contract_sha256: Sha256
    runtime_contract_sha256: Sha256
    manifest_content_sha256: Sha256
    authorization_ref: str = Field(pattern=r"^ev-[0-9a-f]{64}$")
    authorized_at: str
    authority_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authority_sha256"})

    @model_validator(mode="after")
    def _authority_reconciles(self) -> Self:
        if self.authority_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("main execution authority identity does not match")
        return self


class MainRuntimePreflight(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-runtime-preflight/v1"] = (
        RUNTIME_PREFLIGHT_SCHEMA_VERSION
    )
    status: Literal["runtime_conformance_pass_execution_not_authorized"]
    protected_main_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    runtime_contract_sha256: Sha256
    analysis_census_sha256: Sha256
    logical_request_count: Literal[1024]
    provider_backed_logical_request_count: Literal[896]
    deterministic_logical_request_count: Literal[128]
    provider_turn_count_if_all_routes_complete: Literal[1408]
    route_request_counts: dict[str, int]
    route_turn_counts: dict[str, int]
    context_identity_count: Literal[128]
    hidden_truth_or_evaluator_fields_visible: Literal[False]
    b3_in_controlled_matrix: Literal[False]
    preflight_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preflight_sha256"})

    @model_validator(mode="after")
    def _preflight_reconciles(self) -> Self:
        expected_counts = {variant: 128 for variant in CONTROLLED_VARIANTS}
        expected_turns = {
            variant: 128 * len(_ROUTE_TURNS[variant]) for variant in CONTROLLED_VARIANTS
        }
        if self.route_request_counts != expected_counts or self.route_turn_counts != expected_turns:
            raise ValueError("runtime preflight does not cover the exact route matrix")
        if self.preflight_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("runtime preflight identity does not match")
        return self


class MainSelectionOutput(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-selection-output/v1"]
    requested_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _selection_is_unique(self) -> Self:
        if len(self.requested_evidence_ids) != len(set(self.requested_evidence_ids)) or any(
            not item or item != item.strip() for item in self.requested_evidence_ids
        ):
            raise ValueError("retrieval selection IDs must be unique and trimmed")
        return self


class MainTurnReceipt(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-turn-receipt/v1"] = TURN_RECEIPT_SCHEMA_VERSION
    logical_request_id: str
    logical_request_sha256: Sha256
    turn_ordinal: int = Field(ge=1, le=3)
    turn_kind: TurnKind
    visible_context_sha256: Sha256
    gateway_request_identity_sha256: Sha256
    gateway_result_sha256: Sha256
    gateway_status: str
    selected_evidence_ids: tuple[str, ...]
    omitted_evidence_ids: tuple[str, ...]
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _receipt_reconciles(self) -> Self:
        if set(self.selected_evidence_ids) & set(self.omitted_evidence_ids):
            raise ValueError("turn selected and omitted evidence overlap")
        if self.receipt_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("turn receipt identity does not match")
        return self


class MainLogicalTerminal(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-logical-terminal/v1"] = LOGICAL_TERMINAL_SCHEMA_VERSION
    logical_request_id: str
    logical_request_sha256: Sha256
    context_id: str
    variant: str
    status: LogicalStatus
    expected_provider_turn_count: int = Field(ge=0, le=3)
    completed_provider_turn_count: int = Field(ge=0, le=3)
    turn_receipts: tuple[MainTurnReceipt, ...]
    final_visible_context_sha256: Sha256
    final_raw_response_sha256: Sha256 | None
    deterministic_payload: dict[str, object] | None
    issue_code: str | None
    terminal_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"terminal_sha256"})

    @model_validator(mode="after")
    def _terminal_reconciles(self) -> Self:
        if self.completed_provider_turn_count != len(self.turn_receipts):
            raise ValueError("terminal turn count differs from its receipts")
        if self.completed_provider_turn_count > self.expected_provider_turn_count:
            raise ValueError("terminal contains more turns than its route contract")
        if self.status == "deterministic_completed":
            if (
                self.variant != "B0"
                or self.expected_provider_turn_count != 0
                or self.deterministic_payload is None
                or self.final_raw_response_sha256 is not None
                or self.issue_code is not None
            ):
                raise ValueError("deterministic terminal shape changed")
        elif self.deterministic_payload is not None:
            raise ValueError("provider routes cannot retain a deterministic payload")
        if self.status == "completed" and (
            self.completed_provider_turn_count != self.expected_provider_turn_count
            or self.final_raw_response_sha256 is None
            or self.issue_code is not None
        ):
            raise ValueError("completed logical route is incomplete")
        if self.status not in {"completed", "deterministic_completed"} and not self.issue_code:
            raise ValueError("failed logical route requires a bounded issue code")
        if self.terminal_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("logical terminal identity does not match")
        return self


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def _opaque(payload: object) -> str:
    return f"ev-{canonical_execution_sha256(payload)}"


def load_main_runtime_contract(path: Path) -> MainRuntimeContract:
    if path.is_symlink() or not path.is_file():
        raise MainRuntimeError("main runtime contract is unavailable")
    try:
        return MainRuntimeContract.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise MainRuntimeError("main runtime contract is invalid") from exc


def build_main_runtime_preflight(
    packet: DiagnosisMainPrivateCensusPacket,
    contract: MainRuntimeContract,
) -> MainRuntimePreflight:
    """Prove exact route coverage without constructing authority or calling a model."""

    checked_packet = DiagnosisMainPrivateCensusPacket.model_validate(
        packet.model_dump(mode="python")
    )
    checked_contract = MainRuntimeContract.model_validate(contract.model_dump(mode="python"))
    census = checked_packet.analysis_census
    if census.census_sha256 != checked_contract.analysis_census_sha256:
        raise MainRuntimeError("runtime contract is bound to another census")
    request_counts = dict(sorted(Counter(item.variant for item in census.requests).items()))
    turn_counts = {
        variant: request_counts[variant] * len(_ROUTE_TURNS[variant])
        for variant in CONTROLLED_VARIANTS
    }
    provider_requests = sum(count for variant, count in request_counts.items() if variant != "B0")
    deterministic_requests = request_counts.get("B0", 0)
    provider_turns = sum(turn_counts.values())
    if (
        provider_requests != checked_contract.provider_backed_logical_request_count
        or deterministic_requests != checked_contract.deterministic_logical_request_count
        or provider_turns != checked_contract.provider_turn_count_if_all_routes_complete
        or provider_turns != _PROVIDER_TURN_COUNT
    ):
        raise MainRuntimeError("runtime route totals differ from the frozen contract")
    forbidden = (
        "hidden_ground_truth",
        "human_judgment",
        "automatic_label",
        "evaluator_mapping",
        "main_outcome",
    )
    visible = canonical_execution_json(
        tuple(item.model_payload() for item in checked_packet.visible_contexts)
    ).casefold()
    if any(marker in visible for marker in forbidden):
        raise MainRuntimeError("private census exposes hidden or evaluator fields")
    payload = {
        "schema_version": RUNTIME_PREFLIGHT_SCHEMA_VERSION,
        "status": "runtime_conformance_pass_execution_not_authorized",
        "protected_main_outcomes_opened": False,
        "execution_authorized": False,
        "runtime_contract_sha256": checked_contract.runtime_contract_sha256,
        "analysis_census_sha256": census.census_sha256,
        "logical_request_count": len(census.requests),
        "provider_backed_logical_request_count": provider_requests,
        "deterministic_logical_request_count": deterministic_requests,
        "provider_turn_count_if_all_routes_complete": provider_turns,
        "route_request_counts": request_counts,
        "route_turn_counts": turn_counts,
        "context_identity_count": len(checked_packet.visible_contexts),
        "hidden_truth_or_evaluator_fields_visible": False,
        "b3_in_controlled_matrix": False,
    }
    return MainRuntimePreflight.model_validate(
        {**payload, "preflight_sha256": canonical_execution_sha256(payload)}
    )


def _selection_schema(available_ids: Sequence[str]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": "diagnosis-main-selection-output/v1",
            },
            "requested_evidence_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": min(32, len(available_ids)),
                "items": {"type": "string", "enum": list(available_ids)},
            },
        },
        "required": ["schema_version", "requested_evidence_ids"],
    }


def _final_schema(response_contract: Mapping[str, object]) -> dict[str, object]:
    schema = response_contract.get("json_schema")
    if not isinstance(schema, dict):
        raise MainRuntimeError("main response contract contains no JSON schema")
    return cast(dict[str, object], schema)


def _context_from_items(
    items: Sequence[ModelVisibleEvidenceItem],
) -> ModelVisibleEvidenceContext:
    ordered = tuple(sorted(items, key=lambda item: item.evidence_id))
    if not ordered:
        raise MainRuntimeError("a provider turn cannot receive an empty context")
    payload = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": tuple(item.model_dump(mode="json") for item in ordered),
    }
    digest = canonical_execution_sha256(payload)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{digest}",
        items=ordered,
        context_sha256=digest,
    )


def _catalog_context(original: ModelVisibleEvidenceContext) -> ModelVisibleEvidenceContext:
    return _context_from_items(
        tuple(
            build_visible_evidence_item(
                evidence_id=item.evidence_id,
                kind="artifact",
                title="Available observed record",
                content=canonical_execution_json({"kind": item.kind, "title": item.title}),
                source_content_sha256=item.content_sha256,
            )
            for item in original.items
        )
    )


def _plain_context(
    original: ModelVisibleEvidenceContext,
    selected_ids: Sequence[str],
) -> ModelVisibleEvidenceContext:
    by_id = {item.evidence_id: item for item in original.items}
    lines = [
        f"Record {evidence_id}: {by_id[evidence_id].title}. {by_id[evidence_id].content}"
        for evidence_id in selected_ids
    ]
    source_sha = canonical_execution_sha256(
        tuple(by_id[evidence_id].content_sha256 for evidence_id in selected_ids)
    )
    return _context_from_items(
        (
            build_visible_evidence_item(
                evidence_id="ev-plain-rendering",
                kind="artifact",
                title="Plain rendering of observed facts",
                content=" | ".join(lines),
                source_content_sha256=source_sha,
            ),
        )
    )


def _graph_context(
    original: ModelVisibleEvidenceContext,
    selected_ids: Sequence[str],
    *,
    full: bool,
    prior_turn_hashes: Sequence[str],
) -> ModelVisibleEvidenceContext:
    by_id = {item.evidence_id: item for item in original.items}
    selected = tuple(selected_ids)
    graph_items = [
        build_visible_evidence_item(
            evidence_id=evidence_id,
            kind=by_id[evidence_id].kind,
            title=by_id[evidence_id].title,
            content=canonical_execution_json(
                {
                    "node_id": evidence_id,
                    "observed_record": by_id[evidence_id].content,
                    "related_observed_record_ids": tuple(
                        candidate for candidate in selected if candidate != evidence_id
                    ),
                    "source_fingerprint": by_id[evidence_id].source_content_sha256,
                }
            ),
            source_content_sha256=by_id[evidence_id].content_sha256,
        )
        for evidence_id in selected
    ]
    if full:
        graph_items.append(
            build_visible_evidence_item(
                evidence_id="ev-runtime-provenance",
                kind="lineage",
                title="Registered retrieval and turn provenance",
                content=canonical_execution_json(
                    {
                        "selected_observed_record_ids": selected,
                        "turn_request_fingerprints": tuple(prior_turn_hashes),
                        "record_count": len(selected),
                    }
                ),
                source_content_sha256=canonical_execution_sha256(
                    {"selected": selected, "turns": tuple(prior_turn_hashes)}
                ),
            )
        )
    return _context_from_items(tuple(graph_items))


def _all_original_ids(context: ModelVisibleEvidenceContext) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in context.items)


def _validate_selection(
    result: GatewayExecutionResult,
    *,
    available_ids: tuple[str, ...],
) -> MainSelectionOutput:
    if result.status != "parsed" or result.parsed_response is None:
        raise MainRuntimeError("selection turn did not produce a parsed response")
    selection = MainSelectionOutput.model_validate_json(json.dumps(result.parsed_response.payload))
    if not set(selection.requested_evidence_ids) <= set(available_ids):
        raise MainRuntimeError("selection turn requested unavailable evidence")
    return selection


def _manifest(
    authority: MainExecutionAuthority,
    contract: MainRuntimeContract,
) -> EvaluationManifestReference:
    if (
        authority.runtime_contract_sha256 != contract.runtime_contract_sha256
        or authority.analysis_census_sha256 != contract.analysis_census_sha256
        or authority.analysis_plan_sha256 != contract.analysis_plan_sha256
        or authority.response_contract_sha256 != contract.response_contract_sha256
        or authority.manifest_content_sha256
        != canonical_execution_sha256(
            {
                "analysis_census_sha256": contract.analysis_census_sha256,
                "analysis_plan_sha256": contract.analysis_plan_sha256,
                "response_contract_sha256": contract.response_contract_sha256,
                "runtime_contract_sha256": contract.runtime_contract_sha256,
            }
        )
    ):
        raise MainRuntimeError("execution authority is not bound to the runtime freeze")
    project_sha = canonical_execution_sha256(
        {"project": "aletheia-diagnosis-main", "runtime": contract.runtime_contract_sha256}
    )
    snapshot_sha = canonical_execution_sha256({"source_commit_ref": authority.source_commit_ref})
    return EvaluationManifestReference.build(
        project_id=f"p3-project-{project_sha}",
        snapshot_id=f"p3-snapshot-{snapshot_sha}",
        manifest_content_sha256=authority.manifest_content_sha256,
        source_commit_ref=authority.source_commit_ref,
        authorization_state="authorized",
        authorization_ref=authority.authorization_ref,
        provenance_sha256=authority.authority_sha256,
        created_at=authority.authorized_at,
        frozen_at=authority.authorized_at,
        visibility="diagnosis",
    )


def _gateway_request(
    *,
    manifest: EvaluationManifestReference,
    family: DiagnosisMainFamily,
    logical: DiagnosisMainExpectedRequest,
    logical_context: DiagnosisMainContext,
    provider_context: ModelVisibleEvidenceContext,
    variant: ProviderVariant,
    turn_ordinal: int,
    turn_kind: TurnKind,
    prompt: str,
    response_schema: dict[str, object],
    contract: MainRuntimeContract,
    freeze: DiagnosisVariantFairnessFreeze,
) -> GatewayRequest:
    schema_json = canonical_project_json(response_schema)
    route_content_sha = canonical_execution_sha256(
        next(item for item in contract.route_contracts if item["variant"] == variant)
    )
    case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque({"logical_request": logical.request_sha256}),
        family_id=_opaque({"family": family.family_sha256}),
        mechanism_id=_opaque({"mechanism": family.mechanism}),
        dataset_id=_opaque({"dataset": family.dataset_id}),
        variant_id=_opaque({"variant": variant}),
        variant_content_sha256=route_content_sha,
        case_content_sha256=logical.request_sha256,
        evidence_bundle_id=f"p3-evidence-bundle-{provider_context.context_sha256}",
        evidence_content_sha256=provider_context.context_sha256,
        lineage_graph_id=f"p3-lineage-graph-{logical_context.context_sha256}",
        lineage_sha256=logical_context.context_sha256,
        visibility_projection_sha256=provider_context.context_sha256,
        provenance_sha256=canonical_execution_sha256(
            {
                "logical_request": logical.request_sha256,
                "turn_ordinal": turn_ordinal,
                "turn_kind": turn_kind,
            }
        ),
        visibility="diagnosis",
    )
    openai_policy = OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])
    model_policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=openai_policy.model_policy_sha256(),
        provider_ref=_opaque({"provider": "openai"}),
        model_ref=_opaque({"model": openai_policy.model}),
        model_version_ref=_opaque({"model_version": openai_policy.model_version}),
        resource_policy_ref=_opaque({"route": variant, "turn": turn_ordinal, "kind": turn_kind}),
        prompt_policy_ref=_opaque({"prompt_sha256": content_sha256(prompt.encode("utf-8"))}),
        response_schema_sha256=content_sha256(schema_json.encode("utf-8")),
        provenance_sha256=route_content_sha,
        visibility="diagnosis",
    )
    runtime = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=model_policy,
        retry_policy_ref=_opaque({"maximum_attempts": 2, "fallback": "forbidden"}),
        timeout_ns=60_000_000_000,
        max_attempts=2,
        max_response_bytes=32_768,
        provenance_sha256=contract.runtime_contract_sha256,
    )
    return prepare_gateway_request(
        manifest=manifest,
        case=case,
        model_policy=model_policy,
        context=provider_context,
        prompt_text=prompt,
        response_schema=response_schema,
        runtime_policy=runtime,
    )


def _serialized(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


class MainRuntimeStore:
    """Create-only per-turn persistence; incomplete turns require explicit recovery."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise MainRuntimeError("main runtime store root must be a real directory")
        self.root = root.resolve()

    def _logical_root(self, logical_request_id: str) -> Path:
        if not logical_request_id.startswith("dmr-") or "/" in logical_request_id:
            raise MainRuntimeError("logical request ID is unsafe for persistence")
        path = self.root / logical_request_id
        path.mkdir(exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise MainRuntimeError("logical request store is not a real directory")
        return path

    def terminal(self, logical_request_id: str) -> MainLogicalTerminal | None:
        path = self._logical_root(logical_request_id) / "terminal.json"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise MainRuntimeError("logical terminal is not a regular file")
        return MainLogicalTerminal.model_validate_json(path.read_bytes())

    def begin_or_resume_turn(
        self,
        logical_request_id: str,
        turn_ordinal: int,
        request: GatewayRequest,
    ) -> GatewayExecutionResult | None:
        turn_root = self._logical_root(logical_request_id) / f"turn-{turn_ordinal:02d}"
        turn_root.mkdir(exist_ok=True)
        request_path = turn_root / "request.json"
        result_path = turn_root / "result.json"
        expected = _serialized(request)
        if request_path.exists():
            if (
                request_path.is_symlink()
                or not request_path.is_file()
                or request_path.read_bytes() != expected
            ):
                raise MainRuntimeError("persisted turn request differs from reconstruction")
            if not result_path.exists():
                raise MainRuntimeError(
                    "incomplete persisted turn forbids automatic provider replay"
                )
            if result_path.is_symlink() or not result_path.is_file():
                raise MainRuntimeError("persisted turn result is not a regular file")
            return GatewayExecutionResult.model_validate_json(result_path.read_bytes())
        if result_path.exists():
            raise MainRuntimeError("turn result exists without its immutable request")
        publish_immutable_file(request_path, expected)
        return None

    def complete_turn(
        self,
        logical_request_id: str,
        turn_ordinal: int,
        result: GatewayExecutionResult,
    ) -> None:
        turn_root = self._logical_root(logical_request_id) / f"turn-{turn_ordinal:02d}"
        request_path = turn_root / "request.json"
        if not request_path.is_file() or request_path.is_symlink():
            raise MainRuntimeError("turn result cannot precede its immutable request")
        publish_immutable_file(turn_root / "result.json", _serialized(result))

    def publish_terminal(self, terminal: MainLogicalTerminal) -> None:
        publish_immutable_file(
            self._logical_root(terminal.logical_request_id) / "terminal.json",
            _serialized(terminal),
        )


def _turn_receipt(
    *,
    logical: DiagnosisMainExpectedRequest,
    turn_ordinal: int,
    turn_kind: TurnKind,
    context: ModelVisibleEvidenceContext,
    request: GatewayRequest,
    result: GatewayExecutionResult,
    selected_ids: Sequence[str],
    original_ids: Sequence[str],
) -> MainTurnReceipt:
    selected = tuple(selected_ids)
    omitted = tuple(item for item in original_ids if item not in set(selected))
    payload = {
        "schema_version": TURN_RECEIPT_SCHEMA_VERSION,
        "logical_request_id": logical.request_id,
        "logical_request_sha256": logical.request_sha256,
        "turn_ordinal": turn_ordinal,
        "turn_kind": turn_kind,
        "visible_context_sha256": context.context_sha256,
        "gateway_request_identity_sha256": request.initial_attempt.request_identity_sha256,
        "gateway_result_sha256": canonical_execution_sha256(result.model_dump(mode="json")),
        "gateway_status": result.status,
        "selected_evidence_ids": selected,
        "omitted_evidence_ids": omitted,
    }
    return MainTurnReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


def _logical_terminal(
    *,
    logical: DiagnosisMainExpectedRequest,
    variant: str,
    status: LogicalStatus,
    receipts: Sequence[MainTurnReceipt],
    final_context_sha256: str,
    final_raw_sha256: str | None,
    deterministic_payload: dict[str, object] | None,
    issue_code: str | None,
) -> MainLogicalTerminal:
    payload = {
        "schema_version": LOGICAL_TERMINAL_SCHEMA_VERSION,
        "logical_request_id": logical.request_id,
        "logical_request_sha256": logical.request_sha256,
        "context_id": logical.context_id,
        "variant": variant,
        "status": status,
        "expected_provider_turn_count": len(_ROUTE_TURNS[variant]),
        "completed_provider_turn_count": len(receipts),
        "turn_receipts": tuple(item.model_dump(mode="json") for item in receipts),
        "final_visible_context_sha256": final_context_sha256,
        "final_raw_response_sha256": final_raw_sha256,
        "deterministic_payload": deterministic_payload,
        "issue_code": issue_code,
    }
    return MainLogicalTerminal.model_validate(
        {
            **payload,
            "turn_receipts": tuple(receipts),
            "terminal_sha256": canonical_execution_sha256(payload),
        }
    )


def _execute_turn(
    *,
    logical: DiagnosisMainExpectedRequest,
    ordinal: int,
    kind: TurnKind,
    request: GatewayRequest,
    context: ModelVisibleEvidenceContext,
    selected_ids: Sequence[str],
    original_ids: Sequence[str],
    store: MainRuntimeStore,
    adapter: ProviderAdapter,
    clock: Clock,
    cancellation: CancellationProbe,
) -> tuple[GatewayExecutionResult, MainTurnReceipt]:
    resumed = store.begin_or_resume_turn(logical.request_id, ordinal, request)
    result = resumed or execute_gateway_request(
        request,
        adapter=adapter,
        clock=clock,
        cancellation=cancellation,
    )
    if resumed is None:
        store.complete_turn(logical.request_id, ordinal, result)
    receipt = _turn_receipt(
        logical=logical,
        turn_ordinal=ordinal,
        turn_kind=kind,
        context=context,
        request=request,
        result=result,
        selected_ids=selected_ids,
        original_ids=original_ids,
    )
    return result, receipt


def run_main_logical_request(
    *,
    logical: DiagnosisMainExpectedRequest,
    logical_context: DiagnosisMainContext,
    family: DiagnosisMainFamily,
    visible_context: ModelVisibleEvidenceContext,
    contract: MainRuntimeContract,
    authority: MainExecutionAuthority,
    response_contract: Mapping[str, object],
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    store: MainRuntimeStore,
    adapter: ProviderAdapter | None,
    clock: Clock,
    cancellation: CancellationProbe | None = None,
) -> MainLogicalTerminal:
    """Execute one exact logical route or return its immutable terminal replay."""

    existing = store.terminal(logical.request_id)
    if existing is not None:
        if (
            existing.logical_request_sha256 != logical.request_sha256
            or existing.context_id != logical.context_id
            or existing.variant != logical.variant
        ):
            raise MainRuntimeError("persisted terminal differs from the frozen request")
        return existing
    if logical.context_id != logical_context.context_id:
        raise MainRuntimeError("logical request and evaluator context differ")
    if visible_context.context_id != logical_context.context_id:
        raise MainRuntimeError("model-visible context and evaluator binding differ")
    manifest = _manifest(authority, contract)
    original_ids = _all_original_ids(visible_context)
    if logical.variant == "B0":
        deterministic: dict[str, object] = {
            "schema_version": "diagnosis-main-deterministic-output/v1",
            "rule_trace": [
                {
                    "evidence_id": item.evidence_id,
                    "content_sha256": item.content_sha256,
                }
                for item in visible_context.items
            ],
            "bounded_result": "observed_records_listed_without_causal_inference",
        }
        terminal = _logical_terminal(
            logical=logical,
            variant="B0",
            status="deterministic_completed",
            receipts=(),
            final_context_sha256=visible_context.context_sha256,
            final_raw_sha256=None,
            deterministic_payload=deterministic,
            issue_code=None,
        )
        store.publish_terminal(terminal)
        return terminal
    if adapter is None:
        raise MainRuntimeError("provider-backed route requires an explicit adapter")
    variant: ProviderVariant = logical.variant
    prompt_contracts = response_contract.get("prompt_contracts")
    if not isinstance(prompt_contracts, dict) or not isinstance(prompt_contracts.get(variant), str):
        raise MainRuntimeError("response contract has no prompt for the route")
    base_prompt = cast(str, prompt_contracts[variant])
    cancellation_probe = cancellation or NeverCancelled()
    receipts: list[MainTurnReceipt] = []
    selection_ids: tuple[str, ...] = ()
    prior_request_hashes: list[str] = []

    selection_turns = len(_ROUTE_TURNS[variant]) - 1
    for ordinal in range(1, selection_turns + 1):
        selection_context = (
            _catalog_context(visible_context)
            if variant in {"B2", "FULL"}
            else _graph_context(
                visible_context,
                original_ids,
                full=False,
                prior_turn_hashes=(),
            )
        )
        prompt = (
            f"{base_prompt}\n\nRetrieval turn {ordinal}. Select one or more exact "
            "evidence IDs from the supplied catalog. Return only the registered "
            "selection JSON. Do not diagnose yet."
        )
        request = _gateway_request(
            manifest=manifest,
            family=family,
            logical=logical,
            logical_context=logical_context,
            provider_context=selection_context,
            variant=variant,
            turn_ordinal=ordinal,
            turn_kind="selection",
            prompt=prompt,
            response_schema=_selection_schema(original_ids),
            contract=contract,
            freeze=fairness_freeze,
        )
        result, receipt = _execute_turn(
            logical=logical,
            ordinal=ordinal,
            kind="selection",
            request=request,
            context=selection_context,
            selected_ids=selection_ids,
            original_ids=original_ids,
            store=store,
            adapter=adapter,
            clock=clock,
            cancellation=cancellation_probe,
        )
        receipts.append(receipt)
        prior_request_hashes.append(request.initial_attempt.request_identity_sha256)
        try:
            selection = _validate_selection(result, available_ids=original_ids)
        except (MainRuntimeError, ValidationError):
            terminal = _logical_terminal(
                logical=logical,
                variant=variant,
                status="selection_failure",
                receipts=receipts,
                final_context_sha256=selection_context.context_sha256,
                final_raw_sha256=(
                    result.raw_response.content_sha256 if result.raw_response is not None else None
                ),
                deterministic_payload=None,
                issue_code="selection_turn_failed_closed",
            )
            store.publish_terminal(terminal)
            return terminal
        selection_ids = tuple(
            evidence_id
            for evidence_id in original_ids
            if evidence_id in set((*selection_ids, *selection.requested_evidence_ids))
        )

    if selection_turns == 0:
        selection_ids = original_ids
    if variant in {"B1", "B2"}:
        final_context = _plain_context(visible_context, selection_ids)
    elif variant == "CodeGraph":
        final_context = _graph_context(
            visible_context,
            selection_ids,
            full=False,
            prior_turn_hashes=prior_request_hashes,
        )
    elif variant == "FULL":
        final_context = _graph_context(
            visible_context,
            selection_ids,
            full=True,
            prior_turn_hashes=prior_request_hashes,
        )
    else:
        final_context = visible_context
    final_ordinal = selection_turns + 1
    prompt = (
        f"{base_prompt}\n\nFinal diagnosis turn. Use only the supplied final "
        "projection and return exactly one registered diagnosis JSON object."
    )
    final_request = _gateway_request(
        manifest=manifest,
        family=family,
        logical=logical,
        logical_context=logical_context,
        provider_context=final_context,
        variant=variant,
        turn_ordinal=final_ordinal,
        turn_kind="final",
        prompt=prompt,
        response_schema=_final_schema(response_contract),
        contract=contract,
        freeze=fairness_freeze,
    )
    final_result, final_receipt = _execute_turn(
        logical=logical,
        ordinal=final_ordinal,
        kind="final",
        request=final_request,
        context=final_context,
        selected_ids=selection_ids,
        original_ids=original_ids,
        store=store,
        adapter=adapter,
        clock=clock,
        cancellation=cancellation_probe,
    )
    receipts.append(final_receipt)
    if final_result.status != "parsed" or final_result.raw_response is None:
        terminal = _logical_terminal(
            logical=logical,
            variant=variant,
            status="technical_failure",
            receipts=receipts,
            final_context_sha256=final_context.context_sha256,
            final_raw_sha256=(
                final_result.raw_response.content_sha256
                if final_result.raw_response is not None
                else None
            ),
            deterministic_payload=None,
            issue_code=f"gateway_{final_result.status}",
        )
        store.publish_terminal(terminal)
        return terminal
    try:
        raw_text = final_result.raw_response.content.decode("utf-8", errors="strict")
        validate_main_provider_output(
            raw_text,
            variant=variant,
            visible_evidence_ids={item.evidence_id for item in final_context.items},
        )
    except (UnicodeDecodeError, DiagnosisMainResponseError, ValidationError, ValueError):
        terminal = _logical_terminal(
            logical=logical,
            variant=variant,
            status="semantic_failure",
            receipts=receipts,
            final_context_sha256=final_context.context_sha256,
            final_raw_sha256=final_result.raw_response.content_sha256,
            deterministic_payload=None,
            issue_code="variant_semantic_validation_failed",
        )
        store.publish_terminal(terminal)
        return terminal
    terminal = _logical_terminal(
        logical=logical,
        variant=variant,
        status="completed",
        receipts=receipts,
        final_context_sha256=final_context.context_sha256,
        final_raw_sha256=final_result.raw_response.content_sha256,
        deterministic_payload=None,
        issue_code=None,
    )
    store.publish_terminal(terminal)
    return terminal


def load_main_runtime_inputs(
    root: Path,
) -> tuple[MainRuntimeContract, DiagnosisVariantFairnessFreeze, dict[str, object]]:
    contract = load_main_runtime_contract(
        root / "configs/evaluation/diagnosis_main_runtime_contract.json"
    )
    freeze = load_diagnosis_variant_freeze(
        root / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
    )
    response_path = root / "configs/evaluation/diagnosis_main_response_contract.json"
    if response_path.is_symlink() or not response_path.is_file():
        raise MainRuntimeError("main response contract is unavailable")
    response = json.loads(response_path.read_text(encoding="utf-8"))
    if (
        not isinstance(response, dict)
        or response.get("contract_sha256") != contract.response_contract_sha256
    ):
        raise MainRuntimeError("main response contract differs from runtime binding")
    return contract, freeze, cast(dict[str, object], response)


__all__ = [
    "MainExecutionAuthority",
    "MainLogicalTerminal",
    "MainRuntimeContract",
    "MainRuntimeError",
    "MainRuntimePreflight",
    "MainRuntimeStore",
    "NeverCancelled",
    "build_main_runtime_preflight",
    "load_main_runtime_contract",
    "load_main_runtime_inputs",
    "run_main_logical_request",
]
