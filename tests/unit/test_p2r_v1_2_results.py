"""P2R v1.2 publication and content-addressed preservation tests."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.p2r_v1_2_results import (
    TERMINAL_STORE_SHA256,
    P2RV12PublicationSummary,
    load_p2r_v1_2_publication_summary,
    preservation_destination,
    preserve_p2r_v1_2_evidence,
    verify_p2r_v1_2_publication_summary,
    verify_preserved_p2r_v1_2,
)

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "experiments/p2/outputs/p2r-confirmatory-v1-2"


def _summary_payload() -> dict[str, object]:
    return load_p2r_v1_2_publication_summary().model_dump(mode="json")


def _copy_store(tmp_path: Path) -> Path:
    destination = tmp_path / "source/experiments/p2/outputs/p2r-confirmatory-v1-2"
    destination.parent.mkdir(parents=True)
    shutil.copytree(STORE, destination)
    return destination.parents[3]


def test_publication_summary_cannot_convert_rejection_to_admission() -> None:
    payload = _summary_payload()
    mechanisms = payload["mechanisms"]
    assert isinstance(mechanisms, list)
    drift = mechanisms[0]
    assert isinstance(drift, dict)
    drift["disposition"] = "admitted"
    drift["admitted"] = True
    with pytest.raises(ValidationError):
        P2RV12PublicationSummary.model_validate_json(json.dumps(payload))


@pytest.mark.skipif(not STORE.exists(), reason="ignored immutable P2R v1.2 store unavailable")
@pytest.mark.large_artifact
def test_tracked_summary_reproduces_every_terminal_decision() -> None:
    summary = verify_p2r_v1_2_publication_summary(
        load_p2r_v1_2_publication_summary(), terminal_store_path=STORE
    )
    assert summary.terminal_store_sha256 == TERMINAL_STORE_SHA256
    assert summary.n_measurements == 20
    assert summary.n_paired_observations == 10
    assert tuple(item.disposition for item in summary.mechanisms) == (
        "rejected",
        "rejected",
    )
    assert all(
        result.manipulation_fidelity_pass
        for mechanism in summary.mechanisms
        for result in mechanism.dataset_results
    )
    assert not any(
        result.target_effect_pass or result.direction_pass
        for mechanism in summary.mechanisms
        for result in mechanism.dataset_results
    )


@pytest.mark.skipif(not STORE.exists(), reason="ignored immutable P2R v1.2 store unavailable")
@pytest.mark.large_artifact
def test_metric_or_provenance_tamper_does_not_verify() -> None:
    payload = _summary_payload()
    mechanisms = payload["mechanisms"]
    assert isinstance(mechanisms, list)
    drift = mechanisms[0]
    assert isinstance(drift, dict)
    results = drift["dataset_results"]
    assert isinstance(results, list)
    primary = results[0]
    assert isinstance(primary, dict)
    primary["median_target_effect"] = 0.02
    summary = P2RV12PublicationSummary.model_validate_json(json.dumps(payload))
    with pytest.raises(V3RuntimeError, match="does not reproduce"):
        verify_p2r_v1_2_publication_summary(summary, terminal_store_path=STORE)


@pytest.mark.skipif(not STORE.exists(), reason="ignored immutable P2R v1.2 store unavailable")
@pytest.mark.large_artifact
def test_preservation_is_content_addressed_read_only_and_idempotent(tmp_path: Path) -> None:
    source_root = _copy_store(tmp_path)
    archive = tmp_path / "archive"
    receipt = preserve_p2r_v1_2_evidence(
        root=source_root,
        preservation_root=archive,
        preserved_at=datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
    )
    destination = preservation_destination(archive)
    assert destination.name == f"sha256-{TERMINAL_STORE_SHA256}"
    assert receipt == verify_preserved_p2r_v1_2(destination)
    assert receipt == preserve_p2r_v1_2_evidence(root=source_root, preservation_root=archive)
    assert receipt.original_result_store_modified is False


@pytest.mark.skipif(not STORE.exists(), reason="ignored immutable P2R v1.2 store unavailable")
@pytest.mark.large_artifact
def test_preservation_rejects_unexpected_or_tampered_content(tmp_path: Path) -> None:
    source_root = _copy_store(tmp_path)
    store = source_root / "experiments/p2/outputs/p2r-confirmatory-v1-2"
    (store / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(V3RuntimeError, match="missing or unexpected"):
        preserve_p2r_v1_2_evidence(root=source_root, preservation_root=tmp_path / "archive-one")

    (store / "unexpected.json").unlink()
    archive = tmp_path / "archive-two"
    preserve_p2r_v1_2_evidence(root=source_root, preservation_root=archive)
    destination = preservation_destination(archive)
    target = destination / "result-store/closeout.json"
    target.chmod(0o644)
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(V3RuntimeError):
        verify_preserved_p2r_v1_2(destination)
