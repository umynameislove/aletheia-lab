from __future__ import annotations

import json
from pathlib import Path

from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainCensusSources,
    build_diagnosis_main_census,
    serialize_census_artifact,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _p2r_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for dataset_index, dataset_id in enumerate(("portable-a", "portable-b")):
        for mechanism_index, mechanism in enumerate(("data_drift", "preprocessing_bug")):
            for seed in range(5):
                identity = {
                    "dataset_id": dataset_id,
                    "mechanism": mechanism,
                    "seed": seed,
                }
                offset = dataset_index * 0.01 + mechanism_index * 0.02 + seed * 0.001
                records.append(
                    {
                        **identity,
                        "measurement_sha256": canonical_execution_sha256(identity),
                        "target_feature": f"feature-{dataset_index}-{mechanism_index}-{seed}",
                        "achieved_manipulation_magnitude": 0.1 + offset,
                        "manipulated_accuracy": 0.7 - offset,
                        "clean_accuracy": 0.8 + dataset_index * 0.01,
                        "protocol_sha256": canonical_execution_sha256(
                            {"protocol": mechanism}
                        ),
                        "model_sha256": canonical_execution_sha256(
                            {"model": dataset_id}
                        ),
                        "split_membership_sha256": canonical_execution_sha256(
                            {"split": dataset_id, "seed": seed}
                        ),
                        "nuisance_accuracy": 0.6 + offset,
                        "nuisance_effect_magnitude": 0.05 + offset,
                    }
                )
    return records


def _label_noise_attempt(dataset_id: str) -> dict[str, object]:
    summaries = []
    for direction_index, direction in enumerate(("yes_to_no", "no_to_yes")):
        for rate in (0.1, 0.2, 0.3):
            summaries.append(
                {
                    "direction": direction,
                    "conditional_rate": rate,
                    "replicate_count": 5,
                    "mean_relative_net_effect": (
                        rate * (-1.0 if direction_index == 0 else 1.0)
                    ),
                    "sensitivity_only": True,
                    "can_rescue_primary": False,
                }
            )
    return {
        "outcome": {
            "dataset_id": dataset_id,
            "sensitivity_summaries": summaries,
        }
    }


def _portable_sources(tmp_path: Path) -> DiagnosisMainCensusSources:
    paths = {
        "p2r_confirmatory_measurements": tmp_path / "measurements.json",
        "p2_v3_3_label_noise_primary": tmp_path / "primary-attempt.json",
        "p2_v3_3_label_noise_replication": tmp_path / "replication-attempt.json",
        "p2_v3_3_label_noise_protocol": tmp_path / "protocol.json",
    }
    _write_json(paths["p2r_confirmatory_measurements"], _p2r_records())
    _write_json(paths["p2_v3_3_label_noise_primary"], _label_noise_attempt("portable-a"))
    _write_json(
        paths["p2_v3_3_label_noise_replication"],
        _label_noise_attempt("portable-b"),
    )
    _write_json(
        paths["p2_v3_3_label_noise_protocol"],
        {"schema_version": "portable-test-protocol/v1"},
    )

    contract = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_census_source_contract.json").read_text(
            encoding="utf-8"
        )
    )
    for artifact in contract["source_artifacts"]:
        artifact["file_sha256"] = content_sha256(paths[artifact["source_id"]].read_bytes())
    contract["source_contract_sha256"] = canonical_execution_sha256(
        {key: value for key, value in contract.items() if key != "source_contract_sha256"}
    )
    contract_path = tmp_path / "source-contract.json"
    _write_json(contract_path, contract)
    return DiagnosisMainCensusSources(
        source_contract=contract_path,
        p2r_measurements=paths["p2r_confirmatory_measurements"],
        label_noise_primary=paths["p2_v3_3_label_noise_primary"],
        label_noise_replication=paths["p2_v3_3_label_noise_replication"],
        label_noise_protocol=paths["p2_v3_3_label_noise_protocol"],
    )


def test_census_build_is_portable_without_private_preserved_artifacts(
    tmp_path: Path,
) -> None:
    sources = _portable_sources(tmp_path)

    first_packet, first_seal, first_qwen = build_diagnosis_main_census(sources)
    second_packet, second_seal, second_qwen = build_diagnosis_main_census(sources)

    assert serialize_census_artifact(first_packet) == serialize_census_artifact(second_packet)
    assert first_seal == second_seal
    assert first_qwen == second_qwen
    assert first_seal.family_count == 32
    assert first_seal.context_count == 128
    assert first_seal.controlled_request_count == 1024
    assert first_seal.dataset_count == 2
    assert first_seal.superfamily_count == 6
    assert first_seal.mechanism_counts == {
        "data_drift": 10,
        "label_noise": 12,
        "preprocessing_mismatch": 10,
    }
    assert len(first_packet.visible_contexts) == 128
    assert len(first_qwen.family_ids) == 12
    assert len(first_qwen.request_ids) == 72
    assert first_seal.private_packet_byte_sha256 == content_sha256(
        serialize_census_artifact(first_packet)
    )
