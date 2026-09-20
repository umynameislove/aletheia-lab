"""Validated execution-authority and persistence contracts for diagnosis main."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation.diagnosis_main_analysis import CONTROLLED_VARIANTS
from aletheia_lab.evaluation.diagnosis_main_census import DiagnosisMainPrivateCensusPacket
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.project.identity import SHA256_PATTERN

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
