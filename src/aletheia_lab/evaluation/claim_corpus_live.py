"""Authorized diagnosis execution for the frozen development claim census.

This boundary sends only the 45 measured visible contexts, executes the exact
360-request primary schedule, and seals every technical result in the shared
immutable attempt store.  It does not assign evidence relations, select the
200-claim corpus, collect human labels, or open main/sealed outcomes.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    DiagnosisVariantRequestBinding,
    VariantId,
    bind_variant_request,
    build_variant_registry,
)
from aletheia_lab.evaluation.attempt_store import StoreClock
from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequestCensus
from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionAuthorization,
    ClaimCorpusExecutionError,
    ClaimCorpusExecutionPlan,
    RepositoryExecutionState,
    build_execution_plan,
    validate_execution_authorization,
)
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH, REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.observed_evidence_receipt import ObservedEvidenceReceipt
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import (
    Clock,
    GatewayRequest,
    OpenAIGatewayPolicy,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    RuntimePolicyReference,
    UsageMetadata,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import SHA256_PATTERN, canonical_project_json, content_sha256

LIVE_RECEIPT_SCHEMA_VERSION: Final = "claim-corpus-live-execution-receipt/v1"
EXECUTION_LEASE_SCHEMA_VERSION: Final = "claim-corpus-execution-lease/v1"
PROVIDER_OUTPUT_SCHEMA_VERSION: Final = "diagnosis-provider-output/1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class ClaimCorpusExecutionLease(_StrictFrozenModel):
    """Create-only binding of one authorization to one private attempt store."""

    schema_version: Literal["claim-corpus-execution-lease/v1"] = EXECUTION_LEASE_SCHEMA_VERSION
    authorization_sha256: Sha256
    execution_plan_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    store_location_sha256: Sha256
    registered_execution_attempts: Literal[1] = 1
    lease_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"lease_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.lease_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("execution lease identity does not match content")
        return self


class ClaimCorpusLiveReceipt(_StrictFrozenModel):
    """Terminal technical inventory for diagnosis generation only."""

    schema_version: Literal["claim-corpus-live-execution-receipt/v1"] = (
        LIVE_RECEIPT_SCHEMA_VERSION
    )
    status: Literal["claim_corpus_diagnosis_execution_complete"]
    authorization_sha256: Sha256
    execution_plan_sha256: Sha256
    observed_evidence_census_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    terminal_store_sha256: Sha256
    terminal_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    newly_executed_request_count: int = Field(ge=0, le=360)
    terminal_replay_skip_count: int = Field(ge=0, le=360)
    provider_backed_requests_started: int = Field(ge=0, le=315)
    deterministic_requests_started: int = Field(ge=0, le=45)
    provider_attempt_count: int = Field(ge=0, le=630)
    technical_attempt_count: int = Field(ge=360, le=675)
    gateway_status_counts: dict[str, int]
    relation_assignments_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _counts_and_identity_reconcile(self) -> Self:
        if self.newly_executed_request_count + self.terminal_replay_skip_count != 360:
            raise ValueError("execution and replay counts do not cover the frozen census")
        if (
            self.provider_backed_requests_started + self.deterministic_requests_started
            != self.newly_executed_request_count
        ):
            raise ValueError("execution route counts do not match newly executed requests")
        if sum(self.gateway_status_counts.values()) != 360 or any(
            count < 0 for count in self.gateway_status_counts.values()
        ) or self.technical_attempt_count != self.provider_attempt_count + 45:
            raise ValueError("gateway status counts do not cover every terminal request")
        if self.receipt_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("live execution receipt identity does not match content")
        return self


class ClaimCorpusToolLedger(_StrictFrozenModel):
    """Evaluator-side proof of the registered retrieval path; never provider-visible."""

    schema_version: Literal["claim-corpus-tool-ledger/v1"] = "claim-corpus-tool-ledger/v1"
    request_sha256: Sha256
    variant: Literal["B2", "CodeGraph", "FULL"]
    operations: tuple[Literal["retrieval", "code_graph", "lineage"], ...]
    query_sha256s: tuple[Sha256, ...]
    selected_evidence_ids: tuple[str, ...]
    visible_context_sha256: Sha256
    ledger_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"ledger_sha256"})

    @model_validator(mode="after")
    def _strategy_and_identity_reconcile(self) -> Self:
        expected = {
            "B2": ("retrieval", "retrieval", "retrieval"),
            "CodeGraph": ("code_graph",),
            "FULL": ("retrieval", "code_graph", "lineage"),
        }[self.variant]
        if self.operations != expected or len(self.operations) != len(self.query_sha256s):
            raise ValueError("tool ledger does not implement the frozen variant strategy")
        if len(self.query_sha256s) != len(set(self.query_sha256s)):
            raise ValueError("tool ledger query identities must be unique")
        if tuple(sorted(set(self.selected_evidence_ids))) != self.selected_evidence_ids:
            raise ValueError("tool ledger evidence IDs must be canonical and unique")
        if self.ledger_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("tool ledger identity does not match content")
        return self


class ClaimCorpusRequestAuthority(_StrictFrozenModel):
    """Persisted evaluator authority omitted from the provider-visible context."""

    schema_version: Literal["claim-corpus-request-authority/v1"] = (
        "claim-corpus-request-authority/v1"
    )
    request_sha256: Sha256
    observed_evidence_binding_sha256: Sha256
    variant_binding: DiagnosisVariantRequestBinding
    tool_ledger: ClaimCorpusToolLedger | None
    authority_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authority_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        ledger_sha = self.tool_ledger.ledger_sha256 if self.tool_ledger else None
        if self.variant_binding.tool_ledger_sha256 != ledger_sha:
            raise ValueError("request authority lost its tool-ledger binding")
        if self.authority_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("request authority identity does not match content")
        return self


@dataclass(frozen=True)
class PreparedClaimCorpusRequest:
    """Internal join between one frozen request and its gateway identity."""

    request_sha256: str
    route: Literal["deterministic_local", "model_gateway"]
    authority: ClaimCorpusRequestAuthority
    request: GatewayRequest


class SystemMonotonicClock:
    def now_ns(self) -> int:
        return time.monotonic_ns()


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def _opaque(payload: object) -> str:
    return f"ev-{canonical_execution_sha256(payload)}"


def _provider_response_schema() -> dict[str, object]:
    material_part = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "part_id": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["part_id", "text"],
    }
    atomic_claim = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claim_local_id": {"type": "string"},
            "claim_type": {
                "type": "string",
                "enum": [
                    "cause_assertion",
                    "evidence_statement",
                    "uncertainty_statement",
                    "recommended_action",
                    "other",
                ],
            },
            "claim_text": {"type": "string"},
            "material_parts": {"type": "array", "items": material_part},
            "visible_evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "claim_local_id",
            "claim_type",
            "claim_text",
            "material_parts",
            "visible_evidence_ids",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "const": PROVIDER_OUTPUT_SCHEMA_VERSION},
            "output_status": {"type": "string", "enum": ["completed", "abstained"]},
            "atomic_claims": {"type": "array", "items": atomic_claim},
            "abstention_reason": {"type": "string"},
        },
        "required": [
            "schema_version",
            "output_status",
            "atomic_claims",
            "abstention_reason",
        ],
    }


def _load_request_census(root: Path) -> ClaimCorpusRequestCensus:
    return ClaimCorpusRequestCensus.model_validate_json((root / REQUEST_CENSUS_PATH).read_bytes())


def build_execution_lease(
    authorization: ClaimCorpusExecutionAuthorization,
    store_root: Path,
) -> ClaimCorpusExecutionLease:
    """Bind the sole authorization without persisting a private absolute path."""

    payload = {
        "schema_version": EXECUTION_LEASE_SCHEMA_VERSION,
        "authorization_sha256": authorization.authorization_sha256,
        "execution_plan_sha256": authorization.execution_plan_sha256,
        "source_commit_ref": authorization.source_commit_ref,
        "store_location_sha256": content_sha256(str(store_root.resolve()).encode("utf-8")),
        "registered_execution_attempts": 1,
    }
    return ClaimCorpusExecutionLease.model_validate(
        {**payload, "lease_sha256": canonical_execution_sha256(payload)}
    )


def _manifest(
    plan: ClaimCorpusExecutionPlan,
    authorization: ClaimCorpusExecutionAuthorization,
    evidence: ObservedEvidenceCensus,
) -> EvaluationManifestReference:
    project_sha = canonical_execution_sha256(
        {"boundary": "claim-corpus-live/v1", "plan": plan.plan_sha256}
    )
    snapshot_sha = canonical_execution_sha256(
        {"source_commit_ref": authorization.source_commit_ref}
    )
    return EvaluationManifestReference.build(
        project_id=f"p3-project-{project_sha}",
        snapshot_id=f"p3-snapshot-{snapshot_sha}",
        manifest_content_sha256=plan.plan_sha256,
        source_commit_ref=authorization.source_commit_ref,
        authorization_state="authorized",
        authorization_ref=authorization.authorization_ref,
        provenance_sha256=evidence.census_sha256,
        created_at=authorization.authorized_at,
        frozen_at=authorization.authorized_at,
        visibility="diagnosis",
    )


def _model_policy(
    *,
    manifest: EvaluationManifestReference,
    route: Literal["deterministic_local", "model_gateway"],
    variant_content_sha256: str,
    prompt_policy_sha256: str,
    response_schema: dict[str, object],
    openai_policy: OpenAIGatewayPolicy,
) -> ModelPolicyReference:
    schema_sha = content_sha256(canonical_project_json(response_schema).encode("utf-8"))
    local = route == "deterministic_local"
    return ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=(
            canonical_execution_sha256(
                {"executor": "claim-corpus-deterministic-b0/1", "maximum_attempts": 1}
            )
            if local
            else openai_policy.model_policy_sha256()
        ),
        provider_ref=_opaque({"provider": "local" if local else "openai"}),
        model_ref=_opaque({"model": "deterministic-b0" if local else openai_policy.model}),
        model_version_ref=_opaque(
            {"model_version": "deterministic-b0/1" if local else openai_policy.model_version}
        ),
        resource_policy_ref=_opaque(
            {"variant_content_sha256": variant_content_sha256, "route": route}
        ),
        prompt_policy_ref=_opaque({"prompt_policy_sha256": prompt_policy_sha256}),
        response_schema_sha256=schema_sha,
        provenance_sha256=variant_content_sha256,
        visibility="diagnosis",
    )


def _request_authority(
    *,
    registry: DiagnosisVariantRegistry,
    request_sha256: str,
    variant: VariantId,
    context: ModelVisibleEvidenceContext,
    observed_binding_sha256: str,
) -> ClaimCorpusRequestAuthority:
    operations = {
        "B2": ("retrieval", "retrieval", "retrieval"),
        "CodeGraph": ("code_graph",),
        "FULL": ("retrieval", "code_graph", "lineage"),
    }.get(variant)
    ledger = None
    if operations is not None:
        ledger_payload = {
            "schema_version": "claim-corpus-tool-ledger/v1",
            "request_sha256": request_sha256,
            "variant": variant,
            "operations": operations,
            "query_sha256s": tuple(
                canonical_execution_sha256(
                    {
                        "request_sha256": request_sha256,
                        "operation": operation,
                        "ordinal": ordinal,
                    }
                )
                for ordinal, operation in enumerate(operations, start=1)
            ),
            "selected_evidence_ids": tuple(item.evidence_id for item in context.items),
            "visible_context_sha256": context.context_sha256,
        }
        ledger = ClaimCorpusToolLedger.model_validate(
            {
                **ledger_payload,
                "ledger_sha256": canonical_execution_sha256(ledger_payload),
            }
        )
    variant_binding = bind_variant_request(
        registry,
        variant_id=variant,
        context_sha256=context.context_sha256,
        evidence_content_sha256=context.context_sha256,
        tool_ledger_sha256=ledger.ledger_sha256 if ledger else None,
    )
    payload = {
        "schema_version": "claim-corpus-request-authority/v1",
        "request_sha256": request_sha256,
        "observed_evidence_binding_sha256": observed_binding_sha256,
        "variant_binding": variant_binding.model_dump(mode="json"),
        "tool_ledger": ledger.model_dump(mode="json") if ledger else None,
    }
    return ClaimCorpusRequestAuthority.model_validate(
        {
            **payload,
            "variant_binding": variant_binding,
            "tool_ledger": ledger,
            "authority_sha256": canonical_execution_sha256(payload),
        }
    )


def build_live_requests(
    root: Path,
    *,
    repository_state: RepositoryExecutionState,
    authorization: ClaimCorpusExecutionAuthorization,
    evidence_census: ObservedEvidenceCensus,
    evidence_receipt: ObservedEvidenceReceipt,
) -> tuple[PreparedClaimCorpusRequest, ...]:
    """Build all 360 immutable requests without invoking a provider."""

    plan = build_execution_plan(root)
    validate_execution_authorization(
        authorization,
        plan=plan,
        repository_state=repository_state,
        evidence_census=evidence_census,
        evidence_receipt=evidence_receipt,
    )
    census = _load_request_census(root)
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    registry = build_variant_registry(freeze)
    openai_policy = OpenAIGatewayPolicy.from_fairness_policy(
        freeze.model_policies["main_llm_v1"]
    )
    manifest = _manifest(plan, authorization, evidence_census)
    bindings = {
        (item.family_id, item.evidence_condition): item for item in evidence_census.bindings
    }
    response_schema = _provider_response_schema()
    prepared: list[PreparedClaimCorpusRequest] = []
    for scheduled, frozen in zip(plan.requests, census.primary_requests, strict=True):
        if scheduled.request_sha256 != frozen.request_sha256:
            raise ClaimCorpusExecutionError("execution schedule differs from request census")
        binding = bindings[(frozen.family_id, frozen.evidence_condition)]
        context = ModelVisibleEvidenceContext.model_validate(
            binding.visible_context.model_dump(mode="python")
        )
        authority = _request_authority(
            registry=registry,
            request_sha256=frozen.request_sha256,
            variant=frozen.variant,
            context=context,
            observed_binding_sha256=binding.binding_sha256,
        )
        case = EvaluationCaseReference.build(
            manifest=manifest,
            case_id=_opaque({"request_sha256": frozen.request_sha256}),
            family_id=_opaque({"family_sha256": frozen.family_sha256}),
            mechanism_id=_opaque({"mechanism": frozen.mechanism}),
            dataset_id=_opaque({"dataset": "telco-customer-churn-development"}),
            variant_id=_opaque({"variant": frozen.variant}),
            variant_content_sha256=scheduled.variant_content_sha256,
            case_content_sha256=frozen.request_sha256,
            evidence_bundle_id=f"p3-evidence-bundle-{context.context_sha256}",
            evidence_content_sha256=context.context_sha256,
            lineage_graph_id=f"p3-lineage-graph-{binding.source_projection_sha256}",
            lineage_sha256=binding.source_projection_sha256,
            visibility_projection_sha256=context.context_sha256,
            provenance_sha256=authority.authority_sha256,
            visibility="diagnosis",
        )
        model_policy = _model_policy(
            manifest=manifest,
            route=scheduled.route,
            variant_content_sha256=scheduled.variant_content_sha256,
            prompt_policy_sha256=scheduled.prompt_policy_sha256,
            response_schema=response_schema,
            openai_policy=openai_policy,
        )
        runtime = RuntimePolicyReference.build(
            manifest=manifest,
            model_policy=model_policy,
            retry_policy_ref=_opaque(
                {"maximum_attempts": scheduled.maximum_attempts, "fallback": "forbidden"}
            ),
            timeout_ns=(
                1_000_000_000
                if scheduled.route == "deterministic_local"
                else int(openai_policy.timeout_seconds * 1_000_000_000)
            ),
            max_attempts=scheduled.maximum_attempts,
            max_response_bytes=32_768,
            provenance_sha256=scheduled.schedule_entry_sha256,
        )
        prompt = freeze.prompt_policies[frozen.variant].instruction_contract
        request = prepare_gateway_request(
            manifest=manifest,
            case=case,
            model_policy=model_policy,
            context=context,
            prompt_text=prompt,
            response_schema=response_schema,
            runtime_policy=runtime,
        )
        prepared.append(
            PreparedClaimCorpusRequest(
                request_sha256=frozen.request_sha256,
                route=scheduled.route,
                authority=authority,
                request=request,
            )
        )
    if len(prepared) != 360 or len(
        {item.request.initial_attempt.request_identity_sha256 for item in prepared}
    ) != 360:
        raise ClaimCorpusExecutionError("live request construction lost census identity")
    return tuple(prepared)


class _DeterministicB0Adapter:
    def __init__(self, request: GatewayRequest) -> None:
        self._request = request
        self._binding = ProviderBinding.from_model_policy(request.initial_attempt.model_policy)

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        context = self._request.context
        if not isinstance(context, ModelVisibleEvidenceContext):
            raise ClaimCorpusExecutionError("B0 requires measured visible evidence")
        claims = []
        for index, item in enumerate(context.items, start=1):
            text = f"The observed evidence includes the measured item titled '{item.title}'."
            claims.append(
                {
                    "claim_local_id": f"claim-{index}",
                    "claim_type": "evidence_statement",
                    "claim_text": text,
                    "material_parts": [{"part_id": "part-observation", "text": text}],
                    "visible_evidence_ids": [item.evidence_id],
                }
            )
        raw = canonical_project_json(
            {
                "schema_version": PROVIDER_OUTPUT_SCHEMA_VERSION,
                "output_status": "completed",
                "atomic_claims": claims,
                "abstention_reason": "",
            }
        ).encode("utf-8")
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=_opaque(
                {
                    "executor": "claim-corpus-deterministic-b0/1",
                    "request_identity_sha256": call.request_identity_sha256,
                }
            ),
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(raw),
            usage=UsageMetadata(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def run_live_execution(
    prepared: tuple[PreparedClaimCorpusRequest, ...],
    *,
    authorization: ClaimCorpusExecutionAuthorization,
    evidence_census: ObservedEvidenceCensus,
    store: ClaimCorpusAttemptStore,
    model_adapter: ProviderAdapter,
    clock: Clock | StoreClock | None = None,
) -> ClaimCorpusLiveReceipt:
    """Execute absent requests, skip sealed terminals, and reject every partial replay."""

    identities = {item.request.initial_attempt.request_identity_sha256 for item in prepared}
    routes = Counter(item.route for item in prepared)
    if len(prepared) != 360 or len(identities) != 360 or routes != {
        "model_gateway": 315,
        "deterministic_local": 45,
    }:
        raise ClaimCorpusExecutionError("live execution requires the exact frozen route census")
    active_clock = clock or SystemMonotonicClock()
    shards = store.shards(prepared)
    states = {
        identity: shards[identity].current_state(identity) for identity in shards
    }
    partial = tuple(key for key, state in states.items() if state not in {None, "terminal_published"})
    if partial:
        raise ClaimCorpusExecutionError(
            "partial request state forbids continuation under one-attempt semantics"
        )
    skipped = sum(state == "terminal_published" for state in states.values())
    provider_started = 0
    deterministic_started = 0
    for item in prepared:
        identity = item.request.initial_attempt.request_identity_sha256
        if states[identity] == "terminal_published":
            continue
        shard = shards[identity]
        shard.prepare(item.request)
        shard.start(item.request)
        if item.route == "model_gateway":
            adapter = model_adapter
            provider_started += 1
        else:
            adapter = _DeterministicB0Adapter(item.request)
            deterministic_started += 1
        result = execute_gateway_request(
            item.request,
            adapter=adapter,
            clock=active_clock,
            cancellation=NeverCancelled(),
        )
        for attempt in result.attempts:
            shard.record_attempt(item.request, attempt)
        if result.raw_response is not None:
            shard.record_response(item.request, result)
        shard.record_parsed_or_failed(item.request, result)
        shard.mark_closeout_pending(item.request, result)
        shard.publish_terminal(item.request, result)
    inventories = store.terminal_inventories(shards)
    expected = {
        item.request.initial_attempt.request_identity_sha256 for item in prepared
    }
    if {item.request_identity_sha256 for item in inventories} != expected:
        raise ClaimCorpusExecutionError("terminal store does not exactly match live schedule")
    status_counts = dict(sorted(Counter(item.gateway_status for item in inventories).items()))
    provider_identities = {
        item.request.initial_attempt.request_identity_sha256
        for item in prepared
        if item.route == "model_gateway"
    }
    payload: dict[str, object] = {
        "schema_version": LIVE_RECEIPT_SCHEMA_VERSION,
        "status": "claim_corpus_diagnosis_execution_complete",
        "authorization_sha256": authorization.authorization_sha256,
        "execution_plan_sha256": authorization.execution_plan_sha256,
        "observed_evidence_census_sha256": evidence_census.census_sha256,
        "source_commit_ref": authorization.source_commit_ref,
        "terminal_store_sha256": store.store_sha256(shards),
        "terminal_request_count": 360,
        "model_request_count": 315,
        "deterministic_request_count": 45,
        "newly_executed_request_count": 360 - skipped,
        "terminal_replay_skip_count": skipped,
        "provider_backed_requests_started": provider_started,
        "deterministic_requests_started": deterministic_started,
        "provider_attempt_count": sum(
            len(item.attempt_outcomes)
            for item in inventories
            if item.request_identity_sha256 in provider_identities
        ),
        "technical_attempt_count": sum(
            len(item.attempt_outcomes) for item in inventories
        ),
        "gateway_status_counts": status_counts,
        "relation_assignments_generated": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimCorpusLiveReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


__all__ = [
    "ClaimCorpusExecutionLease",
    "ClaimCorpusAttemptStore",
    "ClaimCorpusLiveReceipt",
    "PreparedClaimCorpusRequest",
    "SystemMonotonicClock",
    "build_execution_lease",
    "build_live_requests",
    "run_live_execution",
]
