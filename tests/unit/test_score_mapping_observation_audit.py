"""Synthetic checks for the finite M5 reader-channel audit."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest

from aletheia_lab.benchmark.p2.score_mapping_observation_audit import (
    ObservationAuditError,
    audit_development_observation_summaries,
)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _summaries() -> tuple[dict[str, Any], dict[str, Any]]:
    mapping_cells = []
    target_cells = []
    for source in ("source-a", "source-b"):
        for estimator in ("logistic_regression", "hist_gradient_boosting"):
            cell_id = f"{source}-{estimator}"
            raw_hash = _digest(f"{cell_id}-raw-scores")
            calibrated_hash = _digest(f"{cell_id}-calibrated-scores")
            target_hash = _digest(f"{source}-targets")
            mapping_doses = []
            target_doses = []
            for dose in (0, 1, 2, 4):
                target_measurement: dict[str, Any] = {
                    "selected_shards": dose,
                    "score_source_sha256": calibrated_hash,
                    "source_target_sha256": target_hash,
                }
                if dose == 0:
                    mapping_doses.append(
                        {
                            "selected_shards": 0,
                            "status": "zero_or_flat_control",
                            "affected_rows": 0,
                            "mapping_delta_log_loss": 0.0,
                        }
                    )
                    target_measurement.update(
                        {
                            "changed_target_count": 0,
                            "healthy_log_loss": 0.2,
                            "faulty_log_loss": 0.2,
                        }
                    )
                else:
                    pair_id = f"{cell_id}-{dose}"
                    mapping_doses.append(
                        {
                            "selected_shards": dose,
                            "status": "resolution_matched_pair",
                            "source_identity_shared": True,
                            "full_witness_distinguishes": True,
                            "visible_missing_key_identical": True,
                            "twelve_decimal_missing_key_identical": False,
                            "rival_changed_target_rows": 2,
                            "mapping_view_sha256": {
                                view: _digest(
                                    f"{pair_id}-{view}"
                                    if view == "missing_key"
                                    else f"{pair_id}-{view}-mapping"
                                )
                                for view in ("full", "missing_key", "noisy", "misleading")
                            },
                            "rival_view_sha256": {
                                view: _digest(
                                    f"{pair_id}-{view}"
                                    if view == "missing_key"
                                    else f"{pair_id}-{view}-rival"
                                )
                                for view in ("full", "missing_key", "noisy", "misleading")
                            },
                        }
                    )
                    target_measurement["adversarial_paired_binding"] = {
                        "class_counts_preserved": True,
                        "correction_exact": True,
                        "selection_uses_development_mapping_metric": True,
                        "changed_target_count": 2,
                        "pair_count": 1,
                        "payload_equality": {
                            "6": {"full": False, "missing_key": True},
                            "12": {"full": False, "missing_key": False},
                        },
                    }
                target_doses.append(target_measurement)
            mapping_cells.append(
                {
                    "dataset_id": source,
                    "model_kind": estimator,
                    "development_count": 20,
                    "source_score_sha256": raw_hash,
                    "source_target_binding_sha256": target_hash,
                    "measurements": mapping_doses,
                }
            )
            target_cells.append(
                {
                    "dataset_id": source,
                    "model_kind": estimator,
                    "development_count": 20,
                    "measurements": target_doses,
                }
            )
    common: dict[str, Any] = {
        "provider_calls": 0,
        "registered_attempt": False,
        "sealed_predictions_or_metrics_computed": False,
    }
    return (
        {
            **common,
            "schema_version": "score-mapping-development-symptom-study/v1",
            "status": "development_candidate",
            "visible_metric_decimal_places": 6,
            "cells": mapping_cells,
        },
        {
            **common,
            "schema_version": "target-binding-development/v2",
            "status": "development_only",
            "matched_predecessor_byte_sha256": _digest("mapping-summary"),
            "independently_admitted": False,
            "cells": target_cells,
        },
    )


def test_complete_paired_reader_has_exact_finite_shortcut_ceiling() -> None:
    mapping, target = _summaries()
    audit = audit_development_observation_summaries(
        mapping, target, mapping_summary_sha256=_digest("mapping-summary")
    )
    assert audit["positive_matched_pairs"] == 12
    assert audit["zero_controls"] == 4
    assert audit["optimal_balanced_lookup_accuracy_by_view"] == {
        "full": 1.0,
        "missing_key": 0.5,
        "noisy": 1.0,
        "misleading": 1.0,
    }
    assert audit["twelve_decimal_missing_key_pairs_distinct"] == 12
    assert audit["mechanism_admitted"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_dose",
        "missing_source",
        "score_drift",
        "target_drift",
        "view_leak",
        "full_blind",
        "correction_failed",
        "precision_misreported",
        "predecessor_mismatch",
        "sealed_claim",
        "cross_pair_canonical_collision",
    ],
)
def test_incomplete_or_leaking_development_evidence_fails_closed(mutation: str) -> None:
    mapping, target = deepcopy(_summaries())
    mapping_item = mapping["cells"][0]["measurements"][1]
    target_item = target["cells"][0]["measurements"][1]
    if mutation == "missing_dose":
        mapping["cells"][0]["measurements"].pop()
    elif mutation == "missing_source":
        mapping["cells"].pop()
    elif mutation == "score_drift":
        target_item["score_source_sha256"] = _digest("changed")
    elif mutation == "target_drift":
        target_item["source_target_sha256"] = _digest("changed")
    elif mutation == "view_leak":
        mapping_item["rival_view_sha256"]["missing_key"] = _digest("leaked")
    elif mutation == "full_blind":
        mapping_item["rival_view_sha256"]["full"] = mapping_item["mapping_view_sha256"]["full"]
    elif mutation == "correction_failed":
        target_item["adversarial_paired_binding"]["correction_exact"] = False
    elif mutation == "precision_misreported":
        target_item["adversarial_paired_binding"]["payload_equality"]["12"]["missing_key"] = True
    elif mutation == "predecessor_mismatch":
        target["matched_predecessor_byte_sha256"] = _digest("other")
    elif mutation == "cross_pair_canonical_collision":
        mapping["cells"][0]["measurements"][2]["rival_view_sha256"]["noisy"] = mapping_item[
            "mapping_view_sha256"
        ]["noisy"]
    else:
        mapping["sealed_predictions_or_metrics_computed"] = True
    with pytest.raises(ObservationAuditError):
        audit_development_observation_summaries(
            mapping, target, mapping_summary_sha256=_digest("mapping-summary")
        )
