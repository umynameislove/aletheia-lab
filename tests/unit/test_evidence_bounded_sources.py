"""Source replay requires measured intervention and corrected target lineage."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_evidence_bounded_pilot import cases as fixture_cases
from test_score_mapping_evidence import _case

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    build_development_evidence_views,
    mapping_observation,
    target_binding_rival_observation,
)
from aletheia_lab.evaluation import evidence_bounded_sources as sources
from aletheia_lab.evaluation.evidence_bounded_sources import _paired_cases, replay_development_cases


def test_real_typed_interventions_reconstruct_complete_pair_and_reject_drift(tmp_path):
    case = _case(tmp_path)
    replay = SimpleNamespace(
        source=case.source,
        witness=case.witness,
        target_rows=case.targets,
        model=case.model,
        calibration=case.calibration,
        artifacts=case.artifacts,
        train=case.reference_features,
        development=case.evaluation_features,
    )
    mapping = mapping_observation(
        witness=case.witness,
        source=case.source,
        intervention=case.intervention,
        scoring_target_rows=case.targets,
        reference_model=case.model,
        reference_calibration=case.calibration,
        artifacts=case.artifacts,
        reference_features=case.reference_features,
        evaluation_features=case.evaluation_features,
    )
    rival_targets = tuple((row_id, 1 - label) for row_id, label in case.targets)
    rival = target_binding_rival_observation(
        witness=case.witness,
        source=case.source,
        scoring_target_rows=rival_targets,
        reference_features=case.reference_features,
        evaluation_features=case.evaluation_features,
    )
    item = {
        "status": "resolution_matched_pair",
        "selected_shards": 4,
        "swapped_pairs": ((case.ids[0], case.ids[1]),),
        "mapping_log_loss": mapping.observed_log_loss,
        "rival_log_loss": rival.observed_log_loss,
        "mapping_view_sha256": {
            k: canonical_sha256(v)
            for k, v in build_development_evidence_views(mapping, metric_decimal_places=6).items()
        },
        "rival_view_sha256": {
            k: canonical_sha256(v)
            for k, v in build_development_evidence_views(rival, metric_decimal_places=6).items()
        },
    }
    cases = _paired_cases(replay, item, "synthetic", "fixture")
    assert len(cases) == 8
    assert {c["reference"]["status"] for c in cases} == {"ambiguous", "resolved"}
    missing = [c["context"] for c in cases if c["condition"] == "missing_key"]
    assert missing[0] == missing[1]
    with pytest.raises(ValueError, match="metrics"):
        _paired_cases(replay, {**item, "rival_log_loss": 0}, "synthetic", "fixture")
    with pytest.raises(ValueError, match="unmatched"):
        _paired_cases(replay, {**item, "status": "unmatched"}, "synthetic", "fixture")
    with pytest.raises(ValueError, match="projection"):
        _paired_cases(
            replay,
            {**item, "mapping_view_sha256": {k: "f" * 64 for k in item["mapping_view_sha256"]}},
            "synthetic",
            "fixture",
        )


def test_source_replay_keeps_complete_census_and_rejects_predecessor_drift(tmp_path, monkeypatch):
    datasets = [
        SimpleNamespace(dataset_id=name, archive=SimpleNamespace(file_name=f"{name}.zip"))
        for name in ("source-one", "source-two")
    ]
    cells = [
        {"dataset_id": d.dataset_id, "model_kind": kind}
        for d in datasets
        for kind in sources.MODEL_KINDS
    ]
    summary = {
        "schema_version": "score-mapping-development-symptom-study/v1",
        "status": "development_candidate",
        "visible_metric_decimal_places": 6,
        "provider_calls": 0,
        "registered_attempt": False,
        "sealed_predictions_or_metrics_computed": False,
        "protocol_sha256": "protocol-digest",
        "cells": [
            {**c, "measurements": [{"selected_shards": n} for n in (0, 1, 2, 3)]} for c in cells
        ],
    }
    protocol = SimpleNamespace(
        dataset_splits=("split-one", "split-two"), canonical_sha256=lambda: "protocol-digest"
    )
    archive_paths = []
    monkeypatch.setattr(sources, "_private_dir", lambda path: path)
    monkeypatch.setattr(sources, "_checked_prior_summary", lambda path: {"cells": cells})
    monkeypatch.setattr(sources, "_read", lambda path: summary)
    monkeypatch.setattr(sources, "load_v3_confirmatory_protocol", lambda path: protocol)
    monkeypatch.setattr(
        sources,
        "verify_v3_protocol_artifacts",
        lambda protocol, *, root: (None, SimpleNamespace(datasets=datasets), None),
    )

    def snapshot(*, dataset, archive_path):
        archive_paths.append(archive_path)
        return None, "training/development-frame"

    monkeypatch.setattr(sources, "load_v3_dataset_snapshot_for_registration", snapshot)
    monkeypatch.setattr(sources, "reconstruct_runtime_split", lambda **kwargs: "frozen-split")
    monkeypatch.setattr(sources, "load_development_cell", lambda **kwargs: "replayed-artifact")
    monkeypatch.setattr(sources, "file_sha256", lambda path: "a" * 64)

    def pair(replay, item, source, kind):
        assert replay == "replayed-artifact" and item["selected_shards"] != 0
        return [
            {**c, "pair_id": f"{source}-{kind}-{item['selected_shards']}", "source_cluster": source}
            for c in fixture_cases()
        ]

    monkeypatch.setattr(sources, "_paired_cases", pair)
    root, source_root = tmp_path / "code", tmp_path / "existing-sources"
    cases, hashes = replay_development_cases(
        root=root, source_root=source_root, memory_root=tmp_path
    )
    assert len(cases) == 96 and len({c["pair_id"] for c in cases}) == 12
    assert len(hashes) == 3 and len(archive_paths) == 2
    assert all(p.is_relative_to(source_root) for p in archive_paths)
    summary["provider_calls"] = 1
    with pytest.raises(ValueError, match="incompatible"):
        replay_development_cases(root=root, memory_root=tmp_path)
    summary["provider_calls"] = 0
    summary["protocol_sha256"] = "different-protocol"
    with pytest.raises(ValueError, match="protocol differs"):
        replay_development_cases(root=root, memory_root=tmp_path)
    summary["protocol_sha256"] = "protocol-digest"
    summary["cells"][0]["measurements"].pop()
    with pytest.raises(ValueError, match="census"):
        replay_development_cases(root=root, memory_root=tmp_path)
