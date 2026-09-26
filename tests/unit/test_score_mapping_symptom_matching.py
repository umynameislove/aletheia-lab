"""The evaluator-mapping hard negative cannot change scores or priors."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_mapping_development_symptoms import (
    DevelopmentSymptomStudyError,
    _coarsened_views,
    run_development_symptom_study,
)
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    DevelopmentObservation,
    build_development_evidence_views,
)
from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import (
    MAX_ABSOLUTE_LOSS_GAP,
    VISIBLE_METRIC_DECIMALS,
    SymptomMatchingError,
    match_target_swaps,
    verify_target_swap_match,
)


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


def test_private_predecessor_cannot_become_output_parent(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    prior = tmp_path / "prior"
    prior.mkdir()
    with pytest.raises(DevelopmentSymptomStudyError, match="read-only"):
        run_development_symptom_study(root=root, prior=prior, output=prior / "new")
    assert not (prior / "new").exists()
