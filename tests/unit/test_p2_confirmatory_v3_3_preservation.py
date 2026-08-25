"""Tests for byte-exact v3.3 preservation and compact publication facts."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_3_preservation import (
    TERMINAL_STORE_SHA256,
    V33PublicationSummary,
    load_v3_3_publication_summary,
    preservation_destination,
    preserve_v3_3_evidence,
    verify_preserved_v3_3,
    verify_v3_3_publication_summary,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "experiments/p2/outputs/label-noise-shift-factorial-v3.3"


def _copy_source_evidence(tmp_path: Path) -> Path:
    source_root = tmp_path / "source"
    output = source_root / "experiments/p2/outputs"
    output.mkdir(parents=True)
    shutil.copytree(_STORE, output / "label-noise-shift-factorial-v3.3")
    shutil.copyfile(
        _ROOT / "experiments/p2/outputs/label-noise-shift-factorial-v3.3-registration.json",
        output / "label-noise-shift-factorial-v3.3-registration.json",
    )
    shutil.copyfile(
        _ROOT / "experiments/p2/outputs/label-noise-shift-factorial-v3.3-sealed-open.json",
        output / "label-noise-shift-factorial-v3.3-sealed-open.json",
    )
    return source_root


@pytest.mark.skipif(not _STORE.exists(), reason="ignored immutable v3.3 terminal store unavailable")
@pytest.mark.large_artifact
def test_tracked_publication_summary_reproduces_from_terminal_store() -> None:
    summary = verify_v3_3_publication_summary(
        load_v3_3_publication_summary(), terminal_store_path=_STORE
    )
    assert summary.disposition == "abstain"
    assert summary.cross_dataset_claim_allowed is False
    assert summary.terminal_store_sha256 == TERMINAL_STORE_SHA256
    assert tuple(item.assumptions_pass for item in summary.assumption_environments) == (
        False,
        True,
        False,
    )


def test_publication_summary_cannot_turn_abstention_into_admission() -> None:
    payload = load_v3_3_publication_summary().model_dump(mode="json")
    payload["cross_dataset_claim_allowed"] = True
    with pytest.raises(ValueError):
        V33PublicationSummary.model_validate_json(json.dumps(payload))


@pytest.mark.skipif(not _STORE.exists(), reason="ignored immutable v3.3 terminal store unavailable")
@pytest.mark.large_artifact
def test_preservation_copy_is_content_addressed_and_idempotently_verified(tmp_path: Path) -> None:
    source_root = _copy_source_evidence(tmp_path)
    archive = tmp_path / "archive"
    receipt = preserve_v3_3_evidence(
        root=source_root,
        preservation_root=archive,
        preserved_at=datetime(2026, 8, 24, 9, 30, tzinfo=UTC),
    )
    destination = preservation_destination(archive)
    assert destination.name == f"sha256-{TERMINAL_STORE_SHA256}"
    assert receipt == verify_preserved_v3_3(destination)
    assert receipt == preserve_v3_3_evidence(root=source_root, preservation_root=archive)
    assert receipt.copy_is_byte_identical is True
    assert receipt.original_result_store_modified is False


@pytest.mark.skipif(not _STORE.exists(), reason="ignored immutable v3.3 terminal store unavailable")
@pytest.mark.large_artifact
def test_preservation_rejects_unexpected_source_artifact(tmp_path: Path) -> None:
    source_root = _copy_source_evidence(tmp_path)
    store = source_root / "experiments/p2/outputs/label-noise-shift-factorial-v3.3"
    (store / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(V3RuntimeError, match="missing or unexpected"):
        preserve_v3_3_evidence(root=source_root, preservation_root=tmp_path / "archive")


@pytest.mark.skipif(not _STORE.exists(), reason="ignored immutable v3.3 terminal store unavailable")
@pytest.mark.large_artifact
def test_preserved_copy_detects_byte_tamper(tmp_path: Path) -> None:
    source_root = _copy_source_evidence(tmp_path)
    archive = tmp_path / "archive"
    preserve_v3_3_evidence(root=source_root, preservation_root=archive)
    destination = preservation_destination(archive)
    target = destination / "result-store/closeout.json"
    target.chmod(0o644)
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(V3RuntimeError, match="cannot load|checksum"):
        verify_preserved_v3_3(destination)


@pytest.mark.skipif(not _STORE.exists(), reason="ignored immutable v3.3 terminal store unavailable")
@pytest.mark.large_artifact
def test_preservation_rejects_symlinked_source_artifact(tmp_path: Path) -> None:
    source_root = _copy_source_evidence(tmp_path)
    store = source_root / "experiments/p2/outputs/label-noise-shift-factorial-v3.3"
    target = store / "closeout.json"
    replacement = tmp_path / "replacement"
    target.rename(replacement)
    target.symlink_to(replacement)
    with pytest.raises(V3RuntimeError, match="non-regular"):
        preserve_v3_3_evidence(root=source_root, preservation_root=tmp_path / "archive")
