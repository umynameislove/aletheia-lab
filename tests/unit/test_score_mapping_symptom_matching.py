"""The evaluator-mapping hard negative cannot change scores or priors."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_score_mapping_verification import _case

import aletheia_lab.benchmark.p2.score_mapping_development_symptoms as symptoms
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_mapping_development_symptoms import (
    DevelopmentSymptomStudyError,
    _CellReplay,
    _checked_prior_summary,
    _coarsened_views,
    _measure_dose,
    _required_artifact,
    run_development_symptom_study,
)
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    DevelopmentObservation,
    ScoreMappingEvidenceError,
    build_development_evidence_views,
    serialize_development_evidence_view,
    serialize_m5_diagnostic_view,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import apply_evaluator_mapping_fault
from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import (
    MAX_ABSOLUTE_LOSS_GAP,
    VISIBLE_METRIC_DECIMALS,
    SymptomMatchingError,
    match_target_swaps,
    verify_target_swap_match,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import verify_evaluator_mapping
from aletheia_lab.content_hashing import file_sha256


def test_equal_class_count_swap_exactly_matches_two_row_mapping_symptom() -> None:
    ids = ("r0", "r1")
    targets = (0, 1)
    scores = (0.1, 0.9)
    mapping_loss = reference_prior_standardized_log_loss(
        true_labels=targets, probabilities=(0.9, 0.1)
    )
    rival = match_target_swaps(
        record_ids=ids,
        targets=targets,
        probabilities=scores,
        mapping_log_loss=mapping_loss,
        max_changed_targets=2,
    )
    assert rival.scoring_target_rows == (("r0", 1), ("r1", 0))
    assert rival.swapped_pairs == (("r0", "r1"),)
    assert rival.observed_log_loss == pytest.approx(mapping_loss, abs=1e-14)
    assert rival.absolute_loss_gap <= MAX_ABSOLUTE_LOSS_GAP
    assert rival.resolution_matched
    verify_target_swap_match(
        record_ids=ids,
        targets=targets,
        probabilities=scores,
        mapping_log_loss=mapping_loss,
        max_changed_targets=2,
        result=rival,
    )


def test_independent_pair_replay_rejects_forged_rows_and_metrics() -> None:
    ids = ("r0", "r1")
    targets = (0, 1)
    scores = (0.1, 0.9)
    mapping_loss = reference_prior_standardized_log_loss(
        true_labels=targets, probabilities=(0.9, 0.1)
    )
    rival = match_target_swaps(
        record_ids=ids,
        targets=targets,
        probabilities=scores,
        mapping_log_loss=mapping_loss,
        max_changed_targets=2,
    )
    for forged in (
        replace(rival, scoring_target_rows=(("r0", 0), ("r1", 1))),
        replace(rival, observed_log_loss=rival.observed_log_loss + 0.01),
        replace(rival, swapped_pairs=(("r1", "r0"),)),
    ):
        with pytest.raises(SymptomMatchingError):
            verify_target_swap_match(
                record_ids=ids,
                targets=targets,
                probabilities=scores,
                mapping_log_loss=mapping_loss,
                max_changed_targets=2,
                result=forged,
            )


def test_rival_uses_only_opposite_label_pairs_and_respects_footprint() -> None:
    ids = tuple(f"r{index}" for index in range(20))
    labels = tuple(index % 2 for index in range(20))
    scores = tuple(0.1 if label == 0 else 0.9 for label in labels)
    high_target = reference_prior_standardized_log_loss(
        true_labels=labels, probabilities=tuple(1.0 - score for score in scores)
    )
    rival = match_target_swaps(
        record_ids=ids,
        targets=labels,
        probabilities=scores,
        mapping_log_loss=high_target,
        max_changed_targets=4,
    )
    changed = tuple(
        index
        for index, (_, label) in enumerate(rival.scoring_target_rows)
        if label != labels[index]
    )
    assert len(changed) == 2 * len(rival.swapped_pairs) <= 4
    assert tuple(label for _, label in rival.scoring_target_rows).count(1) == labels.count(1)
    assert not rival.resolution_matched


@pytest.mark.parametrize(
    ("ids", "targets", "scores", "footprint"),
    [
        (("r0", "r0"), (0, 1), (0.1, 0.9), 2),
        (("r0", "r1"), (False, 1), (0.1, 0.9), 2),
        (("r0", "r1"), (0, 1), (0.0, 0.9), 2),
        (("r0", "r1"), (0, 1), (0.1, 0.9), 1),
    ],
)
def test_malformed_or_too_small_rival_is_rejected(
    ids: tuple[str, ...],
    targets: tuple[int, ...],
    scores: tuple[float, ...],
    footprint: int,
) -> None:
    with pytest.raises(SymptomMatchingError):
        match_target_swaps(
            record_ids=ids,
            targets=targets,
            probabilities=scores,
            mapping_log_loss=1.0,
            max_changed_targets=footprint,
        )


def _observation() -> DevelopmentObservation:
    return DevelopmentObservation(
        record_count=2,
        reference_log_loss=0.5000001,
        observed_log_loss=0.6000001,
        model_classes=(0, 1),
        evaluator_classes=(1, 0),
        example_score_pair=(0.1, 0.9),
        example_observed_positive=0.1,
        example_corrected_positive=0.9,
        target_binding_matches_source=True,
        corrected_log_loss=0.5000001,
        feature_width=1,
        reference_feature_count=4,
        feature_mean_distance=0.2,
        source_identity_sha256="a" * 64,
        reference_features_sha256="b" * 64,
    )


def test_visible_resolution_is_explicit_and_does_not_change_old_projection() -> None:
    mapping = _observation()
    rival = replace(
        mapping,
        observed_log_loss=0.6000002,
        evaluator_classes=(0, 1),
        example_observed_positive=0.9,
        target_binding_matches_source=False,
        corrected_log_loss=0.6000002,
    )
    assert VISIBLE_METRIC_DECIMALS == 6
    assert (
        build_development_evidence_views(mapping)["missing_key"]
        != (build_development_evidence_views(rival)["missing_key"])
    )
    mapping_views = _coarsened_views(mapping)
    rival_views = _coarsened_views(rival)
    assert mapping_views["missing_key"] == rival_views["missing_key"]
    assert mapping_views["full"] != rival_views["full"]
    assert mapping_views["missing_key"]["metric_decimal_places"] == 6
    assert (
        build_development_evidence_views(mapping)["missing_key"] != (mapping_views["missing_key"])
    )


def test_shortcut_rule_has_no_pairwise_signal_without_key() -> None:
    mapping = _observation()
    rival = replace(
        mapping,
        observed_log_loss=0.6000002,
        evaluator_classes=(0, 1),
        example_observed_positive=0.9,
        target_binding_matches_source=False,
        corrected_log_loss=0.6000002,
    )
    mapping_views = _coarsened_views(mapping)
    rival_views = _coarsened_views(rival)
    missing = mapping_views["missing_key"]
    assert missing == rival_views["missing_key"]
    assert set(missing) == {"schema_version", "metric_decimal_places", "items"}
    assert [item["id"] for item in missing["items"]] == [
        "performance-comparison",
        "score-source-controls",
    ]
    serialized = json.dumps(missing, sort_keys=True)
    assert not any(
        token in serialized
        for token in ("target-binding", "column-interpretation", "independent-recomputation")
    )

    def full_rule(view: dict[str, object]) -> str:
        items = {item["id"]: item["payload"] for item in view["items"]}
        interpretation = items["column-interpretation"]
        targets_match = items["target-binding-check"]["scoring_targets_match_source_rows"]
        if interpretation["model_column_classes"] != interpretation["evaluator_column_classes"]:
            return "mapping" if targets_match else "unresolved"
        return "target_binding" if not targets_match else "unresolved"

    assert full_rule(mapping_views["full"]) == "mapping"
    assert full_rule(rival_views["full"]) == "target_binding"
    assert mapping_views["noisy"]["items"][-1] == rival_views["noisy"]["items"][-1]
    assert mapping_views["misleading"]["items"][-1] == rival_views["misleading"]["items"][-1]


def test_reader_bytes_match_only_at_the_declared_observation_resolution() -> None:
    mapping = _observation()
    rival = replace(
        mapping,
        observed_log_loss=0.6000002,
        evaluator_classes=(0, 1),
        example_observed_positive=0.9,
        target_binding_matches_source=False,
        corrected_log_loss=0.6000002,
    )
    six = serialize_development_evidence_view(
        mapping, condition="missing_key", metric_decimal_places=6
    )
    assert six == serialize_development_evidence_view(
        rival, condition="missing_key", metric_decimal_places=6
    )
    assert six == serialize_m5_diagnostic_view(mapping, condition="missing_key")
    assert six == serialize_m5_diagnostic_view(rival, condition="missing_key")
    assert json.loads(six) == json.loads(json.dumps(_coarsened_views(mapping)["missing_key"]))
    assert serialize_development_evidence_view(
        mapping, condition="missing_key", metric_decimal_places=12
    ) != serialize_development_evidence_view(
        rival, condition="missing_key", metric_decimal_places=12
    )
    assert serialize_development_evidence_view(
        mapping, condition="full", metric_decimal_places=6
    ) != serialize_development_evidence_view(rival, condition="full", metric_decimal_places=6)
    for private_token in (
        b"source_identity_sha256",
        b"reference_features_sha256",
        b"evaluator_classes",
        b"target_binding_matches_source",
        b"condition",
        b"0.6000001",
    ):
        assert private_token not in six
    assert mapping.observed_log_loss == 0.6000001
    assert rival.observed_log_loss == 0.6000002


def test_visible_metric_rounds_once_from_raw_not_via_twelve_decimals() -> None:
    boundary = 0.1234565000004
    assert round(boundary, 6) != round(round(boundary, 12), 6)
    observation = replace(_observation(), reference_log_loss=boundary, corrected_log_loss=boundary)
    original = build_development_evidence_views(observation)
    visible = _coarsened_views(observation)
    for view in visible.values():
        performance = view["items"][0]["payload"]
        assert performance["reference_log_loss"] == round(boundary, 6)
        assert performance["log_loss_change"] == round(
            performance["observed_log_loss"] - performance["reference_log_loss"], 6
        )
        for item in view["items"]:
            if item["id"] == "independent-recomputation":
                assert item["payload"]["source_column_decode_log_loss"] == round(boundary, 6)
    assert original["full"]["items"][0]["payload"]["reference_log_loss"] == round(boundary, 12)
    assert observation.reference_log_loss == boundary


def test_small_raw_gap_across_a_rounding_boundary_is_not_an_identical_payload() -> None:
    mapping = replace(_observation(), observed_log_loss=0.60000049)
    rival = replace(mapping, observed_log_loss=0.60000051)
    assert abs(mapping.observed_log_loss - rival.observed_log_loss) < MAX_ABSOLUTE_LOSS_GAP
    assert serialize_development_evidence_view(
        mapping, condition="missing_key", metric_decimal_places=6
    ) != serialize_development_evidence_view(
        rival, condition="missing_key", metric_decimal_places=6
    )


@pytest.mark.parametrize("precision", [True, False, 0, 5, 7, 13])
def test_reader_rejects_undeclared_precision(precision: int) -> None:
    with pytest.raises(ScoreMappingEvidenceError, match="resolution"):
        serialize_development_evidence_view(
            _observation(), condition="missing_key", metric_decimal_places=precision
        )


def test_reader_rejects_unknown_condition_and_keeps_zero_canonical() -> None:
    with pytest.raises(ScoreMappingEvidenceError, match="unknown evidence condition"):
        serialize_development_evidence_view(
            _observation(),
            condition="raw_audit",  # type: ignore[arg-type]
            metric_decimal_places=6,
        )
    observation = replace(_observation(), reference_log_loss=0.0000001, observed_log_loss=0.0)
    payload = serialize_development_evidence_view(
        observation, condition="missing_key", metric_decimal_places=6
    )
    assert b"-0.0" not in payload
    assert json.loads(payload)["items"][0]["payload"]["log_loss_change"] == 0.0


def test_private_predecessor_cannot_become_output_parent(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    prior = tmp_path / "prior"
    prior.mkdir()
    with pytest.raises(DevelopmentSymptomStudyError, match="read-only"):
        run_development_symptom_study(root=root, prior=prior, output=prior / "new")
    assert not (prior / "new").exists()


def test_pinned_predecessor_identity_and_artifact_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = tmp_path / "prior"
    prior.mkdir()
    summary = prior / "summary.json"
    payload = {
        "schema_version": "score-mapping-development-feasibility/v1",
        "status": "development_only",
        "cell_count": 4,
        "registered_attempt": False,
        "provider_calls": 0,
        "sealed_predictions_or_metrics_computed": False,
    }
    summary.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "aletheia_lab.benchmark.p2.score_mapping_development_symptoms.PRIOR_SUMMARY_SHA256",
        file_sha256(summary),
    )
    assert _checked_prior_summary(prior) == payload
    _required_artifact(summary, file_sha256(summary))

    summary.write_text(json.dumps({**payload, "provider_calls": 1}), encoding="utf-8")
    with pytest.raises(DevelopmentSymptomStudyError, match="bytes changed"):
        _checked_prior_summary(prior)
    with pytest.raises(DevelopmentSymptomStudyError, match="missing or changed"):
        _required_artifact(summary, "0" * 64)


def test_forward_dose_replays_real_synthetic_scores_and_rejects_metric_drift(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    replay = _CellReplay(
        witness=case.witness,
        source=case.source,
        model=case.model,
        calibration=case.calibration,
        artifacts=case.artifacts,
        train=case.features + 1.0,
        development=case.features,
        dev_ids=case.ids,
        dev_targets=tuple(label for _, label in case.targets),
        target_rows=case.targets,
        healthy_scores=tuple(row[1] for row in case.witness.calibrated_score_rows),
    )
    for dose in (0, 4):
        intervention = apply_evaluator_mapping_fault(case.source, selected_shard_count=dose)
        verified = verify_evaluator_mapping(
            witness=case.witness,
            source=case.source,
            intervention=intervention,
            scoring_target_rows=case.targets,
            reference_model=case.model,
            evaluation_matrix=case.features,
            reference_calibration=case.calibration,
            artifacts=case.artifacts,
        )
        previous = {
            "selected_shards": dose,
            "healthy_log_loss": verified.healthy_log_loss,
            "faulty_log_loss": verified.faulty_log_loss,
            "corrected_log_loss": verified.corrected_log_loss,
            "affected_rows": len(verified.affected_record_ids),
            "changed_score_rows": len(verified.changed_score_record_ids),
            "achieved_fraction": verified.achieved_affected_fraction,
            "delta_log_loss": verified.faulty_log_loss - verified.healthy_log_loss,
        }
        measured = _measure_dose(replay, previous)
        assert measured["mapping_log_loss"] == verified.faulty_log_loss
        assert measured["affected_rows"] == len(verified.affected_record_ids)
        if dose == 0:
            assert measured["status"] == "zero_or_flat_control"
        else:
            assert measured["status"] in {
                "resolution_matched_pair",
                "unmatched_or_shortcut",
                "no_eligible_rival_pair",
            }
            assert measured["mapping_delta_log_loss"] > 0

        with pytest.raises(DevelopmentSymptomStudyError, match="metric does not replay"):
            _measure_dose(replay, {**previous, "faulty_log_loss": 0.0})


@pytest.mark.parametrize("one_unmatched", [False, True])
def test_offline_study_requires_all_four_cells_for_a_development_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, one_unmatched: bool
) -> None:
    root = tmp_path / "repo"
    prior = tmp_path / "prior"
    output = tmp_path / "private" / "study"
    for path in (root, prior, output.parent):
        path.mkdir()
    datasets = tuple(
        SimpleNamespace(
            dataset_id=f"development-{index}",
            role=f"role-{index}",
            archive=SimpleNamespace(file_name=f"dataset-{index}.bin"),
        )
        for index in range(2)
    )
    receipts = tuple(
        SimpleNamespace(dataset_id=dataset.dataset_id, role=dataset.role) for dataset in datasets
    )
    protocol = SimpleNamespace(dataset_splits=receipts, canonical_sha256=lambda: "a" * 64)
    prior_cells = [
        {"dataset_id": dataset.dataset_id, "model_kind": kind}
        for dataset in datasets
        for kind in ("logistic_regression", "hist_gradient_boosting")
    ]
    monkeypatch.setattr(
        symptoms,
        "_checked_prior_summary",
        lambda _prior: {"protocol_sha256": "a" * 64, "cells": prior_cells},
    )
    monkeypatch.setattr(symptoms, "load_v3_confirmatory_protocol", lambda _path: protocol)
    monkeypatch.setattr(
        symptoms,
        "verify_v3_protocol_artifacts",
        lambda _protocol, *, root: (None, SimpleNamespace(datasets=datasets), None),
    )
    monkeypatch.setattr(
        symptoms,
        "load_v3_dataset_snapshot_for_registration",
        lambda *, dataset, archive_path: (None, object()),
    )
    monkeypatch.setattr(symptoms, "reconstruct_runtime_split", lambda **kwargs: object())
    visited: list[tuple[str, str]] = []

    def synthetic_cell(**kwargs: object) -> dict[str, object]:
        dataset = kwargs["dataset"]
        kind = kwargs["kind"]
        assert isinstance(dataset, SimpleNamespace)
        assert isinstance(kind, str)
        visited.append((dataset.dataset_id, kind))
        matched = not (one_unmatched and dataset == datasets[0] and kind == "logistic_regression")
        return {
            "dataset_id": dataset.dataset_id,
            "model_kind": kind,
            "measurements": [
                {
                    "selected_shards": dose,
                    "status": ("resolution_matched_pair" if matched else "unmatched_or_shortcut"),
                }
                for dose in (0, 1, 2, 4)
            ],
        }

    monkeypatch.setattr(symptoms, "_cell_study", synthetic_cell)
    result = run_development_symptom_study(root=root, prior=prior, output=output)

    assert result["registered_attempt"] is False
    assert result["provider_calls"] == 0
    assert result["sealed_predictions_or_metrics_computed"] is False
    assert result["cell_count"] == 4
    assert len(visited) == 4
    assert result["candidate_selected_shards"] == (None if one_unmatched else 1)
    assert result["status"] == (
        "exploratory_unmatched" if one_unmatched else "development_candidate"
    )
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == result
