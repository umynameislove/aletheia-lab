"""Adversarial tests for prospective v3 dataset acquisition and binding."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

import aletheia_lab.benchmark.p2.confirmatory_v3_datasets as datasets_module
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    V3DatasetBinding,
    V3DatasetBindingError,
    V3DatasetBindingManifest,
    V3DatasetBindingReceipt,
    acquire_v3_dataset_archives,
    inspect_v3_dataset_archive,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
    verify_v3_dataset_binding_design,
    verify_v3_dataset_binding_receipt,
)
from aletheia_lab.data.download import ChecksumError

_MANIFEST_SHA256 = "67599382e1e114cf76e4f35d1e01c92477c8dd65f9e4e7eff1a94957bf3658fa"
_RECEIPT_SHA256 = "05b2703381f81f10979f916ef6eb657ed34c5728152e9c052d9dfa67fc66c684"


def _archive_bytes(member_name: str, content: bytes, *, extra_member: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(member_name, content)
        if extra_member:
            archive.writestr("unexpected.txt", b"unexpected")
    return output.getvalue()


def _synthetic_csv(
    tmp_path: Path,
    *,
    conflicting_duplicate: bool = False,
    blank_feature: bool = False,
    unexpected_target: bool = False,
    extra_member: bool = False,
) -> tuple[V3DatasetBinding, Path]:
    rows = ["feature,category,target"]
    for index in range(2000):
        feature: str | int = index
        category = "a" if index % 2 == 0 else "b"
        target = "false" if index < 1000 else "true"
        if conflicting_duplicate and index == 1000:
            feature = 0
            category = "a"
        if blank_feature and index == 0:
            category = ""
        if unexpected_target and index == 1999:
            target = "unknown"
        rows.append(f"{feature},{category},{target}")
    content = ("\n".join(rows) + "\n").encode()
    archive_content = _archive_bytes("snapshot.csv", content, extra_member=extra_member)
    archive_path = tmp_path / "synthetic.zip"
    archive_path.write_bytes(archive_content)
    binding = V3DatasetBinding.model_validate(
        {
            "dataset_id": "synthetic_dataset",
            "role": "primary",
            "uci_id": 999,
            "doi": "10.24432/TEST1",
            "source_page_uri": "https://archive.ics.uci.edu/dataset/999/synthetic",
            "license": "CC_BY_4_0",
            "archive": {
                "source_uri": "https://archive.ics.uci.edu/static/public/999/synthetic.zip",
                "file_name": "synthetic.zip",
                "sha256": hashlib.sha256(archive_content).hexdigest(),
                "byte_count": len(archive_content),
                "member_path": "snapshot.csv",
                "member_sha256": hashlib.sha256(content).hexdigest(),
                "member_byte_count": len(content),
                "member_count": 1,
            },
            "parser": {
                "format": "csv",
                "engine": "pandas_c",
                "sheet_name": None,
                "header_row": 0,
                "delimiter": ",",
                "keep_default_na": False,
            },
            "source_columns": ("feature", "category", "target"),
            "target": {
                "column": "target",
                "storage": "boolean",
                "positive_token": "true",
                "negative_token": "false",
                "positive_semantics": "positive_event",
            },
            "identifier_columns": (),
            "excluded_feature_columns": (),
            "post_outcome_exclusions": (),
            "categorical_features": ("category",),
            "numeric_features": ("feature",),
            "expected_row_count": 2000,
            "expected_class_counts": {"false": 1000, "true": 1000},
            "minimum_records_per_class": 1000,
            "missing_value_policy": "fail_if_any_missing_or_blank",
            "undocumented_category_policy": "retain_as_explicit_other_category",
            "duplicate_split_policy": ("keep_exact_analysis_feature_groups_within_one_partition"),
            "split_seed": 7,
            "split_fractions": (0.6, 0.2, 0.2),
        }
    )
    return binding, archive_path


def test_tracked_manifest_is_bound_to_d3c_and_freezes_leakage_policy() -> None:
    manifest = load_v3_dataset_binding_manifest()
    design = verify_v3_dataset_binding_design(manifest)

    assert manifest.canonical_sha256() == _MANIFEST_SHA256
    assert design.canonical_sha256() == manifest.design_sha256
    assert tuple(item.expected_row_count for item in manifest.datasets) == (30000, 12330)
    assert manifest.datasets[1].post_outcome_exclusions == ("PageValues",)
    assert manifest.datasets[1].analysis_features == (
        "Month",
        "OperatingSystems",
        "Browser",
        "Region",
        "TrafficType",
        "VisitorType",
        "Weekend",
        "Administrative",
        "Administrative_Duration",
        "Informational",
        "Informational_Duration",
        "ProductRelated",
        "ProductRelated_Duration",
        "BounceRates",
        "ExitRates",
        "SpecialDay",
    )
    assert manifest.governance.model_fitting_forbidden
    assert not manifest.governance.registration_authorized
    assert not manifest.governance.execution_authorized


def test_tracked_receipt_is_stable_and_contains_no_predictive_outcomes() -> None:
    receipt = load_v3_dataset_binding_receipt()

    assert receipt.canonical_sha256() == _RECEIPT_SHA256
    assert receipt.all_datasets_eligible
    assert not receipt.model_fitted
    assert not receipt.predictive_metrics_generated
    assert not receipt.sealed_outcomes_generated
    assert tuple(item.minimum_class_count for item in receipt.datasets) == (6636, 1908)
    assert tuple(item.duplicate_group_count for item in receipt.datasets) == (52, 76)
    assert tuple(item.conflicting_target_duplicate_group_count for item in receipt.datasets) == (
        21,
        0,
    )


def test_csv_archive_audit_reconciles_schema_counts_and_record_identity(tmp_path: Path) -> None:
    binding, archive = _synthetic_csv(tmp_path)

    audit = inspect_v3_dataset_archive(
        manifest_sha256="a" * 64,
        dataset=binding,
        archive_path=archive,
    )

    assert audit.row_count == 2000
    assert audit.class_counts == {"false": 1000, "true": 1000}
    assert audit.minimum_class_count == 1000
    assert audit.missing_or_blank_cell_count == 0
    assert audit.duplicate_group_count == 0
    assert audit.conflicting_target_duplicate_group_count == 0
    assert audit.identifier_is_unique
    assert audit.eligible
    assert len(audit.canonical_sha256()) == 64


def test_duplicate_groups_are_measured_and_cannot_be_hidden(tmp_path: Path) -> None:
    binding, archive = _synthetic_csv(tmp_path, conflicting_duplicate=True)

    audit = inspect_v3_dataset_archive(
        manifest_sha256="b" * 64,
        dataset=binding,
        archive_path=archive,
    )

    assert audit.duplicate_group_count == 1
    assert audit.rows_in_duplicate_groups == 2
    assert audit.conflicting_target_duplicate_group_count == 1
    assert binding.duplicate_split_policy == (
        "keep_exact_analysis_feature_groups_within_one_partition"
    )


@pytest.mark.parametrize(
    ("variant", "message"),
    [
        ("tamper", "archive byte count|archive SHA-256"),
        ("extra_member", "member census"),
        ("blank", "missing or blank"),
        ("target", "target encoding"),
    ],
)
def test_archive_schema_and_target_tampering_fail_closed(
    tmp_path: Path, variant: str, message: str
) -> None:
    binding, archive = _synthetic_csv(
        tmp_path,
        extra_member=variant == "extra_member",
        blank_feature=variant == "blank",
        unexpected_target=variant == "target",
    )
    if variant == "tamper":
        archive.write_bytes(archive.read_bytes() + b"tamper")

    with pytest.raises(V3DatasetBindingError, match=message):
        inspect_v3_dataset_archive(
            manifest_sha256="c" * 64,
            dataset=binding,
            archive_path=archive,
        )


def test_xls_parser_contract_uses_frozen_sheet_and_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding, _ = _synthetic_csv(tmp_path)
    fake_xls = b"frozen-xls-member"
    archive_content = _archive_bytes("snapshot.xls", fake_xls)
    archive = tmp_path / "synthetic-xls.zip"
    archive.write_bytes(archive_content)
    payload = binding.model_dump()
    payload["archive"] = {
        **binding.archive.model_dump(),
        "sha256": hashlib.sha256(archive_content).hexdigest(),
        "byte_count": len(archive_content),
        "member_path": "snapshot.xls",
        "member_sha256": hashlib.sha256(fake_xls).hexdigest(),
        "member_byte_count": len(fake_xls),
    }
    payload["parser"] = {
        "format": "xls",
        "engine": "xlrd",
        "sheet_name": "Data",
        "header_row": 1,
        "delimiter": None,
        "keep_default_na": True,
    }
    xls_binding = V3DatasetBinding.model_validate(payload)
    expected_frame = pd.DataFrame(
        {
            "feature": range(2000),
            "category": ["a" if index % 2 == 0 else "b" for index in range(2000)],
            "target": [False] * 1000 + [True] * 1000,
        }
    )

    def fake_read_excel(
        source: object, *, engine: str, sheet_name: str | None, header: int
    ) -> pd.DataFrame:
        assert source is not None
        assert engine == "xlrd"
        assert sheet_name == "Data"
        assert header == 1
        return expected_frame

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)
    audit = inspect_v3_dataset_archive(
        manifest_sha256="d" * 64,
        dataset=xls_binding,
        archive_path=archive,
    )

    assert audit.row_count == 2000
    assert audit.class_counts == {"false": 1000, "true": 1000}


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("parser", "engine", "xlrd"),
        ("target", "positive_token", "1"),
        ("binding", "split_fractions", [0.5, 0.25, 0.25]),
        ("binding", "post_outcome_exclusions", ["feature"]),
        ("archive", "member_path", "../snapshot.csv"),
    ],
)
def test_structural_binding_deviations_are_rejected(
    tmp_path: Path,
    section: str,
    field: str,
    replacement: object,
) -> None:
    binding, _ = _synthetic_csv(tmp_path)
    payload = binding.model_dump(mode="json")
    target = payload if section == "binding" else payload[section]
    assert isinstance(target, dict)
    target[field] = replacement

    with pytest.raises(ValidationError):
        V3DatasetBinding.model_validate_json(json.dumps(payload))


def test_acquisition_uses_only_manifest_url_hash_and_file_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_v3_dataset_binding_manifest()
    calls: list[tuple[str, str, Path]] = []

    def fake_download(*, url: str, sha256: str, destination: str | Path) -> Path:
        path = Path(destination)
        calls.append((url, sha256, path))
        return path

    monkeypatch.setattr(datasets_module, "download_pinned_file", fake_download)
    paths = acquire_v3_dataset_archives(manifest, destination=tmp_path)

    assert paths == tuple(tmp_path / item.archive.file_name for item in manifest.datasets)
    assert calls == [
        (item.archive.source_uri, item.archive.sha256, tmp_path / item.archive.file_name)
        for item in manifest.datasets
    ]


def test_acquisition_wraps_source_failures_as_binding_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_v3_dataset_binding_manifest()

    def failed_download(*, url: str, sha256: str, destination: str | Path) -> Path:
        raise ChecksumError(f"rejected {url} {sha256} {destination}")

    monkeypatch.setattr(datasets_module, "download_pinned_file", failed_download)
    with pytest.raises(V3DatasetBindingError, match="cannot acquire pinned archive"):
        acquire_v3_dataset_archives(manifest, destination=tmp_path)


@pytest.mark.parametrize(
    "variant",
    [
        "unofficial_source",
        "nested_archive_name",
        "invalid_xls_parser",
        "duplicate_source_column",
        "wrong_class_keys",
        "unreconciled_class_counts",
        "class_below_floor",
        "nonpositive_split",
    ],
)
def test_binding_semantic_invariants_fail_closed(tmp_path: Path, variant: str) -> None:
    binding, _ = _synthetic_csv(tmp_path)
    payload = binding.model_dump(mode="json")
    if variant == "unofficial_source":
        payload["archive"]["source_uri"] = "https://example.invalid/data.zip"
    elif variant == "nested_archive_name":
        payload["archive"]["file_name"] = "nested/data.zip"
    elif variant == "invalid_xls_parser":
        payload["parser"].update(
            {"format": "xls", "engine": "xlrd", "sheet_name": None, "delimiter": None}
        )
    elif variant == "duplicate_source_column":
        payload["source_columns"][1] = payload["source_columns"][0]
    elif variant == "wrong_class_keys":
        payload["expected_class_counts"] = {"false": 1000, "unknown": 1000}
    elif variant == "unreconciled_class_counts":
        payload["expected_class_counts"] = {"false": 1000, "true": 1001}
    elif variant == "class_below_floor":
        payload["expected_class_counts"] = {"false": 999, "true": 1001}
    else:
        payload["split_fractions"] = [0.6, 0.4, 0.0]

    with pytest.raises(ValidationError):
        V3DatasetBinding.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("variant", ["dataset_census", "split_seed"])
def test_manifest_census_and_seed_cannot_drift(variant: str) -> None:
    manifest = load_v3_dataset_binding_manifest()
    payload = manifest.model_dump(mode="json")
    if variant == "dataset_census":
        payload["datasets"].reverse()
    else:
        payload["datasets"][0]["split_seed"] = 99

    with pytest.raises(ValidationError):
        V3DatasetBindingManifest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("variant", ["role_census", "manifest_binding"])
def test_receipt_census_and_manifest_binding_cannot_drift(variant: str) -> None:
    receipt = load_v3_dataset_binding_receipt()
    payload = receipt.model_dump(mode="json")
    if variant == "role_census":
        payload["datasets"][1]["role"] = "primary"
    else:
        payload["datasets"][1]["manifest_sha256"] = "0" * 64

    with pytest.raises(ValidationError):
        V3DatasetBindingReceipt.model_validate_json(json.dumps(payload))


def test_receipt_and_design_binding_tampering_are_rejected() -> None:
    manifest = load_v3_dataset_binding_manifest()
    receipt = load_v3_dataset_binding_receipt()
    changed_audit = receipt.datasets[0].model_copy(update={"source_schema_sha256": "f" * 64})
    changed_receipt = receipt.model_copy(update={"datasets": (changed_audit, receipt.datasets[1])})

    with pytest.raises(V3DatasetBindingError, match="differs"):
        verify_v3_dataset_binding_receipt(changed_receipt, receipt)
    changed_manifest = manifest.model_copy(update={"design_sha256": "0" * 64})
    with pytest.raises(V3DatasetBindingError, match="another v3 design"):
        verify_v3_dataset_binding_design(changed_manifest)
    changed_dataset = manifest.datasets[0].model_copy(update={"doi": "10.24432/DIFFERENT"})
    changed_manifest = manifest.model_copy(
        update={"datasets": (changed_dataset, manifest.datasets[1])}
    )
    with pytest.raises(V3DatasetBindingError, match="differ from"):
        verify_v3_dataset_binding_design(changed_manifest)


def test_design_verification_fails_closed_when_design_is_unavailable(tmp_path: Path) -> None:
    manifest = load_v3_dataset_binding_manifest()
    with pytest.raises(V3DatasetBindingError, match="cannot validate"):
        verify_v3_dataset_binding_design(manifest, root=tmp_path)


def test_loaders_fail_closed_on_missing_or_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(V3DatasetBindingError, match="manifest"):
        load_v3_dataset_binding_manifest(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
    with pytest.raises(V3DatasetBindingError, match="receipt"):
        load_v3_dataset_binding_receipt(invalid)
