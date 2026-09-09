"""Outcome-blind V2 diagnosis schedule and authentic relation-frame runtime.

This module derives every V2 frame from the tracked development evidence census.
It neither calls a provider nor admits a frame intent as a relation label.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ELIGIBLE_VARIANTS,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
    EvidenceCondition,
    Mechanism,
)
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import provider_response_schema_v2
from aletheia_lab.evaluation.claim_corpus_recovery_probe import PROBE_PROMPT
from aletheia_lab.evaluation.claim_evidence_census import (
    ObservedEvidenceCensus,
    load_observed_evidence_census,
)
from aletheia_lab.evaluation.claim_evidence_semantics import build_visible_evidence_item
from aletheia_lab.evaluation.claim_validation_v2 import (
    V2_PROTOCOL_PATH,
    ClaimSupportValidationV2Protocol,
    load_v2_protocol,
)
from aletheia_lab.evaluation.claim_validation_v2_relation_frames import (
    build_frame_capacity,
    build_source_frame_entries,
    build_v2_relation_frame_batch,
    build_visible_context,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime_contracts import (
    EVIDENCE_CENSUS_PATH,
    FRAME_ASSIGNMENT_ALGORITHM,
    REQUEST_CENSUS_PATH,
    RUNTIME_MANIFEST_PATH,
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    RUNTIME_READINESS_PATH,
    RUNTIME_READINESS_SCHEMA_VERSION,
    SCHEDULE_ALGORITHM,
    ClaimSupportValidationV2RuntimeManifest,
    ClaimSupportValidationV2RuntimeReadiness,
    ClaimValidationV2RuntimeError,
    ExecutionRoute,
    StrictFrozenModel,
    V2DiagnosisScheduleEntry,
    V2QualificationProbe,
    V2RelationFrameBatch,
    V2RuntimeSourceBinding,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import content_sha256

ModelT = TypeVar("ModelT", bound=StrictFrozenModel)

_RUNTIME_SOURCE_PATHS = (
    "scripts/claim_support_validation_v2_runtime.py",
    "src/aletheia_lab/evaluation/claim_validation_v2_frames.py",
    "src/aletheia_lab/evaluation/claim_validation_v2_relation_frames.py",
    "src/aletheia_lab/evaluation/claim_validation_v2_runtime.py",
    "src/aletheia_lab/evaluation/claim_validation_v2_runtime_contracts.py",
    "src/aletheia_lab/model_gateway/contracts.py",
    "src/aletheia_lab/model_gateway/openai.py",
    "src/aletheia_lab/model_gateway/openai_recovery.py",
    "src/aletheia_lab/model_gateway/runtime.py",
    "src/aletheia_lab/model_gateway/validation_v2.py",
)


def _load_inputs(
    root: Path,
) -> tuple[
    ClaimSupportValidationV2Protocol,
    ClaimCorpusRequestCensus,
    ObservedEvidenceCensus,
]:
    try:
        protocol = load_v2_protocol(root / V2_PROTOCOL_PATH)
        census = ClaimCorpusRequestCensus.model_validate_json(
            (root / REQUEST_CENSUS_PATH).read_bytes()
        )
        evidence = load_observed_evidence_census(root / EVIDENCE_CENSUS_PATH, census)
    except (OSError, ValidationError, ValueError) as exc:
        raise ClaimValidationV2RuntimeError("V2 runtime inputs are unavailable or invalid") from exc
    return protocol, census, evidence


def _request_key(request: ClaimCorpusRequest) -> tuple[Mechanism, int, EvidenceCondition, str]:
    try:
        order = int(request.family_id.rsplit("-", 1)[-1])
    except ValueError as exc:
        raise ClaimValidationV2RuntimeError("family order is not encoded canonically") from exc
    return request.mechanism, order, request.evidence_condition, request.variant


def _balanced_schedule(
    protocol: ClaimSupportValidationV2Protocol,
    census: ClaimCorpusRequestCensus,
    evidence: ObservedEvidenceCensus,
) -> tuple[V2DiagnosisScheduleEntry, ...]:
    requests = {_request_key(item): item for item in census.primary_requests}
    contexts = {
        (item.family_id, item.evidence_condition): item.visible_context.context_sha256
        for item in evidence.bindings
    }
    mechanisms: tuple[Mechanism, ...] = (
        "data_drift",
        "preprocessing_mismatch",
        "label_noise",
    )
    conditions: tuple[EvidenceCondition, ...] = ("full", "missing_key", "noisy")
    rounds: list[list[ClaimCorpusRequest]] = []
    for round_index in range(15):
        current: list[ClaimCorpusRequest] = []
        for mechanism_index, mechanism in enumerate(mechanisms):
            for variant_index, variant in enumerate(ELIGIBLE_VARIANTS):
                cell_index = mechanism_index * len(ELIGIBLE_VARIANTS) + variant_index
                family_order = (round_index + cell_index) % 5 + 1
                condition = conditions[
                    (round_index // 5 + variant_index + 2 * mechanism_index) % 3
                ]
                current.append(requests[(mechanism, family_order, condition, variant)])
        current.sort(
            key=lambda item: canonical_execution_sha256(
                {
                    "algorithm": SCHEDULE_ALGORITHM,
                    "protocol_sha256": protocol.protocol_sha256,
                    "schedule_round": round_index + 1,
                    "source_request_sha256": item.request_sha256,
                }
            )
        )
        rounds.append(current)
    ordered = tuple(item for current in rounds for item in current)
    result: list[V2DiagnosisScheduleEntry] = []
    for sequence, request in enumerate(ordered, start=1):
        round_index = (sequence - 1) // 24 + 1
        route: ExecutionRoute = (
            "deterministic_local" if request.variant == "B0" else "model_gateway"
        )
        context_sha = contexts[(request.family_id, request.evidence_condition)]
        identity = {
            "protocol_sha256": protocol.protocol_sha256,
            "sequence": sequence,
            "schedule_round": round_index,
            "source_request_sha256": request.request_sha256,
            "family_id": request.family_id,
            "family_sha256": request.family_sha256,
            "mechanism": request.mechanism,
            "family_order": int(request.family_id.rsplit("-", 1)[-1]),
            "evidence_condition": request.evidence_condition,
            "variant": request.variant,
            "execution_route": route,
            "visible_context_sha256": context_sha,
        }
        result.append(
            V2DiagnosisScheduleEntry.model_validate(
                {**identity, "v2_request_sha256": canonical_execution_sha256(identity)}
            )
        )
    return tuple(result)


def _validate_round_balance(schedule: Sequence[V2DiagnosisScheduleEntry]) -> None:
    if len(schedule) != 360:
        raise ValueError("V2 schedule must contain exactly 360 entries")
    for round_index in range(1, 16):
        entries = tuple(item for item in schedule if item.schedule_round == round_index)
        if (
            len(entries) != 24
            or Counter(item.mechanism for item in entries)
            != {"data_drift": 8, "preprocessing_mismatch": 8, "label_noise": 8}
            or Counter(item.evidence_condition for item in entries)
            != {"full": 8, "missing_key": 8, "noisy": 8}
            or Counter(item.variant for item in entries)
            != {variant: 3 for variant in ELIGIBLE_VARIANTS}
            or max(Counter(item.family_order for item in entries).values())
            - min(Counter(item.family_order for item in entries).values())
            > 1
        ):
            raise ValueError("V2 schedule round is not balanced across frozen strata")


def _qualification_probes(
    protocol: ClaimSupportValidationV2Protocol,
    schedule: Sequence[V2DiagnosisScheduleEntry],
    evidence: ObservedEvidenceCensus,
) -> tuple[V2QualificationProbe, ...]:
    variants = ("A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")
    result = []
    for variant in variants:
        source = next(item for item in schedule if item.variant == variant)
        original = next(binding.visible_context for binding in evidence.bindings
                        if binding.visible_context.context_sha256 == source.visible_context_sha256)
        synthetic = build_visible_context(tuple(build_visible_evidence_item(
            evidence_id=item.evidence_id, kind=item.kind, title="Synthetic counter",
            content="The synthetic counter is 7.",
            source_content_sha256=canonical_execution_sha256({"synthetic_counter": 7}),
        ) for item in original.items))
        schema_json = json.dumps(provider_response_schema_v2(
            tuple(item.evidence_id for item in synthetic.items)),
            sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        payload = {
            "protocol_sha256": protocol.protocol_sha256,
            "variant": variant,
            "source_schedule_entry_sha256": source.v2_request_sha256,
            "prompt_text": PROBE_PROMPT,
            "response_schema_json": schema_json,
            "context": synthetic.model_dump(mode="json"),
            "model_snapshot": "gpt-4.1-2025-04-14",
            "maximum_output_tokens": 2048,
            "synthetic_only": True,
            "admitted_to_corpus": False,
        }
        result.append(
            V2QualificationProbe.model_validate(
                {
                    **{key: value for key, value in payload.items() if key != "protocol_sha256"},
                    "context": synthetic,
                    "qualification_request_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    return tuple(result)


def build_v2_runtime_manifest(root: Path) -> ClaimSupportValidationV2RuntimeManifest:
    """Rebuild the full zero-provider V2 runtime census from frozen inputs."""

    root = root.resolve()
    protocol, census, evidence = _load_inputs(root)
    schedule = _balanced_schedule(protocol, census, evidence)
    _validate_round_balance(schedule)
    frames = build_source_frame_entries(
        {item.family_id: item.mechanism for item in census.primary_requests}, evidence
    )
    capacity = build_frame_capacity(frames, schedule)
    probes = _qualification_probes(protocol, schedule, evidence)
    try:
        sources = tuple(
            V2RuntimeSourceBinding(
                relative_path=relative,
                content_sha256=content_sha256((root / relative).read_bytes()),
            )
            for relative in _RUNTIME_SOURCE_PATHS
        )
    except OSError as exc:
        raise ClaimValidationV2RuntimeError("V2 runtime implementation source is unavailable") from exc
    policy = protocol.reliability_policy
    model_payload: dict[str, object] = {
        "schema_version": RUNTIME_MANIFEST_SCHEMA_VERSION,
        "status": "v2_runtime_frozen_qualification_authorization_pending",
        "protocol_sha256": protocol.protocol_sha256,
        "request_census_sha256": census.census_sha256,
        "evidence_census_sha256": evidence.census_sha256,
        "schedule_algorithm": SCHEDULE_ALGORITHM,
        "frame_assignment_algorithm": FRAME_ASSIGNMENT_ALGORITHM,
        "diagnosis_schedule": schedule,
        "source_frames": frames,
        "frame_capacity": capacity,
        "qualification_probes": probes,
        "implementation_sources": sources,
        "minimum_provider_start_interval_ms": policy.minimum_provider_start_interval_ms,
        "retry_initial_backoff_ms": policy.retry_initial_backoff_ms,
        "retry_backoff_multiplier": policy.retry_backoff_multiplier,
        "retry_backoff_ceiling_ms": policy.retry_backoff_ceiling_ms,
        "retry_after_ceiling_ms": policy.retry_after_ceiling_ms,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    identity_payload = {
        **model_payload,
        "diagnosis_schedule": tuple(item.model_dump(mode="json") for item in schedule),
        "source_frames": tuple(item.model_dump(mode="json") for item in frames),
        "frame_capacity": tuple(item.model_dump(mode="json") for item in capacity),
        "qualification_probes": tuple(item.model_dump(mode="json") for item in probes),
        "implementation_sources": tuple(item.model_dump(mode="json") for item in sources),
    }
    return ClaimSupportValidationV2RuntimeManifest.model_validate(
        {
            **model_payload,
            "manifest_sha256": canonical_execution_sha256(identity_payload),
        }
    )


def build_v2_runtime_readiness(
    manifest: ClaimSupportValidationV2RuntimeManifest,
) -> ClaimSupportValidationV2RuntimeReadiness:
    """Summarize the exact offline gate without granting provider authority."""

    checked = ClaimSupportValidationV2RuntimeManifest.model_validate(
        manifest.model_dump(mode="python")
    )
    payload: dict[str, object] = {
        "schema_version": RUNTIME_READINESS_SCHEMA_VERSION,
        "status": "claim_support_validation_v2_runtime_ready_qualification_pending",
        "protocol_sha256": checked.protocol_sha256,
        "runtime_manifest_sha256": checked.manifest_sha256,
        "diagnosis_request_count": 360,
        "provider_backed_diagnosis_request_count": sum(
            item.execution_route == "model_gateway" for item in checked.diagnosis_schedule
        ),
        "deterministic_diagnosis_request_count": sum(
            item.execution_route == "deterministic_local" for item in checked.diagnosis_schedule
        ),
        "relation_request_ceiling": 1440,
        "qualification_request_count": len(checked.qualification_probes),
        "source_frame_context_count": len(checked.source_frames),
        "all_source_frames_meet_structural_family_minimum": all(
            item.family_count >= 10 for item in checked.frame_capacity
        ),
        "all_source_frames_meet_structural_scheduled_cell_minimum": all(
            item.scheduled_diagnosis_cell_count >= 25 for item in checked.frame_capacity
        ),
        "balanced_round_count": len(
            {item.schedule_round for item in checked.diagnosis_schedule}
        ),
        "safe_failure_taxonomy_bound": True,
        "retry_and_pacing_policy_bound": True,
        "source_claim_expressiveness_review_required": True,
        "live_qualification_authorized": False,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimSupportValidationV2RuntimeReadiness.model_validate(
        {**payload, "readiness_sha256": canonical_execution_sha256(payload)}
    )


def _load_tracked(path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2RuntimeError("tracked V2 runtime artifact is invalid") from exc


def verify_tracked_v2_runtime(root: Path) -> dict[str, object]:
    """Independently regenerate both tracked V2 runtime artifacts."""

    expected = build_v2_runtime_manifest(root)
    tracked = _load_tracked(root / RUNTIME_MANIFEST_PATH, ClaimSupportValidationV2RuntimeManifest)
    if tracked != expected:
        raise ClaimValidationV2RuntimeError("tracked V2 runtime manifest is stale or altered")
    readiness = build_v2_runtime_readiness(expected)
    tracked_readiness = _load_tracked(
        root / RUNTIME_READINESS_PATH, ClaimSupportValidationV2RuntimeReadiness
    )
    if tracked_readiness != readiness:
        raise ClaimValidationV2RuntimeError("tracked V2 runtime readiness is stale or altered")
    return readiness.model_dump(mode="json")


def canonical_json(model: BaseModel) -> str:
    """Serialize one runtime artifact with stable bytes for tracked generation."""

    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "RUNTIME_MANIFEST_PATH",
    "RUNTIME_READINESS_PATH",
    "ClaimSupportValidationV2RuntimeManifest",
    "ClaimSupportValidationV2RuntimeReadiness",
    "ClaimValidationV2RuntimeError",
    "V2RelationFrameBatch",
    "build_v2_relation_frame_batch",
    "build_v2_runtime_manifest",
    "build_v2_runtime_readiness",
    "canonical_json",
    "verify_tracked_v2_runtime",
]
