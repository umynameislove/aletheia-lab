"""Build the outcome-blind finite census for the diagnosis main study.

The census is a complete transform of already-registered P2 result artifacts:
20 P2R measurement records plus 12 P2-v3.3 label-noise summary cells.  No
family is selected by effect size, model output, or a protected main outcome.

The private packet contains evaluator-only family/condition mappings and exact
model-visible contexts.  The repository seal contains only hashes, aggregate
counts, and pseudonymous Qwen sensitivity request identities.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel

from aletheia_lab.evaluation._diagnosis_main_census_contracts import (
    _CORE_QWEN_CONDITIONS,
    _QWEN_VARIANTS,
    PRIVATE_PACKET_SCHEMA_VERSION,
    PUBLIC_SEAL_SCHEMA_VERSION,
    QWEN_CENSUS_SCHEMA_VERSION,
    CensusSourceArtifact,
    CensusSourceContract,
    DiagnosisMainCensusError,
    DiagnosisMainCensusSeal,
    DiagnosisMainPrivateCensusPacket,
    QwenSensitivityCensus,
    SourceArtifactReceipt,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    ALL_CONDITIONS,
    CONTROLLED_VARIANTS,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainFamily,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.project.identity import content_sha256


@dataclass(frozen=True)
class DiagnosisMainCensusSources:
    source_contract: Path
    p2r_measurements: Path
    label_noise_primary: Path
    label_noise_replication: Path
    label_noise_protocol: Path


@dataclass(frozen=True)
class _FamilyProjection:
    family: DiagnosisMainFamily
    source_record_sha256: str
    key_title: str
    key_payload: dict[str, object]
    performance_payload: dict[str, object]
    provenance_payload: dict[str, object]
    secondary_payload: dict[str, object]
    counter_key: tuple[object, ...]
    noise_key: tuple[object, ...]


def _read_json_regular(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise DiagnosisMainCensusError("required census source is not a regular file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosisMainCensusError("required census source is unreadable") from exc


def _source_receipt(
    artifact: CensusSourceArtifact,
    path: Path,
    *,
    source_unit_count: int,
) -> SourceArtifactReceipt:
    observed = content_sha256(path.read_bytes())
    if observed != artifact.file_sha256:
        raise DiagnosisMainCensusError(f"source artifact hash mismatch: {artifact.source_id}")
    if source_unit_count != artifact.expected_source_unit_count:
        raise DiagnosisMainCensusError(f"source unit count mismatch: {artifact.source_id}")
    return SourceArtifactReceipt(
        source_id=artifact.source_id,
        file_sha256=observed,
        source_unit_count=source_unit_count,
    )


def _family_id(source_record_sha256: str) -> str:
    return f"dmf-{canonical_execution_sha256({'source_record_sha256': source_record_sha256})}"


def _superfamily_id(dataset_id: str, mechanism: str) -> str:
    return f"dmsf-{canonical_execution_sha256({'dataset': dataset_id, 'mechanism': mechanism})}"


def _family_model(
    *,
    source_record_sha256: str,
    source_artifact_sha256: str,
    source_unit_id: str,
    dataset_id: str,
    mechanism: str,
    template_id: str,
) -> DiagnosisMainFamily:
    payload = {
        "family_id": _family_id(source_record_sha256),
        "mechanism": mechanism,
        "dataset_id": dataset_id,
        "source_unit_id": source_unit_id,
        "source_record_sha256": source_record_sha256,
        "source_artifact_sha256": source_artifact_sha256,
        "intervention_template_id": template_id,
        "superfamily_id": _superfamily_id(dataset_id, mechanism),
        "source_partition": "prior_registered_outcome",
    }
    return DiagnosisMainFamily.model_validate(
        {**payload, "family_sha256": canonical_execution_sha256(payload)}
    )


def _required_mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DiagnosisMainCensusError(f"{label} is not a JSON object")
    return cast(dict[str, object], value)


def _required_number(payload: Mapping[str, object], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagnosisMainCensusError(f"source field {key} is not numeric")
    return float(value)


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DiagnosisMainCensusError(f"source field {key} is not text")
    return value


def _p2r_projections(
    records: Sequence[object],
    *,
    source_artifact_sha256: str,
) -> list[_FamilyProjection]:
    projections: list[_FamilyProjection] = []
    for raw in records:
        record = _required_mapping(raw, label="P2R measurement")
        original_mechanism = _required_text(record, "mechanism")
        if original_mechanism not in {"data_drift", "preprocessing_bug"}:
            raise DiagnosisMainCensusError("P2R source contains an unexpected mechanism")
        mechanism = (
            "preprocessing_mismatch" if original_mechanism == "preprocessing_bug" else "data_drift"
        )
        record_sha = _required_text(record, "measurement_sha256")
        dataset = _required_text(record, "dataset_id")
        seed = int(_required_number(record, "seed"))
        key_title = (
            "Observed input-distribution comparison"
            if mechanism == "data_drift"
            else "Observed feature-transformation comparison"
        )
        family = _family_model(
            source_record_sha256=record_sha,
            source_artifact_sha256=source_artifact_sha256,
            source_unit_id=record_sha,
            dataset_id=dataset,
            mechanism=mechanism,
            template_id=f"p2r-{mechanism}-measurement-v1",
        )
        observed_accuracy = _required_number(record, "manipulated_accuracy")
        reference_accuracy = _required_number(record, "clean_accuracy")
        projections.append(
            _FamilyProjection(
                family=family,
                source_record_sha256=record_sha,
                key_title=key_title,
                key_payload={
                    "measurement_dimension": _required_text(record, "target_feature"),
                    "observed_change_magnitude": _required_number(
                        record, "achieved_manipulation_magnitude"
                    ),
                    "observation_count_seed": seed,
                },
                performance_payload={
                    "reference_accuracy": reference_accuracy,
                    "observed_accuracy": observed_accuracy,
                    "observed_minus_reference": observed_accuracy - reference_accuracy,
                },
                provenance_payload={
                    "record_fingerprint": record_sha,
                    "protocol_fingerprint": _required_text(record, "protocol_sha256"),
                    "model_fingerprint": _required_text(record, "model_sha256"),
                    "split_fingerprint": _required_text(record, "split_membership_sha256"),
                },
                secondary_payload={
                    "comparison_accuracy": _required_number(record, "nuisance_accuracy"),
                    "comparison_difference_magnitude": _required_number(
                        record, "nuisance_effect_magnitude"
                    ),
                },
                counter_key=(dataset, mechanism, seed),
                noise_key=(dataset, mechanism, record_sha),
            )
        )
    return projections


def _label_noise_projections(
    attempt: Mapping[str, object],
    *,
    source_artifact_sha256: str,
    protocol_sha256: str,
) -> list[_FamilyProjection]:
    outcome = _required_mapping(attempt.get("outcome"), label="label-noise outcome")
    dataset = _required_text(outcome, "dataset_id")
    summaries = outcome.get("sensitivity_summaries")
    if not isinstance(summaries, list) or len(summaries) != 6:
        raise DiagnosisMainCensusError("label-noise attempt must contain six summaries")
    projections: list[_FamilyProjection] = []
    for raw_summary in summaries:
        summary = _required_mapping(raw_summary, label="label-noise summary")
        direction = _required_text(summary, "direction")
        if direction not in {"yes_to_no", "no_to_yes"}:
            raise DiagnosisMainCensusError("label-noise direction changed")
        rate = _required_number(summary, "conditional_rate")
        if rate not in {0.1, 0.2, 0.3}:
            raise DiagnosisMainCensusError("label-noise rate changed")
        record_payload = {"dataset_id": dataset, "summary": summary}
        record_sha = canonical_execution_sha256(record_payload)
        family = _family_model(
            source_record_sha256=record_sha,
            source_artifact_sha256=source_artifact_sha256,
            source_unit_id=f"{dataset}:{direction}:{rate:.1f}",
            dataset_id=dataset,
            mechanism="label_noise",
            template_id="p2-v3-3-training-target-comparison-v1",
        )
        projections.append(
            _FamilyProjection(
                family=family,
                source_record_sha256=record_sha,
                key_title="Observed training-target comparison",
                key_payload={
                    "observed_training_target_disagreement_rate": rate,
                    "comparison_direction": (
                        "class_1_to_0" if direction == "yes_to_no" else "class_0_to_1"
                    ),
                    "replicate_count": int(_required_number(summary, "replicate_count")),
                },
                performance_payload={
                    "mean_relative_net_effect": _required_number(
                        summary, "mean_relative_net_effect"
                    ),
                    "reference_net_effect": 0.0,
                    "replicate_count": int(_required_number(summary, "replicate_count")),
                },
                provenance_payload={
                    "record_fingerprint": record_sha,
                    "protocol_fingerprint": protocol_sha256,
                    "dataset_fingerprint": canonical_execution_sha256({"dataset_id": dataset}),
                },
                secondary_payload={
                    "registered_sensitivity_only": summary.get("sensitivity_only") is True,
                    "primary_rescue_permitted": summary.get("can_rescue_primary") is True,
                },
                counter_key=(dataset, "label_noise", rate, direction),
                noise_key=(dataset, "label_noise", record_sha),
            )
        )
    return projections


def _pair_sources(
    projections: Sequence[_FamilyProjection],
) -> tuple[dict[str, _FamilyProjection], dict[str, _FamilyProjection]]:
    by_superfamily: dict[str, list[_FamilyProjection]] = defaultdict(list)
    for projection in projections:
        by_superfamily[projection.family.superfamily_id].append(projection)
    noise_pairs: dict[str, _FamilyProjection] = {}
    for group in by_superfamily.values():
        ordered = sorted(group, key=lambda item: item.source_record_sha256)
        for offset, projection in enumerate(ordered):
            noise_pairs[projection.family.family_id] = ordered[(offset + 1) % len(ordered)]

    counter_pairs: dict[str, _FamilyProjection] = {}
    for projection in projections:
        family = projection.family
        group = by_superfamily[family.superfamily_id]
        if family.mechanism == "label_noise":
            dataset, _, rate, direction = projection.counter_key
            opposite = "no_to_yes" if direction == "yes_to_no" else "yes_to_no"
            matches = [
                candidate
                for candidate in group
                if candidate.counter_key == (dataset, "label_noise", rate, opposite)
            ]
        else:
            _, _, seed = projection.counter_key
            ordered = sorted(group, key=lambda item: cast(int, item.counter_key[2]))
            position = next(
                index for index, candidate in enumerate(ordered) if candidate is projection
            )
            matches = [ordered[(position + 1) % len(ordered)]]
        if len(matches) != 1 or matches[0].family.family_id == family.family_id:
            raise DiagnosisMainCensusError("counterevidence pairing is not unique and external")
        counter_pairs[family.family_id] = matches[0]
    return noise_pairs, counter_pairs


def _visible_item(
    *,
    evidence_id: str,
    kind: Literal["metric", "config", "log", "artifact", "dataset_profile", "lineage"],
    title: str,
    payload: Mapping[str, object],
    source_sha256: str,
) -> ModelVisibleEvidenceItem:
    return build_visible_evidence_item(
        evidence_id=evidence_id,
        kind=kind,
        title=title,
        content=canonical_execution_json(dict(payload)),
        source_content_sha256=source_sha256,
    )


def _visible_context(items: Sequence[ModelVisibleEvidenceItem]) -> ModelVisibleEvidenceContext:
    ordered = tuple(sorted(items, key=lambda item: item.evidence_id))
    identity = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": tuple(item.model_dump(mode="json") for item in ordered),
    }
    context_sha = canonical_execution_sha256(identity)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{context_sha}",
        items=ordered,
        context_sha256=context_sha,
    )


def _normalized_context_sha256(context: ModelVisibleEvidenceContext) -> str:
    return canonical_execution_sha256(
        tuple(
            {
                "kind": item.kind,
                "title": " ".join(item.title.casefold().split()),
                "content": " ".join(item.content.casefold().split()),
            }
            for item in context.items
        )
    )


def _contexts_for(
    projection: _FamilyProjection,
    *,
    noise_source: _FamilyProjection,
    counter_source: _FamilyProjection,
) -> tuple[tuple[DiagnosisMainContext, ModelVisibleEvidenceContext], ...]:
    source_sha = projection.source_record_sha256
    performance = _visible_item(
        evidence_id="ev-performance-summary",
        kind="metric",
        title="Measured prediction performance",
        payload=projection.performance_payload,
        source_sha256=source_sha,
    )
    provenance = _visible_item(
        evidence_id="ev-source-provenance",
        kind="lineage",
        title="Observed source and execution provenance",
        payload=projection.provenance_payload,
        source_sha256=source_sha,
    )
    decisive = _visible_item(
        evidence_id="ev-decisive-measurement",
        kind="metric",
        title=projection.key_title,
        payload=projection.key_payload,
        source_sha256=source_sha,
    )
    secondary = _visible_item(
        evidence_id="ev-secondary-observation",
        kind="dataset_profile",
        title="Measured secondary observation from the same dataset slice",
        payload={
            "source_fingerprint": noise_source.source_record_sha256,
            **noise_source.secondary_payload,
        },
        source_sha256=noise_source.source_record_sha256,
    )
    challenge = _visible_item(
        evidence_id="ev-attribution-challenge",
        kind="metric",
        title="Independent within-slice attribution challenge",
        payload={
            "source_fingerprint": counter_source.source_record_sha256,
            "observed_comparison": counter_source.performance_payload,
            "secondary_comparison": counter_source.secondary_payload,
        },
        source_sha256=counter_source.source_record_sha256,
    )
    by_condition = {
        "full": (performance, provenance, decisive),
        "missing_key": (performance, provenance),
        "noisy": (performance, provenance, decisive, secondary),
        "counterevidence": (performance, provenance, decisive, challenge),
    }
    response_modes = {
        "full": "diagnose",
        "missing_key": "abstain_or_request_evidence",
        "noisy": "diagnose_with_uncertainty",
        "counterevidence": "acknowledge_conflict_or_abstain",
    }
    result = []
    for condition in ALL_CONDITIONS:
        visible = _visible_context(by_condition[condition])
        payload = {
            "context_id": visible.context_id,
            "case_family_id": projection.family.family_id,
            "evidence_condition": condition,
            "expected_response_mode": response_modes[condition],
            "visible_context_sha256": visible.context_sha256,
            "normalized_content_sha256": _normalized_context_sha256(visible),
            "semantic_cluster_id": f"semantic-{projection.family.family_sha256}",
            "counterevidence_source_family_id": (
                counter_source.family.family_id if condition == "counterevidence" else None
            ),
        }
        context = DiagnosisMainContext.model_validate(
            {**payload, "context_sha256": canonical_execution_sha256(payload)}
        )
        result.append((context, visible))
    return tuple(result)


def _request(context: DiagnosisMainContext, variant: str) -> DiagnosisMainExpectedRequest:
    identity = canonical_execution_sha256({"context_id": context.context_id, "variant": variant})
    payload = {
        "request_id": f"dmr-{identity}",
        "context_id": context.context_id,
        "variant": variant,
    }
    return DiagnosisMainExpectedRequest.model_validate(
        {**payload, "request_sha256": canonical_execution_sha256(payload)}
    )


def _serialize(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def build_diagnosis_main_census(
    sources: DiagnosisMainCensusSources,
) -> tuple[
    DiagnosisMainPrivateCensusPacket,
    DiagnosisMainCensusSeal,
    QwenSensitivityCensus,
]:
    """Build and validate the exact private packet, public seal, and Qwen subset."""

    contract = CensusSourceContract.model_validate_json(sources.source_contract.read_bytes())
    artifacts = {item.source_id: item for item in contract.source_artifacts}
    p2r_raw = _read_json_regular(sources.p2r_measurements)
    if not isinstance(p2r_raw, list) or len(p2r_raw) != 20:
        raise DiagnosisMainCensusError("P2R source must contain exactly 20 records")
    primary_raw = _required_mapping(
        _read_json_regular(sources.label_noise_primary),
        label="primary label-noise attempt",
    )
    replication_raw = _required_mapping(
        _read_json_regular(sources.label_noise_replication),
        label="replication label-noise attempt",
    )
    _read_json_regular(sources.label_noise_protocol)

    receipts = (
        _source_receipt(
            artifacts["p2r_confirmatory_measurements"],
            sources.p2r_measurements,
            source_unit_count=20,
        ),
        _source_receipt(
            artifacts["p2_v3_3_label_noise_primary"],
            sources.label_noise_primary,
            source_unit_count=6,
        ),
        _source_receipt(
            artifacts["p2_v3_3_label_noise_replication"],
            sources.label_noise_replication,
            source_unit_count=6,
        ),
        _source_receipt(
            artifacts["p2_v3_3_label_noise_protocol"],
            sources.label_noise_protocol,
            source_unit_count=0,
        ),
    )
    projections = [
        *_p2r_projections(
            p2r_raw,
            source_artifact_sha256=artifacts["p2r_confirmatory_measurements"].file_sha256,
        ),
        *_label_noise_projections(
            primary_raw,
            source_artifact_sha256=artifacts["p2_v3_3_label_noise_primary"].file_sha256,
            protocol_sha256=artifacts["p2_v3_3_label_noise_protocol"].file_sha256,
        ),
        *_label_noise_projections(
            replication_raw,
            source_artifact_sha256=artifacts["p2_v3_3_label_noise_replication"].file_sha256,
            protocol_sha256=artifacts["p2_v3_3_label_noise_protocol"].file_sha256,
        ),
    ]
    if len(projections) != 32:
        raise DiagnosisMainCensusError("source census did not produce exactly 32 families")
    families = tuple(sorted((item.family for item in projections), key=lambda item: item.family_id))
    if len({item.family_id for item in families}) != 32:
        raise DiagnosisMainCensusError("source census produced duplicate family identities")
    noise_pairs, counter_pairs = _pair_sources(projections)
    context_pairs = tuple(
        pair
        for projection in projections
        for pair in _contexts_for(
            projection,
            noise_source=noise_pairs[projection.family.family_id],
            counter_source=counter_pairs[projection.family.family_id],
        )
    )
    contexts = tuple(sorted((pair[0] for pair in context_pairs), key=lambda item: item.context_id))
    visible_contexts = tuple(
        sorted((pair[1] for pair in context_pairs), key=lambda item: item.context_id)
    )
    requests = tuple(
        sorted(
            (_request(context, variant) for context in contexts for variant in CONTROLLED_VARIANTS),
            key=lambda item: item.request_id,
        )
    )
    census_payload = {
        "schema_version": "diagnosis-main-analysis-census/v2",
        "source_partition": "sealed_main",
        "family_count": 32,
        "context_count": 128,
        "request_count": 1024,
        "exact_duplicate_context_count": 0,
        "cross_family_semantic_duplicate_count": 0,
        "families": tuple(item.model_dump(mode="json") for item in families),
        "contexts": tuple(item.model_dump(mode="json") for item in contexts),
        "requests": tuple(item.model_dump(mode="json") for item in requests),
    }
    census = DiagnosisMainAnalysisCensus.model_validate(
        {
            **census_payload,
            "families": families,
            "contexts": contexts,
            "requests": requests,
            "census_sha256": canonical_execution_sha256(census_payload),
        }
    )

    families_by_superfamily: dict[str, list[DiagnosisMainFamily]] = defaultdict(list)
    for family in families:
        families_by_superfamily[family.superfamily_id].append(family)
    qwen_families = tuple(
        sorted(
            family.family_id
            for group in families_by_superfamily.values()
            for family in sorted(group, key=lambda item: item.family_sha256)[:2]
        )
    )
    context_by_id = {item.context_id: item for item in contexts}
    qwen_requests = tuple(
        sorted(
            request.request_id
            for request in requests
            if context_by_id[request.context_id].case_family_id in qwen_families
            and context_by_id[request.context_id].evidence_condition in _CORE_QWEN_CONDITIONS
            and request.variant in _QWEN_VARIANTS
        )
    )
    qwen_payload = {
        "schema_version": QWEN_CENSUS_SCHEMA_VERSION,
        "status": "request_census_locked_execution_not_authorized",
        "protected_main_outcomes_opened": False,
        "execution_authorized": False,
        "analysis_census_sha256": census.census_sha256,
        "selection_rule": "lowest_family_sha256_within_each_dataset_by_mechanism_superfamily",
        "family_count": 12,
        "families_per_superfamily": 2,
        "conditions": ("full", "missing_key", "noisy"),
        "variants": ("B1", "A3"),
        "request_count": 72,
        "family_ids": qwen_families,
        "request_ids": qwen_requests,
        "replacement_after_outcome_forbidden": True,
        "pooling_with_gpt_main_permitted": False,
    }
    qwen = QwenSensitivityCensus.model_validate(
        {**qwen_payload, "census_sha256": canonical_execution_sha256(qwen_payload)}
    )
    packet_payload = {
        "schema_version": PRIVATE_PACKET_SCHEMA_VERSION,
        "status": "outcome_blind_census_locked_execution_not_authorized",
        "protected_main_outcomes_opened": False,
        "execution_authorized": False,
        "source_contract_sha256": contract.source_contract_sha256,
        "source_receipts": tuple(item.model_dump(mode="json") for item in receipts),
        "analysis_census": census.model_dump(mode="json"),
        "visible_contexts": tuple(item.model_dump(mode="json") for item in visible_contexts),
        "qwen_family_ids": qwen_families,
        "qwen_request_ids": qwen_requests,
    }
    packet = DiagnosisMainPrivateCensusPacket.model_validate(
        {
            **packet_payload,
            "source_receipts": receipts,
            "analysis_census": census,
            "visible_contexts": visible_contexts,
            "packet_sha256": canonical_execution_sha256(packet_payload),
        }
    )
    mechanism_counts = dict(sorted(Counter(item.mechanism for item in families).items()))
    condition_counts = dict(sorted(Counter(item.evidence_condition for item in contexts).items()))
    variant_counts = dict(sorted(Counter(item.variant for item in requests).items()))
    normalized_hashes = tuple(item.normalized_content_sha256 for item in contexts)
    semantic_families: dict[str, set[str]] = defaultdict(set)
    for context in contexts:
        semantic_families[context.semantic_cluster_id].add(context.case_family_id)
    seal_payload = {
        "schema_version": PUBLIC_SEAL_SCHEMA_VERSION,
        "status": "outcome_blind_census_locked_execution_not_authorized",
        "protected_main_outcomes_opened": False,
        "execution_authorized": False,
        "source_contract_sha256": contract.source_contract_sha256,
        "source_receipts": tuple(item.model_dump(mode="json") for item in receipts),
        "family_count": 32,
        "context_count": 128,
        "controlled_request_count": 1024,
        "dataset_count": len({item.dataset_id for item in families}),
        "superfamily_count": len({item.superfamily_id for item in families}),
        "mechanism_counts": mechanism_counts,
        "contexts_per_condition": condition_counts,
        "requests_per_variant": variant_counts,
        "exact_duplicate_context_count": 0,
        "normalized_duplicate_context_count": len(normalized_hashes) - len(set(normalized_hashes)),
        "cross_family_semantic_duplicate_count": sum(
            len(family_ids) > 1 for family_ids in semantic_families.values()
        ),
        "analysis_census_sha256": census.census_sha256,
        "private_packet_canonical_sha256": packet.packet_sha256,
        "private_packet_byte_sha256": content_sha256(_serialize(packet)),
        "private_packet_locator_role": "project_private_memory_artifact",
        "qwen_family_count": 12,
        "qwen_request_count": 72,
        "qwen_census_sha256": qwen.census_sha256,
        "b3_controlled_matrix_included": False,
        "b3_reporting_class": "separate_logdx_native_external_transfer",
    }
    seal = DiagnosisMainCensusSeal.model_validate(
        {**seal_payload, "seal_sha256": canonical_execution_sha256(seal_payload)}
    )
    return packet, seal, qwen


def serialize_census_artifact(model: BaseModel) -> bytes:
    """Return the one canonical human-readable byte representation used for sealing."""

    return _serialize(model)


__all__ = [
    "CensusSourceContract",
    "DiagnosisMainCensusError",
    "DiagnosisMainCensusSeal",
    "DiagnosisMainCensusSources",
    "DiagnosisMainPrivateCensusPacket",
    "QwenSensitivityCensus",
    "build_diagnosis_main_census",
    "serialize_census_artifact",
]
