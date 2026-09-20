from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainCensusError,
    DiagnosisMainCensusSources,
    build_diagnosis_main_census,
    serialize_census_artifact,
)
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]
PRESERVED = (
    ROOT.parent
    / "preserved-artifacts/p2-label-noise-shift-factorial-v3.3"
    / "sha256-d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152"
    / "result-store"
)

pytestmark = pytest.mark.skipif(
    not PRESERVED.is_dir(),
    reason="private preserved P2 artifacts are available only in the project workspace",
)


def _sources() -> DiagnosisMainCensusSources:
    return DiagnosisMainCensusSources(
        source_contract=ROOT / "configs/evaluation/diagnosis_main_census_source_contract.json",
        p2r_measurements=ROOT / "experiments/p2/outputs/p2r-confirmatory-v1-2/measurements.json",
        label_noise_primary=PRESERVED / "primary-attempt.json",
        label_noise_replication=PRESERVED / "replication-attempt.json",
        label_noise_protocol=ROOT / "configs/benchmark/p2_label_noise_shift_v3_3_protocol.json",
    )


def test_complete_prior_source_census_is_deterministic_and_fail_closed() -> None:
    first_packet, first_seal, first_qwen = build_diagnosis_main_census(_sources())
    second_packet, second_seal, second_qwen = build_diagnosis_main_census(_sources())

    assert serialize_census_artifact(first_packet) == serialize_census_artifact(second_packet)
    assert first_seal == second_seal
    assert first_qwen == second_qwen
    assert first_seal.protected_main_outcomes_opened is False
    assert first_seal.execution_authorized is False
    assert first_seal.family_count == 32
    assert first_seal.context_count == 128
    assert first_seal.controlled_request_count == 1024
    assert first_seal.mechanism_counts == {
        "data_drift": 10,
        "label_noise": 12,
        "preprocessing_mismatch": 10,
    }
    assert first_seal.contexts_per_condition == {
        "counterevidence": 32,
        "full": 32,
        "missing_key": 32,
        "noisy": 32,
    }
    assert first_seal.exact_duplicate_context_count == 0
    assert first_seal.normalized_duplicate_context_count == 0
    assert first_seal.cross_family_semantic_duplicate_count == 0
    assert first_seal.private_packet_byte_sha256 == content_sha256(
        serialize_census_artifact(first_packet)
    )


def test_each_family_has_exact_sibling_transformations_and_eight_paths() -> None:
    packet, _, _ = build_diagnosis_main_census(_sources())
    census = packet.analysis_census
    visible = {item.context_id: item for item in packet.visible_contexts}
    contexts_by_family: dict[str, dict[str, set[str]]] = {}
    request_cells: dict[str, set[str]] = {}
    for context in census.contexts:
        contexts_by_family.setdefault(context.case_family_id, {})[context.evidence_condition] = {
            item.evidence_id for item in visible[context.context_id].items
        }
        request_cells[context.context_id] = {
            request.variant
            for request in census.requests
            if request.context_id == context.context_id
        }

    for conditions in contexts_by_family.values():
        assert set(conditions) == {"full", "missing_key", "noisy", "counterevidence"}
        assert conditions["missing_key"] == conditions["full"] - {"ev-decisive-measurement"}
        assert conditions["noisy"] == conditions["full"] | {"ev-secondary-observation"}
        assert conditions["counterevidence"] == conditions["full"] | {"ev-attribution-challenge"}
    assert all(
        variants == {"A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL"}
        for variants in request_cells.values()
    )


def test_qwen_subset_is_two_families_per_superfamily_and_exact_72_requests() -> None:
    packet, seal, qwen = build_diagnosis_main_census(_sources())
    families = {family.family_id: family for family in packet.analysis_census.families}
    superfamilies = [families[family_id].superfamily_id for family_id in qwen.family_ids]

    assert len(qwen.family_ids) == 12
    assert len(qwen.request_ids) == 72
    assert set(superfamilies) == {
        family.superfamily_id for family in packet.analysis_census.families
    }
    assert all(superfamilies.count(superfamily) == 2 for superfamily in set(superfamilies))
    assert qwen.execution_authorized is False
    assert qwen.pooling_with_gpt_main_permitted is False
    assert seal.qwen_census_sha256 == qwen.census_sha256


def test_source_mutation_is_rejected_before_census_construction(tmp_path: Path) -> None:
    sources = _sources()
    modified = json.loads(sources.p2r_measurements.read_text(encoding="utf-8"))
    modified[0]["clean_accuracy"] = 0.0
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps(modified), encoding="utf-8")

    with pytest.raises(DiagnosisMainCensusError, match="hash mismatch"):
        build_diagnosis_main_census(
            DiagnosisMainCensusSources(
                source_contract=sources.source_contract,
                p2r_measurements=path,
                label_noise_primary=sources.label_noise_primary,
                label_noise_replication=sources.label_noise_replication,
                label_noise_protocol=sources.label_noise_protocol,
            )
        )
