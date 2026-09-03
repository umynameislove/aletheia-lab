"""Outcome-blind preflight and rehearsal for claim-corpus execution.

This module deliberately stops before provider access.  It turns the frozen
primary census into one canonical execution schedule, proves the deterministic
versus model-backed split, and exposes unresolved live-run blockers without
manufacturing evidence or scientific outcomes.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    build_variant_registry,
)
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
)
from aletheia_lab.evaluation.claim_corpus_readiness import (
    FAIRNESS_PATH,
    REQUEST_CENSUS_PATH,
    verify_readiness,
)
from aletheia_lab.evaluation.claim_evidence_census import (
    ObservedEvidenceCensus,
    load_observed_evidence_census,
    validate_observed_evidence_census,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    load_evidence_semantics_policy,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.human_workflow import load_human_workflow
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.project.identity import SHA256_PATTERN

EXECUTION_PLAN_SCHEMA_VERSION: Final = "claim-corpus-execution-plan/v1"
EXECUTION_PREFLIGHT_SCHEMA_VERSION: Final = "claim-corpus-execution-preflight/v1"
EXECUTION_REHEARSAL_SCHEMA_VERSION: Final = "claim-corpus-execution-rehearsal/v1"
HUMAN_WORKFLOW_PATH: Final = "configs/evaluation/claim_support_human_workflow.json"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ExecutionRoute = Literal["deterministic_local", "model_gateway"]
ResumeState = Literal["absent", "terminal", "partial"]
ResumeAction = Literal["execute", "skip_terminal"]
LiveBlocker = Literal[
    "credential_missing",
    "observed_evidence_census_pending",
    "repository_not_clean_synchronized_main",
    "variant_execution_authorization_pending",
]


class ClaimCorpusExecutionError(ValueError):
    """Raised when the frozen execution boundary cannot be reconciled."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class RepositoryExecutionState(_StrictFrozenModel):
    """Minimal repository state needed by the one-attempt preflight."""

    branch: str
    head_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    origin_main_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None
    clean: bool

    @property
    def synchronized_main(self) -> bool:
        return (
            self.branch == "main"
            and self.clean
            and self.origin_main_commit is not None
            and self.head_commit == self.origin_main_commit
        )


class ScheduledClaimCorpusRequest(_StrictFrozenModel):
    """One frozen census request assigned to its only permitted execution route."""

    request_sha256: Sha256
    family_id: str
    evidence_condition: Literal["full", "missing_key", "noisy"]
    variant: Literal["A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL"]
    route: ExecutionRoute
    maximum_attempts: Literal[1, 2]
    variant_content_sha256: Sha256
    model_policy_sha256: Sha256 | None
    prompt_policy_sha256: Sha256
    schedule_entry_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"schedule_entry_sha256"})

    @model_validator(mode="after")
    def _route_and_identity_reconcile(self) -> Self:
        if self.route == "deterministic_local":
            if self.variant != "B0" or self.maximum_attempts != 1:
                raise ValueError("only B0 may use the deterministic local route")
            if self.model_policy_sha256 is not None:
                raise ValueError("the deterministic route cannot bind a model policy")
        elif self.variant == "B0" or self.maximum_attempts != 2 or self.model_policy_sha256 is None:
            raise ValueError("model-backed requests require the frozen two-attempt policy")
        if self.schedule_entry_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("schedule entry identity does not match canonical content")
        return self


class ClaimCorpusExecutionPlan(_StrictFrozenModel):
    """Canonical dry-run plan for the 360-request primary census."""

    schema_version: Literal["claim-corpus-execution-plan/v1"] = EXECUTION_PLAN_SCHEMA_VERSION
    readiness_plan_sha256: Sha256
    request_census_sha256: Sha256
    fairness_freeze_sha256: Sha256
    variant_registry_sha256: Sha256
    human_workflow_sha256: Sha256
    evidence_semantics_policy_sha256: Sha256
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_output_tokens_per_model_request: Literal[600]
    primary_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    reserve_request_count_scheduled: Literal[0]
    maximum_provider_attempts_per_request: Literal[2]
    relation_assignment_request_ceiling: Literal[1800]
    one_attempt_provider_call_ceiling: Literal[2115]
    retry_ceiling_provider_call_count: Literal[4230]
    generation_output_token_ceiling: Literal[189000]
    relation_output_token_ceiling: Literal[1080000]
    one_attempt_output_token_ceiling: Literal[1269000]
    retry_output_token_ceiling: Literal[2538000]
    requests: tuple[ScheduledClaimCorpusRequest, ...] = Field(min_length=360, max_length=360)
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    reserve_activation_performed: Literal[False] = False
    plan_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_sha256"})

    @model_validator(mode="after")
    def _census_and_identity_reconcile(self) -> Self:
        identities = tuple(item.request_sha256 for item in self.requests)
        if len(identities) != len(set(identities)):
            raise ValueError("execution plan contains duplicate requests")
        routes = Counter(item.route for item in self.requests)
        if routes != Counter({"model_gateway": 315, "deterministic_local": 45}):
            raise ValueError("execution route census differs from the frozen variants")
        if self.plan_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("execution plan identity does not match canonical content")
        return self


