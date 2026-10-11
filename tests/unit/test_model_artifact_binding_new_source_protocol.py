"""Prediction-blind pins, parsing, leakage grouping and conditional design."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from aletheia_lab.benchmark.p2 import model_artifact_binding_new_source_protocol as inventory
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def test_real_prospective_design_pins_not_execution_authority() -> None:
    protocol = inventory.load_m4_protocol(ROOT)
    assert protocol["model"]["A_iterations"] == 100 and protocol["model"]["B_iterations"] == 1
    assert protocol["analysis"]["minimum_raw_loss_delta"] == 0.01
    assert protocol["analysis"]["healthy_raw_reference_loss_below_log_2_required"] is True
    assert protocol["analysis"]["raw_empirical_loss_delta_positive_required"] is True
    assert protocol["reader"]["missing_key_removes"].startswith("artifact-load-binding-only")
    assert protocol["reader"]["legitimate_B_is_second_fault_mechanism"] is False
    assert all(
        protocol[key] is False
        for key in (
            "provider_calls_authorized",
            "final_execution_authorized",
            "scientific_admission_authorized",
        )
    )
    assert [s["row_count"] for s in protocol["sources"]] == [1372, 569]
    assert [s["feature_count"] for s in protocol["sources"]] == [4, 30]


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    protocol = inventory.load_m4_protocol(ROOT)
    root, sources = tmp_path / "repo", tmp_path / "private-sources"
    (root / "configs/benchmark").mkdir(parents=True)
    sources.mkdir()
    for spec in protocol["sources"]:
        lines = []
        for row in range(600):
            features = [str(row + col / 100) for col in range(spec["feature_count"])]
            values = (
                [*features, str(row % 2)]
                if spec["format"] == "numeric-features-last-target"
                else [str(row + 100), "M" if row % 2 else "B", *features]
            )
            lines.append(",".join(values))
        payload = ("\n".join(lines) + "\n").encode()
        archive = sources / spec["archive_filename"]
        with ZipFile(archive, "w") as zipped:
            zipped.writestr(spec["member"], payload)
        spec.update(
            row_count=600,
            member_bytes=len(payload),
            member_sha256=content_sha256(payload),
            archive_bytes=archive.stat().st_size,
            archive_sha256=file_sha256(archive),
        )
    path = root / inventory.PROTOCOL_PATH
    path.write_text(json.dumps(protocol))
    monkeypatch.setattr(inventory, "FROZEN_PROTOCOL_SHA256", file_sha256(path))
    return {"root": root, "sources": sources, "protocol": protocol}


def test_inventory_no_fit_no_ID_features_and_stable_groups(prepared: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        HistGradientBoostingClassifier, "fit", lambda *_: pytest.fail("inventory fitted model")
    )
    result = inventory.audit_m4_new_sources(root=prepared["root"], sources=prepared["sources"])
    assert (
        result["row_count"] == 1200 and result["source_cluster_count"] == result["cell_count"] == 2
    )
    assert (
        result["model_fitted"]
        is result["final_predictions_or_metrics_computed"]
        is result["U4_authorized"]
        is False
    )
    assert result["provider_calls"] == 0
    for raw in prepared["protocol"]["sources"]:
        spec = inventory.ArtifactSourceSpec.model_validate(raw)
        rows = inventory.read_m4_rows(prepared["sources"], spec)
        assert len(rows[0][0]) == spec.feature_count
        assert rows[0][1] == 0 and rows[1][1] == 1
        assert (
            sum(
                c["rows"]
                for c in result["sources"][prepared["protocol"]["sources"].index(raw)][
                    "partitions"
                ].values()
            )
            == 600
        )


def test_grouping_is_target_blind_transitive_and_permutation_stable() -> None:
    rows = (((1.0,), 0, "10"), ((2.0,), 1, "10"), ((2.0,), 0, "20"), ((3.0,), 1, "30"))
    groups = inventory.component_groups(rows)
    assert groups[0] == groups[1] == groups[2] != groups[3]
    flipped = tuple((x, 1 - y, subject) for x, y, subject in rows)
    assert inventory.component_groups(flipped) == groups
    assert inventory.component_groups(tuple(reversed(rows))) == tuple(reversed(groups))
    no_subject = (((1.0,), 0, None), ((1.0,), 1, None), ((2.0,), 0, None))
    assert inventory.component_groups(no_subject)[0] == inventory.component_groups(no_subject)[1]


@pytest.mark.parametrize(
    "mutation",
    ["archive", "member", "license", "traversal", "schema", "authority", "sources", "exclusion"],
)
def test_byte_and_scientific_drift_rejected(prepared: Any, mutation: str, monkeypatch: Any) -> None:
    protocol = copy.deepcopy(prepared["protocol"])
    if mutation == "archive":
        (prepared["sources"] / protocol["sources"][0]["archive_filename"]).write_bytes(b"different")
    elif mutation == "member":
        protocol["sources"][0]["member_sha256"] = "0" * 64
    elif mutation == "license":
        protocol["sources"][0]["license"] = "unknown"
    elif mutation == "traversal":
        protocol["sources"][0]["member"] = "../untrusted.txt"
    elif mutation == "schema":
        protocol["schema_version"] = "other"
    elif mutation == "authority":
        protocol["final_execution_authorized"] = True
    elif mutation == "sources":
        protocol["sources"][0]["dataset_id"] = "htru2"
    else:
        protocol["historical_source_exclusions"] = []
    if mutation != "archive":
        path = prepared["root"] / inventory.PROTOCOL_PATH
        path.write_text(json.dumps(protocol))
        monkeypatch.setattr(inventory, "FROZEN_PROTOCOL_SHA256", file_sha256(path))
    with pytest.raises(ValueError):
        inventory.audit_m4_new_sources(root=prepared["root"], sources=prepared["sources"])


def test_missing_protocol_hash_drift_symlinks_and_repo_sources(
    prepared: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        inventory.audit_m4_new_sources(root=prepared["root"], sources=prepared["root"])
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(prepared["sources"], target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this platform")
    with pytest.raises(ValueError):
        inventory.audit_m4_new_sources(root=prepared["root"], sources=linked)
    monkeypatch.setattr(inventory, "FROZEN_PROTOCOL_SHA256", "0" * 64)
    with pytest.raises(ValueError):
        inventory.load_m4_protocol(prepared["root"])


@pytest.mark.parametrize(
    "mutation", ["NaN", "bad_target", "wrong_width", "wrong_count", "subject_ID"]
)
def test_invalid_source_rows_never_imputed_or_dropped(prepared: Any, mutation: str) -> None:
    raw_spec = prepared["protocol"]["sources"][1]
    spec = inventory.ArtifactSourceSpec.model_validate(raw_spec)
    directory = prepared["sources"]
    archive_path = directory / spec.archive_filename
    with ZipFile(archive_path) as archive:
        text = archive.read(spec.member).decode()
    lines = text.splitlines()
    row = lines[0].split(",")
    if mutation == "NaN":
        row[-1] = "NaN"
    elif mutation == "bad_target":
        row[1] = "?"
    elif mutation == "wrong_width":
        row.pop()
    elif mutation == "subject_ID":
        row[0] = ""
    else:
        lines.pop()
    lines[0] = ",".join(row)
    payload = ("\n".join(lines) + "\n").encode()
    with ZipFile(archive_path, "w") as zipped:
        zipped.writestr(spec.member, payload)
    amended = spec.model_copy(
        update={
            "member_bytes": len(payload),
            "member_sha256": content_sha256(payload),
            "archive_bytes": archive_path.stat().st_size,
            "archive_sha256": file_sha256(archive_path),
        }
    )
    with pytest.raises(ValueError):
        inventory.read_m4_rows(directory, amended)


def test_fixed_minimum_class_support_and_duplicate_accounting(prepared: Any) -> None:
    spec = inventory.ArtifactSourceSpec.model_validate(prepared["protocol"]["sources"][0])
    rows = inventory.read_m4_rows(prepared["sources"], spec)
    split = prepared["protocol"]["split"]
    bad = tuple((features, 0, subject) for features, _, subject in rows)
    with pytest.raises(ValueError, match="minimum binary"):
        inventory.source_inventory(spec, bad, split)
    duplicated = rows + (rows[0], (rows[0][0], 1, None))
    result = inventory.source_inventory(spec, duplicated, split)
    assert result["feature_duplicate_rows"] == 2
    assert result["conflicting_target_feature_groups"] == 1
    assert result["no_observed_group_cross_partition"] is True
