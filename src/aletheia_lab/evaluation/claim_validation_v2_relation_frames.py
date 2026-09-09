"""Authentic, outcome-blind relation-frame construction for validation V2."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from aletheia_lab.evaluation.claim_corpus_contracts import (
    AtomicClaimV2,
    DiagnosisOutputV2,
    EvidenceCondition,
    Mechanism,
)
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimEvidenceBinding,
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
    build_relation_assignment_request,
)
from aletheia_lab.evaluation.claim_validation_v2_frames import (
    conflicting_measurement_ids,
    covered_parts,
    material_measurements,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime_contracts import (
    FRAME_ASSIGNMENT_ALGORITHM,
    ChallengeFrame,
    ClaimSupportValidationV2RuntimeManifest,
    ClaimValidationV2RuntimeError,
    IneligibilityReason,
    RelationFrame,
    V2DiagnosisScheduleEntry,
    V2FrameCapacity,
    V2FrameIneligibility,
    V2RelationFrameAssignment,
    V2RelationFrameBatch,
    V2SourceFrameEntry,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256


def build_visible_context(
    items: Sequence[ModelVisibleEvidenceItem],
) -> ModelVisibleEvidenceContext:
    checked = tuple(
        sorted(
            (ModelVisibleEvidenceItem.model_validate(item.model_dump(mode="python"))
             for item in items),
            key=lambda item: item.evidence_id,
        )
    )
    payload = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": tuple(item.model_dump(mode="json") for item in checked),
    }
    digest = canonical_execution_sha256(payload)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{digest}", items=checked, context_sha256=digest
    )


def build_source_frame_entries(
    mechanism_by_family: Mapping[str, Mechanism], evidence: ObservedEvidenceCensus
) -> tuple[V2SourceFrameEntry, ...]:
    by_pair = {
        (item.family_id, item.evidence_condition): item for item in evidence.bindings
    }
    result = []
    for binding in evidence.bindings:
        source = binding.visible_context
        source_ids = tuple(item.evidence_id for item in source.items)
        withdrawal_items = tuple(
            item for item in source.items if item.evidence_id != "ev-key-measurement"
        )
        withdrawal = (
            build_visible_context(withdrawal_items)
            if len(withdrawal_items) < len(source.items) and withdrawal_items
            else None
        )
        partial_item = min(
            source.items,
            key=lambda item: (
                {"ev-key-measurement": 0, "ev-performance-summary": 1}.get(
                    item.evidence_id, 2
                ),
                item.evidence_id,
            ),
        )
        partial = build_visible_context((partial_item,))
        counter_candidates = tuple(
            item
            for (family_id, condition), item in by_pair.items()
            if family_id != binding.family_id
            and condition == binding.evidence_condition
            and mechanism_by_family[family_id] == mechanism_by_family[binding.family_id]
            and conflicting_measurement_ids(source.items, item.visible_context.items)
        )
        if not counter_candidates:
            raise ClaimValidationV2RuntimeError(
                "authentic direct-counterevidence capacity is unavailable"
            )
        counter = min(
            counter_candidates,
            key=lambda item: canonical_execution_sha256(
                {
                    "algorithm": FRAME_ASSIGNMENT_ALGORITHM,
                    "frame": "direct_counterevidence",
                    "source_context_sha256": source.context_sha256,
                    "candidate_context_sha256": item.visible_context.context_sha256,
                }
            ),
        )
        eligible = (("natural_context", "support_withdrawal")
                    if withdrawal is not None else ("natural_context",))
        payload: dict[str, object] = {
            "family_id": binding.family_id,
            "family_sha256": binding.family_sha256,
            "mechanism": mechanism_by_family[binding.family_id],
            "evidence_condition": binding.evidence_condition,
            "source_projection_sha256": binding.source_projection_sha256,
            "natural_context_sha256": source.context_sha256,
            "natural_evidence_ids": source_ids,
            "support_withdrawal_context_sha256": (
                withdrawal.context_sha256 if withdrawal is not None else None
            ),
            "partial_support_context_sha256": partial.context_sha256,
            "partial_support_evidence_ids": (partial_item.evidence_id,),
            "direct_counterevidence_context_sha256": counter.visible_context.context_sha256,
            "direct_counterevidence_family_id": counter.family_id,
            "eligible_frames": eligible
            + ("partial_support_projection", "direct_counterevidence"),
        }
        result.append(V2SourceFrameEntry.model_validate(
            {**payload, "entry_sha256": canonical_execution_sha256(payload)}
        ))
    if len({item.entry_sha256 for item in result}) != 45:
        raise ClaimValidationV2RuntimeError("V2 source-frame census is duplicated")
    return tuple(result)


def build_frame_capacity(
    frames: Sequence[V2SourceFrameEntry], schedule: Sequence[V2DiagnosisScheduleEntry]
) -> tuple[V2FrameCapacity, ...]:
    pairs_by_frame: dict[RelationFrame, set[tuple[str, EvidenceCondition]]] = {
        frame: set() for frame in (
            "natural_context", "support_withdrawal",
            "partial_support_projection", "direct_counterevidence",
        )
    }
    for entry in frames:
        for frame in entry.eligible_frames:
            pairs_by_frame[frame].add((entry.family_id, entry.evidence_condition))
    return tuple(V2FrameCapacity(
        frame=frame,
        family_count=len({family for family, _ in pairs}),
        context_count=len(pairs),
        scheduled_diagnosis_cell_count=sum(
            (item.family_id, item.evidence_condition) in pairs for item in schedule
        ),
    ) for frame, pairs in pairs_by_frame.items())


def _selected_claims(output: DiagnosisOutputV2) -> tuple[AtomicClaimV2, ...]:
    return tuple(sorted(
        output.atomic_claims,
        key=lambda claim: canonical_execution_sha256({
            "algorithm": "canonical-claim-hash/v1", "output_sha256": output.output_sha256,
            "claim": claim.model_dump(mode="json"),
        }),
    )[:2])


def _assignment(
    schedule: V2DiagnosisScheduleEntry, output: DiagnosisOutputV2,
    claim: AtomicClaimV2, frame: RelationFrame,
    context: ModelVisibleEvidenceContext, evidence_ids: Sequence[str],
) -> V2RelationFrameAssignment:
    binding_payload = {
        "schema_version": "claim-evidence-binding/v1", "source_partition": "development",
        "hidden_ground_truth_present": False, "evaluator_outcome_present": False,
        "family_id": schedule.family_id, "family_sha256": schedule.family_sha256,
        "evidence_condition": schedule.evidence_condition,
        "source_projection_sha256": context.context_sha256,
        "visible_context": context.model_dump(mode="json"),
    }
    binding = ClaimEvidenceBinding.model_validate({
        **binding_payload, "visible_context": context,
        "binding_sha256": canonical_execution_sha256(binding_payload),
    })
    request = build_relation_assignment_request(
        source_output_sha256=output.output_sha256, claim_local_id=claim.claim_local_id,
        claim_text=claim.claim_text, claim_type=claim.claim_type,
        cited_evidence_ids=evidence_ids, evidence_binding=binding,
    )
    payload = {
        "schedule_entry_sha256": schedule.v2_request_sha256,
        "family_id": schedule.family_id, "source_output_sha256": output.output_sha256,
        "claim_local_id": claim.claim_local_id, "frame": frame,
        "request": request.model_dump(mode="json"),
    }
    return V2RelationFrameAssignment.model_validate({
        **payload, "request": request,
        "assignment_sha256": canonical_execution_sha256(payload),
    })


def _reject(
    schedule: V2DiagnosisScheduleEntry, output: DiagnosisOutputV2,
    claim: AtomicClaimV2, frame: ChallengeFrame, reason: IneligibilityReason,
) -> V2FrameIneligibility:
    return V2FrameIneligibility(
        schedule_entry_sha256=schedule.v2_request_sha256,
        source_output_sha256=output.output_sha256, claim_local_id=claim.claim_local_id,
        frame=frame, reason=reason,
    )


def _partial_projection_reason(
    claim: AtomicClaimV2, cited: tuple[str, ...], frame: V2SourceFrameEntry,
    contexts: Mapping[str, ModelVisibleEvidenceContext],
) -> IneligibilityReason | None:
    if len(claim.material_parts) < 2:
        return "claim_has_one_material_part"
    if len(cited) < 2:
        return "claim_has_one_citation"
    if frame.partial_support_evidence_ids[0] not in cited:
        return "registered_projection_not_cited"
    parts = material_measurements(claim)
    coverage = covered_parts(parts, contexts[frame.partial_support_context_sha256].items)
    return None if 0 < len(coverage) < len(parts) else "projection_not_a_proper_subset"


def _eligible_challenges(
    schedule: V2DiagnosisScheduleEntry, frame: V2SourceFrameEntry,
    output: DiagnosisOutputV2, claim: AtomicClaimV2,
    contexts: Mapping[str, ModelVisibleEvidenceContext],
) -> tuple[
    dict[ChallengeFrame, tuple[ModelVisibleEvidenceContext, tuple[str, ...]]],
    tuple[V2FrameIneligibility, ...],
]:
    source = contexts[frame.natural_context_sha256]
    source_by_id = {item.evidence_id: item for item in source.items}
    cited = tuple(item for item in claim.visible_evidence_ids if item in source_by_id)
    eligible: dict[ChallengeFrame, tuple[ModelVisibleEvidenceContext, tuple[str, ...]]] = {}
    rejected = []
    parts = material_measurements(claim)
    cited_items = tuple(source_by_id[item] for item in cited)
    if not parts or covered_parts(parts, cited_items) != set(range(len(parts))):
        return eligible, tuple(_reject(
            schedule, output, claim, target, "material_measurement_binding_unestablished"
        ) for target in (
            "support_withdrawal", "partial_support_projection", "direct_counterevidence"
        ))
    if "ev-key-measurement" not in cited or frame.support_withdrawal_context_sha256 is None:
        rejected.append(_reject(
            schedule, output, claim, "support_withdrawal", "decisive_support_not_cited"
        ))
    else:
        withdrawal = contexts[frame.support_withdrawal_context_sha256]
        retained = tuple(item for item in cited if item != "ev-key-measurement")
        if retained and len(covered_parts(parts, withdrawal.items)) < len(parts):
            eligible["support_withdrawal"] = withdrawal, retained
        else:
            rejected.append(_reject(
                schedule, output, claim, "support_withdrawal",
                "withdrawal_not_a_proper_projection",
            ))
    reason = _partial_projection_reason(claim, cited, frame, contexts)
    if reason is not None:
        rejected.append(_reject(schedule, output, claim, "partial_support_projection", reason))
    else:
        selected = frame.partial_support_evidence_ids[0]
        eligible["partial_support_projection"] = (
            contexts[frame.partial_support_context_sha256], (selected,),
        )
    counter = contexts[frame.direct_counterevidence_context_sha256]
    counter_by_id = {item.evidence_id: item for item in counter.items}
    conflicting = conflicting_measurement_ids(cited_items, counter.items, parts)
    if conflicting:
        eligible["direct_counterevidence"] = (
            build_visible_context(tuple(counter_by_id[item] for item in conflicting)),
            conflicting,
        )
    else:
        rejected.append(_reject(
            schedule, output, claim, "direct_counterevidence",
            "no_authentic_counterevidence",
        ))
    return eligible, tuple(rejected)


def _frame_contexts(
    manifest: ClaimSupportValidationV2RuntimeManifest, evidence: ObservedEvidenceCensus,
) -> dict[str, ModelVisibleEvidenceContext]:
    contexts = {binding.visible_context.context_sha256: binding.visible_context
                for binding in evidence.bindings}
    for frame in manifest.source_frames:
        source = contexts[frame.natural_context_sha256]
        if frame.support_withdrawal_context_sha256 is not None:
            contexts[frame.support_withdrawal_context_sha256] = build_visible_context(
                tuple(item for item in source.items if item.evidence_id != "ev-key-measurement")
            )
        selected = frame.partial_support_evidence_ids[0]
        contexts[frame.partial_support_context_sha256] = build_visible_context(
            tuple(item for item in source.items if item.evidence_id == selected)
        )
    return contexts


def build_v2_relation_frame_batch(
    manifest: ClaimSupportValidationV2RuntimeManifest, evidence: ObservedEvidenceCensus,
    outputs: Sequence[tuple[str, DiagnosisOutputV2]], *,
    source_record_sha256_by_request: Mapping[str, str],
) -> V2RelationFrameBatch:
    """Assign frames using output-to-record bindings from the audited V2 store reader."""
    checked = ClaimSupportValidationV2RuntimeManifest.model_validate(
        manifest.model_dump(mode="python")
    )
    evidence = ObservedEvidenceCensus.model_validate(evidence.model_dump(mode="python"))
    if evidence.census_sha256 != checked.evidence_census_sha256:
        raise ClaimValidationV2RuntimeError("evidence census differs from the V2 manifest")
    expected = build_source_frame_entries(
        {item.family_id: item.mechanism for item in checked.diagnosis_schedule}, evidence
    )
    if expected != checked.source_frames:
        raise ClaimValidationV2RuntimeError("source frame differs from authentic evidence")
    schedule_by_hash = {item.v2_request_sha256: item for item in checked.diagnosis_schedule}
    output_ids = tuple(item[0] for item in outputs)
    if (len(output_ids) != len(set(output_ids))
            or any(item not in schedule_by_hash for item in output_ids)
            or set(output_ids) != set(source_record_sha256_by_request)):
        raise ClaimValidationV2RuntimeError(
            "diagnosis outputs are duplicated or outside the V2 schedule"
        )
    frame_by_pair = {(item.family_id, item.evidence_condition): item
                     for item in checked.source_frames}
    contexts = _frame_contexts(checked, evidence)
    assignments: list[V2RelationFrameAssignment] = []
    ineligible: list[V2FrameIneligibility] = []
    counts: Counter[ChallengeFrame] = Counter()
    source_claims = 0
    for schedule_sha, raw_output in sorted(
        outputs, key=lambda item: schedule_by_hash[item[0]].sequence
    ):
        schedule = schedule_by_hash[schedule_sha]
        output = DiagnosisOutputV2.model_validate(raw_output.model_dump(mode="python"))
        if output.source_record_sha256 != source_record_sha256_by_request[schedule_sha]:
            raise ClaimValidationV2RuntimeError(
                "diagnosis output is not bound to its V2 request identity"
            )
        frame = frame_by_pair[(schedule.family_id, schedule.evidence_condition)]
        natural = contexts[frame.natural_context_sha256]
        for claim in _selected_claims(output):
            natural_ids = claim.visible_evidence_ids
            if not set(natural_ids) <= {item.evidence_id for item in natural.items}:
                raise ClaimValidationV2RuntimeError("source claim cites unavailable evidence")
            assignments.append(_assignment(
                schedule, output, claim, "natural_context", natural, natural_ids
            ))
            source_claims += 1
            eligible, rejected = _eligible_challenges(
                schedule, frame, output, claim, contexts
            )
            ineligible.extend(rejected)
            if eligible:
                selected = min(eligible, key=lambda candidate: (
                    counts[candidate], canonical_execution_sha256({
                        "algorithm": FRAME_ASSIGNMENT_ALGORITHM,
                        "protocol_sha256": checked.protocol_sha256,
                        "output_sha256": output.output_sha256,
                        "claim_local_id": claim.claim_local_id, "frame": candidate,
                    }),
                ))
                context, evidence_ids = eligible[selected]
                assignments.append(_assignment(
                    schedule, output, claim, selected, context, evidence_ids
                ))
                counts[selected] += 1
    payload: dict[str, object] = {
        "protocol_sha256": checked.protocol_sha256,
        "runtime_manifest_sha256": checked.manifest_sha256,
        "source_claim_count": source_claims, "assignments": tuple(assignments),
        "ineligible_frames": tuple(ineligible), "relation_outcomes_observed": False,
    }
    identity = {
        **payload,
        "assignments": tuple(item.model_dump(mode="json") for item in assignments),
        "ineligible_frames": tuple(item.model_dump(mode="json") for item in ineligible),
    }
    return V2RelationFrameBatch.model_validate({
        **payload, "batch_sha256": canonical_execution_sha256(identity)
    })


__all__ = [
    "build_frame_capacity", "build_source_frame_entries",
    "build_v2_relation_frame_batch", "build_visible_context",
]
