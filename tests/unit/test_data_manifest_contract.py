"""Adversarial tests for dataset and benchmark manifest trust boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from aletheia_lab.benchmark.p2.contracts import FamilyCensus, FamilyCensusEntry
from aletheia_lab.data.download import sha256_file
from aletheia_lab.data.manifest import (
    BENCHMARK_FAMILY_SPLIT_SCHEMA_VERSION,
    DATASET_SNAPSHOT_SCHEMA_VERSION,
    MODEL_DATA_SPLIT_SCHEMA_VERSION,
    BenchmarkFamilySplitManifest,
    DatasetColumn,
    DatasetSnapshotManifest,
    FamilySplitAssignment,
    FamilySplitCounts,
    ManifestContractError,
    ModelDataSplitManifest,
    RecordSplit,
    SplitRatios,
    family_census_sha256,
    load_manifest,
    manifest_artifact_bytes,
    manifest_identity_sha256,
    record_inventory,
    record_membership_sha256,
    validate_benchmark_family_split,
    validate_dataset_snapshot_source,
    validate_model_data_split,
    write_manifest,
)
from aletheia_lab.evidence.schema import canonical_json, sha256_text

_HEX_0 = "0" * 64
_HEX_A = "a" * 64
_HEX_B = "b" * 64


def _ids(count: int = 20) -> tuple[str, ...]:
    return tuple(f"record-{index:04d}" for index in range(count))


def _columns() -> tuple[DatasetColumn, ...]:
    return (
        DatasetColumn(name="customerID", logical_type="string", role="identifier", nullable=False),
        DatasetColumn(
            name="MonthlyCharges", logical_type="float", role="numeric_feature", nullable=False
        ),
        DatasetColumn(name="Churn", logical_type="category", role="target", nullable=False),
    )


def _source_file(tmp_path: Path, content: bytes = b"synthetic,not,customer,data\n") -> Path:
    path = tmp_path / "processed.csv"
    path.write_bytes(content)
    return path


def _dataset(
    source_file: Path,
    *,
    record_ids: tuple[str, ...] | None = None,
    relative_path: str = "data/processed/telco.csv",
    **overrides: object,
) -> DatasetSnapshotManifest:
    ids = record_ids or _ids()
    inventory = record_inventory(ids)
    payload: dict[str, object] = {
        "schema_version": DATASET_SNAPSHOT_SCHEMA_VERSION,
        "dataset_id": "telco_customer_churn",
        "source_uri": (
            "https://raw.githubusercontent.com/IBM/"
            "telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv"
        ),
        "source_version": "upstream-master@16320c9c",
        "preprocessing_version": "telco-clean/v1",
        "normalized_relative_path": relative_path,
        "dataset_sha256": sha256_file(source_file),
        "size_bytes": source_file.stat().st_size,
        "n_rows": len(ids),
        "n_cols": len(_columns()),
        "columns": _columns(),
        "id_column": "customerID",
        "target_column": "Churn",
        "record_id_hashes": inventory,
        "record_membership_sha256": record_membership_sha256(inventory),
    }
    payload.update(overrides)
    return DatasetSnapshotManifest(**payload)  # type: ignore[arg-type]


def _record_split(name: str, ids: tuple[str, ...], positives: int) -> RecordSplit:
    inventory = record_inventory(ids)
    return RecordSplit(
        name=name,  # type: ignore[arg-type]
        record_id_hashes=inventory,
        membership_sha256=record_membership_sha256(inventory),
        n_records=len(ids),
        n_positive=positives,
        n_negative=len(ids) - positives,
        positive_rate=positives / len(ids),
    )


def _split(dataset: DatasetSnapshotManifest, **overrides: object) -> ModelDataSplitManifest:
    ids = _ids()
    payload: dict[str, object] = {
        "schema_version": MODEL_DATA_SPLIT_SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "dataset_sha256": dataset.dataset_sha256,
        "dataset_identity_sha256": dataset.identity_sha256(),
        "n_rows": len(ids),
        "seed": 42,
        "stratified": True,
        "ratios": SplitRatios(),
        "id_column": "customerID",
        "target_column": "Churn",
        "train": _record_split("train", ids[:14], 5),
        "validation": _record_split("validation", ids[14:17], 1),
        "test": _record_split("test", ids[17:], 1),
    }
    payload.update(overrides)
    return ModelDataSplitManifest(**payload)  # type: ignore[arg-type]


def _census() -> FamilyCensus:
    entries: list[FamilyCensusEntry] = []
    for index, digit in enumerate("abcd", start=1):
        fingerprint = digit * 64
        entries.append(
            FamilyCensusEntry(
                case_family_id=f"p2-family-{fingerprint}",
                candidate_id=f"p2-candidate-{index:064x}",
                fault_type="data_drift",
                family_class="stable_control",
                proposed_family_sha256=fingerprint,
            )
        )
    return FamilyCensus(schema_version="p2-family-census/1", entries=tuple(entries))


def _family_split(census: FamilyCensus, **overrides: object) -> BenchmarkFamilySplitManifest:
    names = ("dev", "main", "human_audit", "organic_validity")
    assignments = tuple(
        FamilySplitAssignment(case_family_id=entry.case_family_id, split=names[index])  # type: ignore[arg-type]
        for index, entry in enumerate(census.entries)
    )
    assignments = tuple(sorted(assignments, key=lambda entry: entry.case_family_id))
    payload: dict[str, object] = {
        "schema_version": BENCHMARK_FAMILY_SPLIT_SCHEMA_VERSION,
        "family_census_sha256": family_census_sha256(census),
        "n_families": len(assignments),
        "assignments": assignments,
        "split_counts": FamilySplitCounts(dev=1, main=1, human_audit=1, organic_validity=1),
    }
    payload.update(overrides)
    return BenchmarkFamilySplitManifest(**payload)  # type: ignore[arg-type]


def _forge(model: Any, **updates: object) -> Any:
    return model.model_copy(update=updates)


# --------------------------------------------------------------------------- #
# Dataset snapshot
# --------------------------------------------------------------------------- #


def test_dataset_snapshot_round_trips_and_validates_its_real_source(tmp_path: Path) -> None:
    source = _source_file(tmp_path)
    manifest = _dataset(source)
    returned = validate_dataset_snapshot_source(
        manifest,
        source_file=source,
        record_ids=_ids(),
        columns=_columns(),
    )
    assert returned == manifest
    assert DatasetSnapshotManifest.model_validate(manifest.model_dump()) == manifest


def test_dataset_identity_excludes_only_the_portable_location(tmp_path: Path) -> None:
    source = _source_file(tmp_path)
    first = _dataset(source, relative_path="data/processed/one.csv")
    moved = _dataset(source, relative_path="fixtures/telco.csv")
    changed_source_version = _dataset(source, source_version="release-2")
    assert first.identity_sha256() == moved.identity_sha256()
    assert first.identity_sha256() != changed_source_version.identity_sha256()
    assert manifest_identity_sha256(first) == first.identity_sha256()


def test_record_inventory_is_order_independent_and_rejects_duplicate_raw_ids() -> None:
    assert record_inventory(("record-b", "record-a")) == record_inventory(("record-a", "record-b"))
    with pytest.raises(ValueError, match="duplicate ID"):
        record_inventory(("record-a", "record-a"))


def test_record_hash_and_membership_match_an_independent_canonical_payload() -> None:
    first = sha256_text(
        canonical_json({"schema_version": "p2-record-id-hash/v1", "record_id": "record-a"})
    )
    second = sha256_text(
        canonical_json({"schema_version": "p2-record-id-hash/v1", "record_id": "record-b"})
    )
    expected_inventory = tuple(sorted((first, second)))
    expected_membership = sha256_text(
        canonical_json(
            {
                "schema_version": "p2-record-membership/v1",
                "record_id_hashes": list(expected_inventory),
            }
        )
    )
    assert record_inventory(("record-b", "record-a")) == expected_inventory
    assert record_membership_sha256(expected_inventory) == expected_membership


def test_record_hash_apis_reject_a_bare_string_instead_of_splitting_characters() -> None:
    with pytest.raises(TypeError, match="complete record IDs"):
        record_inventory("record-a")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="sequence of SHA-256"):
        record_membership_sha256(_HEX_A)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "inventory",
    [
        (),
        (_HEX_B, _HEX_A),
        (_HEX_A, _HEX_A),
        ("not-a-sha256",),
    ],
)
def test_record_membership_hash_rejects_noncanonical_inventory(
    inventory: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        record_membership_sha256(inventory)


@pytest.mark.parametrize(
    "path",
    [
        "/private/data.csv",
        "../data.csv",
        "data/../data.csv",
        "data/./data.csv",
        "C:/data/file.csv",
        r"C:\data\file.csv",
        r"\\server\share\file.csv",
        "data\\file.csv",
        ".",
        "",
        " data/file.csv",
    ],
)
def test_dataset_snapshot_rejects_non_portable_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValidationError):
        _dataset(_source_file(tmp_path), relative_path=path)


@pytest.mark.parametrize(
    "uri",
    [
        "file:///private/data.csv",
        "/private/data.csv",
        "http://example.com/data.csv",
        "https://user:secret@example.com/data.csv",
        "https://example.com/data.csv?token=secret",
        "https://example.com/data.csv#fragment",
    ],
)
def test_dataset_snapshot_rejects_local_or_sensitive_source_uris(tmp_path: Path, uri: str) -> None:
    with pytest.raises(ValidationError):
        _dataset(_source_file(tmp_path), source_uri=uri)


def test_dataset_snapshot_rejects_unknown_nested_fields(tmp_path: Path) -> None:
    manifest = _dataset(_source_file(tmp_path))
    payload = manifest.model_dump()
    payload["columns"][0]["ground_truth"] = "leak"  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs"):
        DatasetSnapshotManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "p2-dataset-snapshot/v2"),
        ("dataset_sha256", "A" * 64),
        ("dataset_sha256", "a" * 63),
        ("dataset_id", " telco"),
    ],
)
def test_dataset_snapshot_rejects_invalid_pins(tmp_path: Path, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _dataset(_source_file(tmp_path), **{field: value})


def test_dataset_snapshot_rejects_duplicate_columns_and_role_mismatch(tmp_path: Path) -> None:
    source = _source_file(tmp_path)
    columns = _columns()
    with pytest.raises(ValidationError, match="unique"):
        _dataset(source, columns=(*columns[:-1], columns[0]))
    wrong_role = (
        columns[0],
        columns[1],
        DatasetColumn(
            name="Churn", logical_type="category", role="categorical_feature", nullable=False
        ),
    )
    with pytest.raises(ValidationError, match="target role"):
        _dataset(source, columns=wrong_role)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"n_rows": 21}, "n_rows"),
        ({"n_cols": 4}, "n_cols"),
        ({"record_membership_sha256": _HEX_0}, "record_membership_sha256"),
        ({"target_column": "customerID"}, "must be different"),
    ],
)
def test_dataset_snapshot_rejects_self_inconsistent_fields(
    tmp_path: Path, updates: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _dataset(_source_file(tmp_path), **updates)


@pytest.mark.parametrize(
    "source_change",
    ["bytes", "record_ids", "columns"],
)
def test_source_validation_recomputes_instead_of_trusting_the_manifest(
    tmp_path: Path, source_change: str
) -> None:
    source = _source_file(tmp_path)
    manifest = _dataset(source)
    record_ids = _ids()
    columns = _columns()
    if source_change == "bytes":
        source.write_bytes(b"changed source bytes\n")
    elif source_change == "record_ids":
        record_ids = (*record_ids[:-1], "different-record")
    else:
        columns = (
            *columns[:-1],
            DatasetColumn(name="Other", logical_type="category", role="target", nullable=False),
        )
    with pytest.raises(ManifestContractError):
        validate_dataset_snapshot_source(
            manifest, source_file=source, record_ids=record_ids, columns=columns
        )


def test_snapshot_source_validation_rejects_unsafe_model_copy(tmp_path: Path) -> None:
    source = _source_file(tmp_path)
    forged = _forge(_dataset(source), n_rows=999)
    with pytest.raises(ValidationError):
        validate_dataset_snapshot_source(
            forged, source_file=source, record_ids=_ids(), columns=_columns()
        )


def test_snapshot_source_validation_rejects_a_symlinked_source(
    tmp_path: Path, make_symlink: Any
) -> None:
    source = _source_file(tmp_path)
    manifest = _dataset(source)
    link = tmp_path / "processed-link.csv"
    make_symlink(link, source)
    with pytest.raises(ManifestContractError, match="not a symlink"):
        validate_dataset_snapshot_source(
            manifest,
            source_file=link,
            record_ids=_ids(),
            columns=_columns(),
        )


# --------------------------------------------------------------------------- #
# Model-data split
# --------------------------------------------------------------------------- #


def test_model_data_split_is_exactly_bound_to_dataset(tmp_path: Path) -> None:
    dataset = _dataset(_source_file(tmp_path))
    split = _split(dataset)
    assert validate_model_data_split(dataset, split) == split
    assert manifest_identity_sha256(split) == split.identity_sha256()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", 41),
        ("stratified", False),
        ("ratios", SplitRatios.model_construct(train=0.8, validation=0.1, test=0.1)),
    ],
)
def test_model_data_policy_cannot_be_changed(tmp_path: Path, field: str, value: object) -> None:
    dataset = _dataset(_source_file(tmp_path))
    with pytest.raises(ValidationError):
        _split(dataset, **{field: value})


def test_model_data_split_rejects_unknown_nested_field_and_schema_drift(tmp_path: Path) -> None:
    dataset = _dataset(_source_file(tmp_path))
    payload = _split(dataset).model_dump()
    payload["train"]["expected_behavior"] = "pass"  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs"):
        ModelDataSplitManifest.model_validate(payload)
    payload = _split(dataset).model_dump()
    payload["schema_version"] = "p2-model-data-split/v2"
    with pytest.raises(ValidationError):
        ModelDataSplitManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("membership_sha256", _HEX_0, "membership_sha256"),
        ("n_records", 4, "n_records"),
        ("n_positive", 2, "add up"),
        ("positive_rate", 0.5, "derived"),
    ],
)
def test_record_split_rejects_forged_accounting(field: str, value: object, message: str) -> None:
    split = _record_split("test", _ids(3), 1)
    with pytest.raises(ValidationError, match=message):
        RecordSplit.model_validate({**split.model_dump(), field: value})


@pytest.mark.parametrize(("n_positive", "n_negative"), [(0, 3), (3, 0)])
def test_stratified_split_requires_both_labels(n_positive: int, n_negative: int) -> None:
    inventory = record_inventory(_ids(3))
    with pytest.raises(ValidationError):
        RecordSplit(
            name="test",
            record_id_hashes=inventory,
            membership_sha256=record_membership_sha256(inventory),
            n_records=3,
            n_positive=n_positive,
            n_negative=n_negative,
            positive_rate=n_positive / 3,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_split_positive_rate_must_be_finite(value: float) -> None:
    ids = _ids(3)
    inventory = record_inventory(ids)
    with pytest.raises(ValidationError, match="finite"):
        RecordSplit(
            name="test",
            record_id_hashes=inventory,
            membership_sha256=record_membership_sha256(inventory),
            n_records=3,
            n_positive=1,
            n_negative=2,
            positive_rate=value,
        )


def test_split_rejects_overlap_even_when_counts_still_add_up(tmp_path: Path) -> None:
    dataset = _dataset(_source_file(tmp_path))
    ids = _ids()
    overlapping = _record_split("validation", (ids[13], ids[15], ids[16]), 1)
    with pytest.raises(ValidationError, match="disjoint"):
        _split(dataset, validation=overlapping)


@pytest.mark.parametrize(
    "field",
    ["dataset_id", "dataset_sha256", "dataset_identity_sha256", "n_rows"],
)
def test_cross_validation_rejects_dataset_replay(tmp_path: Path, field: str) -> None:
    dataset = _dataset(_source_file(tmp_path))
    split = _split(dataset)
    replacement: object = 21 if field == "n_rows" else (_HEX_0 if "sha256" in field else "other")
    forged = _forge(split, **{field: replacement})
    with pytest.raises((ValidationError, ManifestContractError)):
        validate_model_data_split(dataset, forged)


def test_cross_validation_rejects_missing_and_extra_record_hashes(tmp_path: Path) -> None:
    dataset = _dataset(_source_file(tmp_path))
    other_ids = (*_ids()[:-1], "foreign-record")
    replayed = ModelDataSplitManifest(
        **{
            **_split(dataset).model_dump(exclude={"test"}),
            "test": _record_split("test", other_ids[17:], 1),
        }  # type: ignore[arg-type]
    )
    with pytest.raises(ManifestContractError, match="missing=1; extra=1"):
        validate_model_data_split(dataset, replayed)


# --------------------------------------------------------------------------- #
# Benchmark-family split
# --------------------------------------------------------------------------- #


def test_family_split_is_bound_to_the_complete_typed_census() -> None:
    census = _census()
    split = _family_split(census)
    assert validate_benchmark_family_split(census, split) == split
    assert manifest_identity_sha256(split) == split.identity_sha256()


def test_family_split_rejects_forged_census_hash() -> None:
    census = _census()
    forged = _forge(_family_split(census), family_census_sha256=_HEX_0)
    with pytest.raises(ManifestContractError, match="not bound"):
        validate_benchmark_family_split(census, forged)


def test_family_split_rejects_missing_and_foreign_family() -> None:
    census = _census()
    split = _family_split(census)
    assignments = list(split.assignments)
    assignments[-1] = FamilySplitAssignment(
        case_family_id=f"p2-family-{'e' * 64}", split=assignments[-1].split
    )
    assignments.sort(key=lambda entry: entry.case_family_id)
    replayed = BenchmarkFamilySplitManifest(
        **{
            **split.model_dump(exclude={"assignments"}),
            "assignments": tuple(assignments),
        }  # type: ignore[arg-type]
    )
    with pytest.raises(ManifestContractError, match="missing=1; extra=1"):
        validate_benchmark_family_split(census, replayed)


def test_family_split_rejects_unsorted_duplicate_and_false_counts() -> None:
    census = _census()
    split = _family_split(census)
    with pytest.raises(ValidationError, match="sorted"):
        _family_split(census, assignments=tuple(reversed(split.assignments)))
    duplicated = (*split.assignments[:-1], split.assignments[0])
    duplicated = tuple(sorted(duplicated, key=lambda entry: entry.case_family_id))
    with pytest.raises(ValidationError, match="exactly once"):
        _family_split(census, assignments=duplicated)
    with pytest.raises(ValidationError, match="recomputed"):
        _family_split(
            census,
            split_counts=FamilySplitCounts(dev=4, main=0, human_audit=0, organic_validity=0),
        )


def test_family_split_rejects_unknown_nested_field_schema_drift_and_bad_ids() -> None:
    census = _census()
    payload = _family_split(census).model_dump()
    payload["assignments"][0]["expected_behavior"] = "accept"  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs"):
        BenchmarkFamilySplitManifest.model_validate(payload)
    payload = _family_split(census).model_dump()
    payload["schema_version"] = "p2-benchmark-family-split/v2"
    with pytest.raises(ValidationError):
        BenchmarkFamilySplitManifest.model_validate(payload)
    with pytest.raises(ValidationError):
        FamilySplitAssignment(case_family_id=" blank", split="dev")
    with pytest.raises(ValidationError):
        FamilySplitAssignment(case_family_id="x" * 129, split="dev")


# --------------------------------------------------------------------------- #
# Immutable persistence and publication safety
# --------------------------------------------------------------------------- #


def test_persistence_is_canonical_idempotent_and_reloadable(tmp_path: Path) -> None:
    manifest = _dataset(_source_file(tmp_path))
    root = tmp_path / "store"
    path = write_manifest(manifest, output_root=root, relative_path="dataset.json")
    assert path.read_bytes() == manifest_artifact_bytes(manifest)
    assert path.read_bytes().endswith(b"\n")
    assert b"\r\n" not in path.read_bytes()
    assert write_manifest(manifest, output_root=root, relative_path="dataset.json") == path
    loaded = load_manifest(
        output_root=root,
        relative_path="dataset.json",
        model_type=DatasetSnapshotManifest,
    )
    assert loaded == manifest


def test_artifact_bytes_match_an_independent_serialization_contract(tmp_path: Path) -> None:
    manifest = _dataset(_source_file(tmp_path))
    expected = (
        json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    assert manifest_artifact_bytes(manifest) == expected


def test_artifact_does_not_persist_raw_record_ids_or_local_paths(tmp_path: Path) -> None:
    manifest = _dataset(_source_file(tmp_path))
    payload = manifest_artifact_bytes(manifest)
    assert all(record_id.encode() not in payload for record_id in _ids())
    assert str(tmp_path).encode() not in payload


def test_writer_refuses_non_identical_overwrite(tmp_path: Path) -> None:
    source = _source_file(tmp_path)
    first = _dataset(source)
    changed = _dataset(source, preprocessing_version="telco-clean/v2")
    root = tmp_path / "store"
    write_manifest(first, output_root=root, relative_path="dataset.json")
    with pytest.raises(FileExistsError, match="non-identical"):
        write_manifest(changed, output_root=root, relative_path="dataset.json")


@pytest.mark.parametrize(
    "relative_path",
    ["../escape.json", "/absolute.json", "C:/escape.json", r"\\server\share\m.json"],
)
def test_writer_rejects_output_escape(tmp_path: Path, relative_path: str) -> None:
    manifest = _dataset(_source_file(tmp_path))
    with pytest.raises(ValueError):
        write_manifest(manifest, output_root=tmp_path / "root", relative_path=relative_path)


def test_writer_rejects_symlink_escape(tmp_path: Path, make_symlink: Any) -> None:
    manifest = _dataset(_source_file(tmp_path))
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    make_symlink(root / "escape", outside)
    with pytest.raises(ValueError, match="symlink"):
        write_manifest(manifest, output_root=root, relative_path="escape/manifest.json")


def test_loader_is_read_only_when_store_is_missing(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-store"
    with pytest.raises(FileNotFoundError, match="root does not exist"):
        load_manifest(
            output_root=missing_root,
            relative_path="dataset.json",
            model_type=DatasetSnapshotManifest,
        )
    assert not missing_root.exists()


def test_loader_rejects_symlinked_manifest(tmp_path: Path, make_symlink: Any) -> None:
    manifest = _dataset(_source_file(tmp_path))
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(manifest_artifact_bytes(manifest))
    make_symlink(root / "dataset.json", outside)
    with pytest.raises(ValueError, match="symlink"):
        load_manifest(
            output_root=root,
            relative_path="dataset.json",
            model_type=DatasetSnapshotManifest,
        )


def test_writer_cleans_stage_file_after_injected_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _dataset(_source_file(tmp_path))
    root = tmp_path / "store"

    def fail_link(*args: object, **kwargs: object) -> None:
        raise OSError("injected link failure")

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(OSError, match="injected"):
        write_manifest(manifest, output_root=root, relative_path="dataset.json")
    assert not (root / "dataset.json").exists()
    assert list(root.glob(".*.stage")) == []


def test_loader_rejects_noncanonical_and_tampered_bytes(tmp_path: Path) -> None:
    manifest = _dataset(_source_file(tmp_path))
    root = tmp_path / "store"
    root.mkdir()
    path = root / "dataset.json"
    pretty = manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"
    path.write_bytes(pretty)
    with pytest.raises(ValueError, match="not canonically encoded"):
        load_manifest(
            output_root=root,
            relative_path="dataset.json",
            model_type=DatasetSnapshotManifest,
        )
    path.write_bytes(b'{"schema_version":"p2-dataset-snapshot/v1","passed":true}\n')
    with pytest.raises(ValidationError):
        load_manifest(
            output_root=root,
            relative_path="dataset.json",
            model_type=DatasetSnapshotManifest,
        )


def test_writer_revalidates_objects_built_with_model_construct(tmp_path: Path) -> None:
    source = _source_file(tmp_path)
    manifest = _dataset(source)
    forged = DatasetSnapshotManifest.model_construct(
        **{**manifest.model_dump(), "record_membership_sha256": _HEX_0}
    )
    with pytest.raises(ValidationError, match="record_membership_sha256"):
        write_manifest(forged, output_root=tmp_path / "store", relative_path="dataset.json")


def test_writer_rejects_an_unregistered_basemodel(tmp_path: Path) -> None:
    class UnregisteredArtifact(BaseModel):
        passed: bool

    with pytest.raises(TypeError, match="registered manifest contracts"):
        write_manifest(  # type: ignore[arg-type]
            UnregisteredArtifact(passed=True),
            output_root=tmp_path / "store",
            relative_path="artifact.json",
        )


def test_contract_models_cannot_own_pass_or_eligibility_fields() -> None:
    models = (
        DatasetSnapshotManifest,
        ModelDataSplitManifest,
        BenchmarkFamilySplitManifest,
    )
    for model in models:
        names = set(model.model_fields)
        assert not any(token in name for name in names for token in ("passed", "eligib", "verdict"))
