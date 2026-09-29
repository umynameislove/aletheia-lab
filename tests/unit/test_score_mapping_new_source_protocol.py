"""Synthetic public-source fixtures for the prediction-blind M5 inventory."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest

from aletheia_lab.benchmark.p2 import score_mapping_new_source_protocol as inventory
from aletheia_lab.content_hashing import file_sha256

REPO = Path(__file__).resolve().parents[2]


def test_checked_in_protocol_matches_frozen_digest() -> None:
    assert file_sha256(REPO / inventory.PROTOCOL_PATH) == inventory.FROZEN_PROTOCOL_SHA256


def _source_spec(directory: Path, *, rice: bool) -> dict[str, object]:
    name = "rice_cammeo_osmancik" if rice else "htru2"
    member = "Rice_Cammeo_Osmancik.arff" if rice else "HTRU_2.csv"
    rows = [
        f"{index / 10:.1f},{(index % 13) / 10:.1f},{'Cammeo' if index % 2 else 'Osmancik'}"
        if rice
        else f"{index / 10:.1f},{(index % 13) / 10:.1f},{index % 2}"
        for index in range(200)
    ]
    rows.append(rows[0])
    header = (
        "@RELATION synthetic\n@ATTRIBUTE x NUMERIC\n@ATTRIBUTE y NUMERIC\n"
        "@ATTRIBUTE Class {Cammeo, Osmancik}\n@DATA\n"
        if rice
        else ""
    )
    payload = (header + "\n".join(rows) + "\n").encode()
    path = directory / f"{name}.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr(member, payload)
    return {
        "dataset_id": name,
        "source_family": "rice_grain_morphology" if rice else "radio_pulsar_candidates",
        "license": "CC-BY-4.0",
        "archive_filename": path.name,
        "archive_bytes": path.stat().st_size,
        "archive_sha256": file_sha256(path),
        "member": member,
        "member_bytes": len(payload),
        "member_sha256": hashlib.sha256(payload).hexdigest(),
        "format": "arff_numeric_last_target" if rice else "csv_no_header_numeric_last_target",
        "row_count": len(rows),
        "feature_count": 2,
        "target_encoding": {"Osmancik": 0, "Cammeo": 1} if rice else {"0": 0, "1": 1},
    }


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, object]]:
    root, sources = tmp_path / "repo", tmp_path / "sources"
    (root / "configs/benchmark").mkdir(parents=True)
    sources.mkdir()
    protocol = json.loads((REPO / inventory.PROTOCOL_PATH).read_text())
    protocol["sources"] = [_source_spec(sources, rice=False), _source_spec(sources, rice=True)]
    (root / inventory.PROTOCOL_PATH).write_text(json.dumps(protocol))
    monkeypatch.setattr(
        inventory, "FROZEN_PROTOCOL_SHA256", file_sha256(root / inventory.PROTOCOL_PATH)
    )
    return root, sources, protocol


def test_inventory_covers_all_rows_and_keeps_feature_duplicates_grouped(
    study: tuple[Path, Path, dict[str, object]],
) -> None:
    root, sources, _ = study
    result = inventory.audit_new_source_protocol(root=root, sources=sources)
    assert result["status"] == "prediction_blind_source_inventory_pass"
    assert result["source_cluster_count"] == 2 and result["cell_count"] == 4
    assert result["model_fitted"] is False
    assert result["final_predictions_or_metrics_computed"] is False
    for source in result["sources"]:
        assert sum(item["rows"] for item in source["partitions"].values()) == 201
        assert source["feature_duplicate_rows"] == 1
        assert all(
            all(count > 0 for count in item["class_counts"])
            for item in source["partitions"].values()
        )


@pytest.mark.parametrize("mutation", ["archive", "source", "split", "authorization", "destination"])
def test_inventory_fails_closed_on_drift(
    study: tuple[Path, Path, dict[str, object]], mutation: str
) -> None:
    root, sources, protocol = study
    altered = deepcopy(protocol)
    if mutation == "archive":
        (sources / "htru2.zip").write_bytes(b"changed")
    elif mutation == "source":
        altered["sources"][0]["source_family"] = "rice_grain_morphology"
    elif mutation == "split":
        altered["split"]["seed"] = "outcome-selected"
    elif mutation == "authorization":
        altered["final_execution_authorized"] = True
    else:
        sources = root / "sources"
        sources.mkdir()
    (root / inventory.PROTOCOL_PATH).write_text(json.dumps(altered))
    with pytest.raises(inventory.NewSourceProtocolError):
        inventory.audit_new_source_protocol(root=root, sources=sources)