class ClaimCorpusExecutionPreflight(_StrictFrozenModel):
    """Public-safe preflight that cannot contain credentials or outcomes."""

    schema_version: Literal["claim-corpus-execution-preflight/v1"] = (
        EXECUTION_PREFLIGHT_SCHEMA_VERSION
    )
    status: Literal[
        "claim_corpus_rehearsal_ready_live_blocked",
        "claim_corpus_live_execution_ready",
    ]
    execution_plan_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    clean_synchronized_main: bool
    credential_environment_name: Literal["OPENAI_API_KEY"] = "OPENAI_API_KEY"
    credential_present: bool
    live_blockers: tuple[LiveBlocker, ...]
    primary_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    relation_assignment_request_ceiling: Literal[1800]
    reserve_request_count_scheduled: Literal[0]
    observed_evidence_context_count: Literal[0, 45]
    observed_evidence_census_sha256: Sha256 | None
    exact_input_token_count_known: Literal[False] = False
    exact_cost_estimate_available: Literal[False] = False
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    preflight_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preflight_sha256"})

    @model_validator(mode="after")
    def _status_and_identity_reconcile(self) -> Self:
        if tuple(sorted(set(self.live_blockers))) != self.live_blockers:
            raise ValueError("live blockers must be sorted and unique")
        if self.status == "claim_corpus_live_execution_ready" and self.live_blockers:
            raise ValueError("live-ready preflight cannot retain blockers")
        if self.status != "claim_corpus_live_execution_ready" and not self.live_blockers:
            raise ValueError("blocked preflight must name at least one blocker")
        if self.preflight_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("preflight identity does not match canonical content")
        return self


class ClaimCorpusExecutionRehearsal(_StrictFrozenModel):
    """Offline proof of the full scheduling and resume policy."""

    schema_version: Literal["claim-corpus-execution-rehearsal/v1"] = (
        EXECUTION_REHEARSAL_SCHEMA_VERSION
    )
    status: Literal["claim_corpus_execution_rehearsal_passed_live_blocked"]
    execution_plan_sha256: Sha256
    initial_execute_count: Literal[360]
    terminal_replay_skip_count: Literal[360]
    partial_state_rejected: Literal[True] = True
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    reserve_request_count_scheduled: Literal[0]
    raw_before_parse_required: Literal[True] = True
    terminal_record_required: Literal[True] = True
    output_driven_early_stop_forbidden: Literal[True] = True
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    rehearsal_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"rehearsal_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.rehearsal_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("rehearsal identity does not match canonical content")
        return self


