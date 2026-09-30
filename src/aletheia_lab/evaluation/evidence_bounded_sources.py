"""Replay the existing development witnesses; never load new-source final outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
    verify_v3_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import reconstruct_runtime_split
from aletheia_lab.benchmark.p2.score_mapping_development import MODEL_KINDS
from aletheia_lab.benchmark.p2.score_mapping_development_symptoms import (
    _checked_prior_summary,
    load_development_cell,
)
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    build_development_evidence_views,
    mapping_observation,
    target_binding_rival_observation,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import apply_evaluator_mapping_fault
from aletheia_lab.benchmark.p2.target_binding_intervention import (
    TargetBindingSource,
    apply_paired_target_binding_fault,
    restore_target_bindings,
)
from aletheia_lab.benchmark.p2.target_binding_verification import verify_paired_target_binding
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.evidence_bounded_policy import compatibility_reference
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.score_mapping_reader import build_score_mapping_reader_context
from aletheia_lab.evaluation.warrant_development_io import _private_dir, _read


def replay_development_cases(
    *, root: Path, memory_root: Path, source_root: Path | None = None
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Reconstruct all twelve historical pairs, preserving their exact visible views."""
    prior = _private_dir(memory_root / "score-mapping-development-feasibility-v1")
    matched = _private_dir(memory_root / "score-mapping-development-symptom-matching-v1")
    prior_summary = _checked_prior_summary(prior)
    source_root = root if source_root is None else source_root
    if source_root.is_symlink():
        raise ValueError("development source checkout cannot be a symlink")
    summary = _read(matched / "summary.json")
    if (
        summary["schema_version"] != "score-mapping-development-symptom-study/v1"
        or summary["status"] != "development_candidate"
        or summary["visible_metric_decimal_places"] != 6
        or summary["provider_calls"] != 0
        or summary["registered_attempt"] is not False
        or summary["sealed_predictions_or_metrics_computed"] is not False
    ):
        raise ValueError("old development predecessor is incompatible")
    protocol_path = root / "configs/benchmark/p2_label_noise_shift_v3_protocol.json"
    protocol = load_v3_confirmatory_protocol(protocol_path)
    _, manifest, _ = verify_v3_protocol_artifacts(protocol, root=root)
    if summary["protocol_sha256"] != protocol.canonical_sha256():
        raise ValueError("old development protocol differs")
    cases: list[dict[str, Any]] = []
    for dataset, receipt in zip(manifest.datasets, protocol.dataset_splits, strict=True):
        archive = source_root / "data/raw/p2-v3" / dataset.archive.file_name
        _, frame = load_v3_dataset_snapshot_for_registration(dataset=dataset, archive_path=archive)
        split = reconstruct_runtime_split(
            protocol=protocol, dataset=dataset, frame=frame, receipt=receipt
        )
        for kind in MODEL_KINDS:
            key = (dataset.dataset_id, kind)
            prior_cell = next(
                c for c in prior_summary["cells"] if (c["dataset_id"], c["model_kind"]) == key
            )
            matched_cell = next(
                c for c in summary["cells"] if (c["dataset_id"], c["model_kind"]) == key
            )
            replay = load_development_cell(
                root=source_root,
                prior=prior,
                dataset=dataset,
                frame=frame,
                split=split,
                protocol=protocol,
                prior_cell=prior_cell,
                kind=kind,
                protocol_path=protocol_path,
            )
            for item in matched_cell["measurements"]:
                if item["selected_shards"] == 0:
                    continue
                cases.extend(_paired_cases(replay, item, dataset.dataset_id, kind))
    if len(cases) != 96 or len({c["pair_id"] for c in cases}) != 12:
        raise ValueError("complete old development pair/view census differs")
    return cases, {
        "feasibility_summary": file_sha256(prior / "summary.json"),
        "symptom_summary": file_sha256(matched / "summary.json"),
        "protocol": file_sha256(protocol_path),
    }


def _paired_cases(
    replay: Any, item: dict[str, Any], source: str, kind: str
) -> list[dict[str, Any]]:
    if item["status"] != "resolution_matched_pair":
        raise ValueError("cannot replace an unmatched development pair")
    intervention = apply_evaluator_mapping_fault(
        replay.source, selected_shard_count=item["selected_shards"]
    )
    mapping = mapping_observation(
        witness=replay.witness,
        source=replay.source,
        intervention=intervention,
        scoring_target_rows=replay.target_rows,
        reference_model=replay.model,
        reference_calibration=replay.calibration,
        artifacts=replay.artifacts,
        reference_features=replay.train,
        evaluation_features=replay.development,
    )
    target_source = TargetBindingSource(replay.witness.dataset_id, replay.target_rows)
    paired = apply_paired_target_binding_fault(
        target_source, swapped_pairs=tuple(tuple(p) for p in item["swapped_pairs"])
    )
    verification = verify_paired_target_binding(
        witness=replay.witness,
        score_source=replay.source,
        target_source=target_source,
        intervention=paired,
        corrected_target_rows=restore_target_bindings(target_source, paired),
        reference_model=replay.model,
        evaluation_matrix=replay.development,
        reference_calibration=replay.calibration,
        artifacts=replay.artifacts,
    )
    rival = target_binding_rival_observation(
        witness=replay.witness,
        source=replay.source,
        scoring_target_rows=paired.observed_target_rows,
        reference_features=replay.train,
        evaluation_features=replay.development,
    )
    if (
        mapping.observed_log_loss != item["mapping_log_loss"]
        or verification.faulty_log_loss != item["rival_log_loss"]
    ):
        raise ValueError("historical metrics do not replay exactly")
    pair_id = canonical_execution_sha256([source, kind, item["selected_shards"]])
    cases = []
    for truth, observation, expected in (
        ("score_mapping", mapping, item["mapping_view_sha256"]),
        ("target_binding", rival, item["rival_view_sha256"]),
    ):
        projections = build_development_evidence_views(observation, metric_decimal_places=6)
        for condition, projection in projections.items():
            if canonical_sha256(projection) != expected[condition]:
                raise ValueError("historical development projection differs")
            context = build_score_mapping_reader_context(
                observation, condition=condition
            ).model_payload()
            reference = compatibility_reference(context)
            if reference["status"] == "resolved" and reference["compatible"] != [truth]:
                raise ValueError("visible reference excludes the verified constructed world")
            cases.append(
                {
                    "pair_id": pair_id,
                    "source_cluster": source,
                    "model_kind": kind,
                    "dose": item["selected_shards"],
                    "truth": truth,
                    "condition": condition,
                    "context": context,
                    "reference": reference,
                }
            )
    missing = [c["context"] for c in cases if c["condition"] == "missing_key"]
    if missing[0] != missing[1]:
        raise ValueError("ambiguous worlds have a reader-envelope shortcut")
    return cases
