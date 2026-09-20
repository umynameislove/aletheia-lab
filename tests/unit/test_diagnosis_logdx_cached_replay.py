from __future__ import annotations

import json
from pathlib import Path

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / "configs/evaluation/diagnosis_logdx_cached_evaluator_replay.json"


def test_logdx_cached_evaluator_replay_is_byte_exact_on_published_target() -> None:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    declared = payload.pop("receipt_sha256")

    assert canonical_execution_sha256(payload) == declared
    assert payload["source"]["release"] == "v1.2"
    assert payload["source"]["dereferenced_commit_sha"] == (
        "99591c1471118c95155976346df72f520a05f100"
    )
    assert payload["corpus"]["case_count"] == 35
    assert sum(payload["corpus"]["split_counts"].values()) == 35
    assert payload["primary_cached_replay"]["split"] == "v2/dev"
    assert payload["primary_cached_replay"]["byte_exact"] is True
    assert payload["primary_cached_replay"]["published_result_file_sha256"] == (
        payload["primary_cached_replay"]["recomputed_result_file_sha256"]
    )
    assert payload["external_provider_calls_executed"] is False
    assert payload["fresh_diagnoser_regeneration_attempted"] is False
    assert payload["end_to_end_native_reproduction_claimed"] is False


def test_supplementary_rounding_observations_are_not_hidden_by_a_post_hoc_tolerance() -> None:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    supplementary = payload["supplementary_cached_replays"]
    non_exact = [item for item in supplementary if not item["byte_exact"]]

    assert {item["split"] for item in non_exact} == {"stress", "v2/stress"}
    assert sum(len(item["rounding_differences"]) for item in non_exact) == 4
    assert all("tolerance" not in item for item in supplementary)


def test_logdx_remains_external_and_truth_limitation_is_explicit() -> None:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    boundaries = payload["reporting_boundaries"]

    assert boundaries["pool_with_controlled_ml_families"] is False
    assert boundaries["pool_native_and_aletheia_metrics"] is False
    assert boundaries["treat_truth_as_independent_human_gold"] is False
    assert "native_diagnoser_reproduction" in boundaries["forbidden_claims"]
    assert "ground_truth_ai_drafted_and_single_author_verified" in boundaries["limitations"]
    assert payload["adapter_mapping"]["ground_truth.json"].startswith("evaluator_only")