def _load_census(root: Path) -> ClaimCorpusRequestCensus:
    try:
        return ClaimCorpusRequestCensus.model_validate_json(
            (root / REQUEST_CENSUS_PATH).read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimCorpusExecutionError("request census is unavailable or invalid") from exc


def _schedule_request(
    request: ClaimCorpusRequest,
    registry: DiagnosisVariantRegistry,
) -> ScheduledClaimCorpusRequest:
    variant = registry.require(request.variant)
    route: ExecutionRoute = (
        "model_gateway" if variant.capabilities.uses_model else "deterministic_local"
    )
    payload = {
        "request_sha256": request.request_sha256,
        "family_id": request.family_id,
        "evidence_condition": request.evidence_condition,
        "variant": request.variant,
        "route": route,
        "maximum_attempts": 2 if route == "model_gateway" else 1,
        "variant_content_sha256": variant.variant_content_sha256,
        "model_policy_sha256": variant.model_policy_sha256,
        "prompt_policy_sha256": variant.prompt_policy_sha256,
    }
    return ScheduledClaimCorpusRequest.model_validate(
        {
            **payload,
            "schedule_entry_sha256": canonical_execution_sha256(payload),
        }
    )


def build_execution_plan(root: Path) -> ClaimCorpusExecutionPlan:
    """Reconcile all frozen inputs and build the exact primary-only schedule."""

    checked_root = root.resolve()
    readiness = verify_readiness(checked_root)
    if not readiness.materialization_ready:
        raise ClaimCorpusExecutionError("claim-corpus materialization readiness is false")
    census = _load_census(checked_root)
    freeze = load_diagnosis_variant_freeze(checked_root / FAIRNESS_PATH)
    registry = build_variant_registry(freeze)
    workflow = load_human_workflow(checked_root, Path(HUMAN_WORKFLOW_PATH))
    semantics_policy = load_evidence_semantics_policy(checked_root)
    model_policy = freeze.model_policies.get("main_llm_v1")
    if model_policy is None:
        raise ClaimCorpusExecutionError("frozen main model policy is unavailable")
    expected_model = {
        "model": "gpt-4.1",
        "model_version": "gpt-4.1-2025-04-14",
        "max_output_tokens": 600,
        "provider_attempt_ceiling": 2,
    }
    if any(getattr(model_policy, key) != value for key, value in expected_model.items()):
        raise ClaimCorpusExecutionError("frozen model or execution budget changed")
    if freeze.execution_authorized or freeze.protected_outcomes_opened:
        raise ClaimCorpusExecutionError("fairness freeze unexpectedly opened execution")

    requests = tuple(_schedule_request(item, registry) for item in census.primary_requests)
    payload: dict[str, object] = {
        "schema_version": EXECUTION_PLAN_SCHEMA_VERSION,
        "readiness_plan_sha256": readiness.plan_sha256,
        "request_census_sha256": census.census_sha256,
        "fairness_freeze_sha256": registry.freeze_sha256,
        "variant_registry_sha256": registry.registry_sha256,
        "human_workflow_sha256": workflow.workflow_sha256,
        "evidence_semantics_policy_sha256": semantics_policy.policy_sha256,
        "model": "gpt-4.1",
        "model_snapshot": "gpt-4.1-2025-04-14",
        "maximum_output_tokens_per_model_request": 600,
        "primary_request_count": 360,
        "model_request_count": 315,
        "deterministic_request_count": 45,
        "reserve_request_count_scheduled": 0,
        "maximum_provider_attempts_per_request": 2,
        "relation_assignment_request_ceiling": 1800,
        "one_attempt_provider_call_ceiling": 2115,
        "retry_ceiling_provider_call_count": 4230,
        "generation_output_token_ceiling": 189000,
        "relation_output_token_ceiling": 1080000,
        "one_attempt_output_token_ceiling": 1269000,
        "retry_output_token_ceiling": 2538000,
        "requests": tuple(item.model_dump(mode="json") for item in requests),
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
        "reserve_activation_performed": False,
    }
    return ClaimCorpusExecutionPlan.model_validate(
        {**payload, "plan_sha256": canonical_execution_sha256(payload)}
    )


def inspect_repository_state(root: Path) -> RepositoryExecutionState:
    """Read only the Git facts needed for a clean-main execution gate."""

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def optional_git(*arguments: str) -> str | None:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return value or None

    try:
        return RepositoryExecutionState(
            branch=git("branch", "--show-current"),
            head_commit=git("rev-parse", "HEAD"),
            origin_main_commit=optional_git("rev-parse", "--verify", "origin/main"),
            clean=not bool(git("status", "--porcelain")),
        )
    except (OSError, subprocess.CalledProcessError, ValidationError) as exc:
        raise ClaimCorpusExecutionError("repository execution state is unavailable") from exc


def build_execution_preflight(
    root: Path,
    *,
    repository_state: RepositoryExecutionState,
    credential_present: bool,
    evidence_census: ObservedEvidenceCensus | None = None,
) -> ClaimCorpusExecutionPreflight:
    """Build a public-safe live-readiness receipt without reading a secret value."""

    plan = build_execution_plan(root)
    checked_evidence: ObservedEvidenceCensus | None = None
    if evidence_census is not None:
        try:
            checked_evidence = validate_observed_evidence_census(
                _load_census(root), evidence_census
            )
        except ClaimCorpusContractError as exc:
            raise ClaimCorpusExecutionError(
                "observed evidence census does not match the execution census"
            ) from exc
    blockers: list[LiveBlocker] = ["variant_execution_authorization_pending"]
    if checked_evidence is None:
        blockers.append("observed_evidence_census_pending")
    if not repository_state.synchronized_main:
        blockers.append("repository_not_clean_synchronized_main")
    if not credential_present:
        blockers.append("credential_missing")
    canonical_blockers = tuple(sorted(set(blockers)))
    payload: dict[str, object] = {
        "schema_version": EXECUTION_PREFLIGHT_SCHEMA_VERSION,
        "status": "claim_corpus_rehearsal_ready_live_blocked",
        "execution_plan_sha256": plan.plan_sha256,
        "source_commit_ref": repository_state.head_commit,
        "clean_synchronized_main": repository_state.synchronized_main,
        "credential_environment_name": "OPENAI_API_KEY",
        "credential_present": credential_present,
        "live_blockers": canonical_blockers,
        "primary_request_count": 360,
        "model_request_count": 315,
        "deterministic_request_count": 45,
        "relation_assignment_request_ceiling": 1800,
        "reserve_request_count_scheduled": 0,
        "observed_evidence_context_count": 45 if checked_evidence is not None else 0,
        "observed_evidence_census_sha256": (
            checked_evidence.census_sha256 if checked_evidence is not None else None
        ),
        "exact_input_token_count_known": False,
        "exact_cost_estimate_available": False,
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimCorpusExecutionPreflight.model_validate(
        {**payload, "preflight_sha256": canonical_execution_sha256(payload)}
    )


def load_execution_evidence_census(root: Path, path: Path) -> ObservedEvidenceCensus:
    """Load the evidence census used to clear the live execution blocker."""

    try:
        return load_observed_evidence_census(path, _load_census(root))
    except ClaimCorpusContractError as exc:
        raise ClaimCorpusExecutionError(
            "observed evidence census is unavailable or invalid"
        ) from exc


def plan_resume_actions(
    plan: ClaimCorpusExecutionPlan,
    states: Mapping[str, ResumeState],
) -> tuple[ResumeAction, ...]:
    """Plan idempotent continuation; partial requests never trigger another call."""

    unknown = set(states) - {item.request_sha256 for item in plan.requests}
    if unknown:
        raise ClaimCorpusExecutionError("resume state contains an unknown request")
    actions: list[ResumeAction] = []
    for request in plan.requests:
        state = states.get(request.request_sha256, "absent")
        if state == "partial":
            raise ClaimCorpusExecutionError(
                "partial request requires independent audit before any provider retry"
            )
        actions.append("skip_terminal" if state == "terminal" else "execute")
    return tuple(actions)


def rehearse_execution(root: Path) -> ClaimCorpusExecutionRehearsal:
    """Exercise the complete schedule and resume policy without provider access."""

    plan = build_execution_plan(root)
    initial = plan_resume_actions(plan, {})
    terminal = plan_resume_actions(
        plan,
        {item.request_sha256: "terminal" for item in plan.requests},
    )
    try:
        plan_resume_actions(plan, {plan.requests[0].request_sha256: "partial"})
    except ClaimCorpusExecutionError:
        partial_rejected = True
    else:  # pragma: no cover - invariant guard
        partial_rejected = False
    if Counter(initial) != Counter({"execute": 360}) or Counter(terminal) != Counter(
        {"skip_terminal": 360}
    ):
        raise ClaimCorpusExecutionError("rehearsal did not cover the complete census")
    payload: dict[str, object] = {
        "schema_version": EXECUTION_REHEARSAL_SCHEMA_VERSION,
        "status": "claim_corpus_execution_rehearsal_passed_live_blocked",
        "execution_plan_sha256": plan.plan_sha256,
        "initial_execute_count": 360,
        "terminal_replay_skip_count": 360,
        "partial_state_rejected": partial_rejected,
        "model_request_count": 315,
        "deterministic_request_count": 45,
        "reserve_request_count_scheduled": 0,
        "raw_before_parse_required": True,
        "terminal_record_required": True,
        "output_driven_early_stop_forbidden": True,
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
    }
    return ClaimCorpusExecutionRehearsal.model_validate(
        {**payload, "rehearsal_sha256": canonical_execution_sha256(payload)}
    )


def canonical_json_bytes(model: BaseModel) -> bytes:
    """Serialize a receipt without depending on mapping or hash iteration order."""

    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


__all__ = [
    "ClaimCorpusExecutionError",
    "ClaimCorpusExecutionPlan",
    "ClaimCorpusExecutionPreflight",
    "ClaimCorpusExecutionRehearsal",
    "RepositoryExecutionState",
    "ScheduledClaimCorpusRequest",
    "build_execution_plan",
    "build_execution_preflight",
    "canonical_json_bytes",
    "inspect_repository_state",
    "load_execution_evidence_census",
    "plan_resume_actions",
    "rehearse_execution",
]
