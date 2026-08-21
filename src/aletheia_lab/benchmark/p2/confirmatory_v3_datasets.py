"""Outcome-free acquisition and binding for the prospective v3 datasets.

This module may inspect source bytes, schema, target encoding, class counts and
duplicate groups.  It cannot split data, fit a model, calculate a predictive
metric or authorize registration/execution.
"""

from __future__ import annotations

import hashlib
import io
import math
import zipfile
from numbers import Integral, Real
from pathlib import Path, PurePosixPath
from typing import Final, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_design import (
    V3StudyDesign,
    load_v3_study_design,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.data.download import ChecksumError, download_pinned_file, sha256_file

D4A_DATASET_BINDING_SCHEMA_VERSION: Final[Literal["p2-v3-dataset-binding/1"]] = (
    "p2-v3-dataset-binding/1"
)
D4A_DATASET_AUDIT_SCHEMA_VERSION: Final[Literal["p2-v3-dataset-audit/1"]] = "p2-v3-dataset-audit/1"
DEFAULT_V3_DATASET_BINDINGS_PATH: Final[Path] = Path(
    "configs/benchmark/p2_label_noise_shift_v3_dataset_bindings.json"
)
DEFAULT_V3_DATASET_RECEIPT_PATH: Final[Path] = Path(
    "configs/benchmark/provenance/p2_v3_dataset_binding_receipt.json"
)

DatasetRole = Literal["primary", "external_replication"]


class V3DatasetBindingError(ValueError):
    """Raised when acquisition or outcome-free dataset binding fails."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArchiveBinding(_StrictFrozenModel):
    source_uri: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    byte_count: int = Field(gt=0)
    member_path: str = Field(min_length=1)
    member_sha256: str = Field(pattern=SHA256_PATTERN)
    member_byte_count: int = Field(gt=0)
    member_count: Literal[1]

    @model_validator(mode="after")
    def _source_and_paths_are_safe(self) -> ArchiveBinding:
        if not self.source_uri.startswith("https://archive.ics.uci.edu/"):
            raise ValueError("v3 archives must use the official UCI HTTPS origin")
        if Path(self.file_name).name != self.file_name or not self.file_name.endswith(".zip"):
            raise ValueError("archive file name must be a plain zip file name")
        member = PurePosixPath(self.member_path)
        if member.is_absolute() or any(part in {"", ".", ".."} for part in member.parts):
            raise ValueError("archive member path is unsafe")
        return self


class ParserBinding(_StrictFrozenModel):
    format: Literal["csv", "xls"]
    engine: Literal["pandas_c", "xlrd"]
    sheet_name: str | None
    header_row: int = Field(ge=0)
    delimiter: Literal[","] | None
    keep_default_na: bool

    @model_validator(mode="after")
    def _parser_matches_format(self) -> ParserBinding:
        if self.format == "csv" and (
            self.engine != "pandas_c" or self.sheet_name is not None or self.delimiter != ","
        ):
            raise ValueError("CSV parsing must use the frozen pandas C-engine contract")
        if self.format == "xls" and (
            self.engine != "xlrd" or self.sheet_name is None or self.delimiter is not None
        ):
            raise ValueError("XLS parsing must use a named sheet through xlrd")
        return self


class TargetBinding(_StrictFrozenModel):
    column: str = Field(min_length=1)
    storage: Literal["integer_binary", "boolean"]
    positive_token: Literal["1", "true"]
    negative_token: Literal["0", "false"]
    positive_semantics: str = Field(min_length=1)

    @model_validator(mode="after")
    def _encoding_is_unambiguous(self) -> TargetBinding:
        expected = {
            "integer_binary": ("1", "0"),
            "boolean": ("true", "false"),
        }[self.storage]
        if (self.positive_token, self.negative_token) != expected:
            raise ValueError("target tokens do not match their frozen storage type")
        return self


class V3DatasetBinding(_StrictFrozenModel):
    dataset_id: str = Field(min_length=1)
    role: DatasetRole
    uci_id: int = Field(gt=0)
    doi: str = Field(pattern=r"^10\.24432/[A-Z0-9]+$")
    source_page_uri: str = Field(min_length=1)
    license: Literal["CC_BY_4_0"]
    archive: ArchiveBinding
    parser: ParserBinding
    source_columns: tuple[str, ...]
    target: TargetBinding
    identifier_columns: tuple[str, ...]
    excluded_feature_columns: tuple[str, ...]
    post_outcome_exclusions: tuple[str, ...]
    categorical_features: tuple[str, ...]
    numeric_features: tuple[str, ...]
    expected_row_count: int = Field(gt=0)
    expected_class_counts: dict[str, int]
    minimum_records_per_class: Literal[1000]
    missing_value_policy: Literal["fail_if_any_missing_or_blank"]
    undocumented_category_policy: Literal["retain_as_explicit_other_category"]
    duplicate_split_policy: Literal["keep_exact_analysis_feature_groups_within_one_partition"]
    split_seed: int = Field(ge=0)
    split_fractions: tuple[float, float, float]

    @model_validator(mode="after")
    def _schema_is_complete(self) -> V3DatasetBinding:
        columns = self.source_columns
        if not columns or len(set(columns)) != len(columns):
            raise ValueError("source columns must be non-empty and unique")
        components = (
            self.identifier_columns,
            self.excluded_feature_columns,
            self.categorical_features,
            self.numeric_features,
            (self.target.column,),
        )
        flattened = tuple(value for component in components for value in component)
        if len(flattened) != len(set(flattened)) or set(flattened) != set(columns):
            raise ValueError("every source column must have exactly one frozen role")
        if set(self.post_outcome_exclusions) - set(self.excluded_feature_columns):
            raise ValueError("post-outcome columns must be excluded from the analysis frame")
        if set(self.expected_class_counts) != {
            self.target.negative_token,
            self.target.positive_token,
        }:
            raise ValueError("expected class-count keys must match the target encoding")
        if sum(self.expected_class_counts.values()) != self.expected_row_count:
            raise ValueError("expected class counts must reconcile with row count")
        if min(self.expected_class_counts.values()) < self.minimum_records_per_class:
            raise ValueError("a bound dataset is below the prospective class-count floor")
        if any(not math.isfinite(value) or value <= 0.0 for value in self.split_fractions):
            raise ValueError("split fractions must be finite and positive")
        if not math.isclose(
            math.fsum(self.split_fractions), 1.0, rel_tol=0.0, abs_tol=1e-12
        ) or self.split_fractions != (0.6, 0.2, 0.2):
            raise ValueError("the binding must preserve the frozen 60/20/20 split")
        return self

    @property
    def analysis_features(self) -> tuple[str, ...]:
        return self.categorical_features + self.numeric_features


class V3DatasetGovernance(_StrictFrozenModel):
    archive_bytes_excluded_from_git: Literal[True]
    download_requires_pinned_sha256: Literal[True]
    target_inspection_limited_to_encoding_and_class_eligibility: Literal[True]
    feature_duplicate_groups_must_not_cross_partitions: Literal[True]
    model_fitting_forbidden: Literal[True]
    sealed_outcome_generation_forbidden: Literal[True]
    registration_authorized: Literal[False]
    execution_authorized: Literal[False]


class V3DatasetBindingManifest(_StrictFrozenModel):
    schema_version: Literal["p2-v3-dataset-binding/1"]
    status: Literal["bound_outcome_free_not_registered"]
    design_uri: Literal["configs/benchmark/p2_label_noise_shift_v3_design.json"]
    design_sha256: str = Field(pattern=SHA256_PATTERN)
    datasets: tuple[V3DatasetBinding, ...]
    governance: V3DatasetGovernance

    @model_validator(mode="after")
    def _dataset_census_is_fixed(self) -> V3DatasetBindingManifest:
        identities = tuple((item.dataset_id, item.role, item.uci_id) for item in self.datasets)
        if identities != (
            ("uci_default_of_credit_card_clients", "primary", 350),
            ("uci_online_shoppers_purchasing_intention", "external_replication", 468),
        ):
            raise ValueError("the complete ordered v3 dataset census is required")
        if self.datasets[0].split_seed != 2718 or self.datasets[1].split_seed != 3141:
            raise ValueError("dataset split seeds must match the outcome-free v3 design")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class DatasetBindingAudit(_StrictFrozenModel):
    schema_version: Literal["p2-v3-dataset-audit/1"] = D4A_DATASET_AUDIT_SCHEMA_VERSION
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_id: str
    role: DatasetRole
    archive_sha256: str = Field(pattern=SHA256_PATTERN)
    member_sha256: str = Field(pattern=SHA256_PATTERN)
    archive_byte_count: int = Field(gt=0)
    member_byte_count: int = Field(gt=0)
    row_count: int = Field(gt=0)
    column_count: int = Field(gt=0)
    source_columns: tuple[str, ...]
    analysis_feature_columns: tuple[str, ...]
    excluded_feature_columns: tuple[str, ...]
    target_column: str
    positive_token: str
    negative_token: str
    class_counts: dict[str, int]
    minimum_class_count: int = Field(gt=0)
    missing_or_blank_cell_count: int = Field(ge=0)
    identifier_is_unique: bool
    duplicate_group_count: int = Field(ge=0)
    rows_in_duplicate_groups: int = Field(ge=0)
    conflicting_target_duplicate_group_count: int = Field(ge=0)
    source_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    record_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    target_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    eligible: Literal[True]

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class V3DatasetBindingReceipt(_StrictFrozenModel):
    schema_version: Literal["p2-v3-dataset-audit/1"] = D4A_DATASET_AUDIT_SCHEMA_VERSION
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    datasets: tuple[DatasetBindingAudit, ...]
    all_datasets_eligible: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]
    sealed_outcomes_generated: Literal[False]
    registration_authorized: Literal[False]
    execution_authorized: Literal[False]

    @model_validator(mode="after")
    def _receipt_reconciles(self) -> V3DatasetBindingReceipt:
        if tuple(item.role for item in self.datasets) != ("primary", "external_replication"):
            raise ValueError("receipt must contain the complete ordered dataset census")
        if any(item.manifest_sha256 != self.manifest_sha256 for item in self.datasets):
            raise ValueError("dataset audit is bound to another manifest")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_v3_dataset_binding_manifest(
    path: str | Path = DEFAULT_V3_DATASET_BINDINGS_PATH,
) -> V3DatasetBindingManifest:
    """Load the strict outcome-free dataset binding manifest."""

    try:
        payload = Path(path).read_text(encoding="utf-8")
        return V3DatasetBindingManifest.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise V3DatasetBindingError(
            "v3 dataset binding manifest is unavailable or invalid"
        ) from exc


def load_v3_dataset_binding_receipt(
    path: str | Path = DEFAULT_V3_DATASET_RECEIPT_PATH,
) -> V3DatasetBindingReceipt:
    """Load the expected deterministic audit receipt."""

    try:
        payload = Path(path).read_text(encoding="utf-8")
        return V3DatasetBindingReceipt.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise V3DatasetBindingError("v3 dataset binding receipt is unavailable or invalid") from exc


def verify_v3_dataset_binding_design(
    manifest: V3DatasetBindingManifest,
    *,
    root: str | Path = ".",
) -> V3StudyDesign:
    """Verify the immutable D3C design and the selected dataset identities."""

    repository = Path(root)
    design_path = repository / manifest.design_uri
    try:
        design = load_v3_study_design(design_path)
    except ValueError as exc:
        raise V3DatasetBindingError("cannot validate the v3 design bound by D4A") from exc
    if design.canonical_sha256() != manifest.design_sha256:
        raise V3DatasetBindingError("D4A manifest is bound to another v3 design")
    expected = tuple(
        (item.dataset_id, item.role, item.uci_id, item.doi) for item in design.new_datasets
    )
    observed = tuple(
        (item.dataset_id, item.role, item.uci_id, item.doi) for item in manifest.datasets
    )
    if observed != expected:
        raise V3DatasetBindingError("D4A datasets differ from the outcome-free v3 design")
    return design


def acquire_v3_dataset_archives(
    manifest: V3DatasetBindingManifest,
    *,
    destination: str | Path,
) -> tuple[Path, ...]:
    """Acquire only the SHA-pinned official UCI archives."""

    output = Path(destination)
    paths: list[Path] = []
    for dataset in manifest.datasets:
        try:
            path = download_pinned_file(
                url=dataset.archive.source_uri,
                sha256=dataset.archive.sha256,
                destination=output / dataset.archive.file_name,
            )
        except (ChecksumError, OSError, ValueError) as exc:
            raise V3DatasetBindingError(
                f"cannot acquire pinned archive for {dataset.dataset_id}"
            ) from exc
        paths.append(path)
    return tuple(paths)


def _archive_member(dataset: V3DatasetBinding, archive_path: Path) -> bytes:
    try:
        is_file = archive_path.is_file()
        byte_count = archive_path.stat().st_size
    except OSError as exc:
        raise V3DatasetBindingError("cannot inspect the bound dataset archive") from exc
    if not is_file or byte_count != dataset.archive.byte_count:
        raise V3DatasetBindingError("archive byte count does not match the binding")
    if sha256_file(archive_path) != dataset.archive.sha256:
        raise V3DatasetBindingError("archive SHA-256 does not match the binding")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            files = tuple(info for info in archive.infolist() if not info.is_dir())
            if len(files) != dataset.archive.member_count:
                raise V3DatasetBindingError("archive member census does not match the binding")
            info = files[0]
            member = PurePosixPath(info.filename)
            if (
                info.filename != dataset.archive.member_path
                or member.is_absolute()
                or any(part in {"", ".", ".."} for part in member.parts)
                or info.file_size != dataset.archive.member_byte_count
                or info.compress_type != zipfile.ZIP_STORED
                or bool(info.flag_bits & 0x1)
            ):
                raise V3DatasetBindingError("archive member identity is unsafe or mismatched")
            content = archive.read(info)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise V3DatasetBindingError("cannot read the pinned dataset archive") from exc
    if len(content) != dataset.archive.member_byte_count or (
        hashlib.sha256(content).hexdigest() != dataset.archive.member_sha256
    ):
        raise V3DatasetBindingError("archive member bytes do not match the binding")
    return content


def _read_snapshot(dataset: V3DatasetBinding, content: bytes) -> pd.DataFrame:
    try:
        if dataset.parser.format == "xls":
            frame = pd.read_excel(
                io.BytesIO(content),
                engine="xlrd",
                sheet_name=dataset.parser.sheet_name,
                header=dataset.parser.header_row,
            )
        else:
            frame = pd.read_csv(
                io.BytesIO(content),
                sep=dataset.parser.delimiter,
                header=dataset.parser.header_row,
                keep_default_na=dataset.parser.keep_default_na,
                engine="c",
            )
    except (ImportError, OSError, ValueError) as exc:
        raise V3DatasetBindingError("cannot parse the pinned dataset member") from exc
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise V3DatasetBindingError("pinned dataset member produced an empty frame")
    return frame


def _target_token(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real) and math.isfinite(float(value)) and float(value).is_integer():
        return str(int(float(value)))
    return str(value).strip().lower()


def _missing_or_blank_cells(frame: pd.DataFrame) -> int:
    missing = int(frame.isna().to_numpy().sum())
    blank = 0
    for column in frame.columns:
        series = frame[column]
        if pd.api.types.is_object_dtype(series.dtype) or pd.api.types.is_string_dtype(series.dtype):
            blank += int(
                series.map(lambda value: isinstance(value, str) and not value.strip()).sum()
            )
    return missing + blank


def inspect_v3_dataset_archive(
    *,
    manifest_sha256: str,
    dataset: V3DatasetBinding,
    archive_path: str | Path,
) -> DatasetBindingAudit:
    """Inspect only identity, schema, eligibility and duplicate leakage risks."""

    checked, frame = load_v3_dataset_snapshot_for_registration(
        dataset=dataset,
        archive_path=archive_path,
    )
    return _audit_v3_dataset_snapshot(
        manifest_sha256=manifest_sha256,
        checked=checked,
        frame=frame,
    )


def load_v3_dataset_snapshot_for_registration(
    *,
    dataset: V3DatasetBinding,
    archive_path: str | Path,
) -> tuple[V3DatasetBinding, pd.DataFrame]:
    """Load a verified snapshot for outcome-free protocol compilation only."""

    checked = V3DatasetBinding.model_validate(dataset.model_dump())
    content = _archive_member(checked, Path(archive_path))
    frame = _read_snapshot(checked, content)
    return checked, frame


def _audit_v3_dataset_snapshot(
    *,
    manifest_sha256: str,
    checked: V3DatasetBinding,
    frame: pd.DataFrame,
) -> DatasetBindingAudit:
    columns = tuple(str(column) for column in frame.columns)
    if columns != checked.source_columns or len(columns) != len(set(columns)):
        raise V3DatasetBindingError("observed source schema differs from the binding")
    if len(frame) != checked.expected_row_count:
        raise V3DatasetBindingError("observed row count differs from the binding")
    missing = _missing_or_blank_cells(frame)
    if missing:
        raise V3DatasetBindingError("pinned dataset contains a missing or blank cell")

    tokens = tuple(_target_token(value) for value in frame[checked.target.column].tolist())
    allowed = {checked.target.negative_token, checked.target.positive_token}
    if set(tokens) != allowed:
        raise V3DatasetBindingError("observed target encoding differs from the binding")
    counts: dict[str, int] = {token: tokens.count(token) for token in sorted(allowed)}
    if counts != checked.expected_class_counts:
        raise V3DatasetBindingError("observed target class counts differ from the binding")
    minimum_class_count = min(counts.values())
    if minimum_class_count < checked.minimum_records_per_class:
        raise V3DatasetBindingError("dataset is below the prospective class-count floor")

    if checked.identifier_columns:
        identifiers = tuple(
            "|".join(str(frame.at[index, column]).strip() for column in checked.identifier_columns)
            for index in frame.index
        )
        identifier_is_unique = len(identifiers) == len(set(identifiers)) and all(identifiers)
    else:
        identifiers = tuple(f"{checked.dataset_id}-row-{index:05d}" for index in range(len(frame)))
        identifier_is_unique = True
    if not identifier_is_unique:
        raise V3DatasetBindingError("record identifiers are empty or non-unique")

    feature_frame = frame.loc[:, list(checked.analysis_features)]
    duplicate_mask = feature_frame.duplicated(keep=False)
    duplicate_rows = int(duplicate_mask.sum())
    duplicate_group_count = int(feature_frame.loc[duplicate_mask].drop_duplicates().shape[0])
    duplicate_targets = feature_frame.loc[duplicate_mask].copy()
    duplicate_targets["__target_token__"] = [
        tokens[index] for index, selected in enumerate(duplicate_mask) if selected
    ]
    if duplicate_group_count:
        group_nunique = duplicate_targets.groupby(
            list(checked.analysis_features), dropna=False, sort=False
        )["__target_token__"].nunique()
        conflicting_groups = int((group_nunique > 1).sum())
    else:
        conflicting_groups = 0

    source_schema_sha256 = canonical_sha256(
        {
            "columns": columns,
            "categorical_features": checked.categorical_features,
            "numeric_features": checked.numeric_features,
            "excluded_features": checked.excluded_feature_columns,
        }
    )
    return DatasetBindingAudit(
        manifest_sha256=manifest_sha256,
        dataset_id=checked.dataset_id,
        role=checked.role,
        archive_sha256=checked.archive.sha256,
        member_sha256=checked.archive.member_sha256,
        archive_byte_count=checked.archive.byte_count,
        member_byte_count=checked.archive.member_byte_count,
        row_count=len(frame),
        column_count=len(columns),
        source_columns=columns,
        analysis_feature_columns=checked.analysis_features,
        excluded_feature_columns=checked.excluded_feature_columns,
        target_column=checked.target.column,
        positive_token=checked.target.positive_token,
        negative_token=checked.target.negative_token,
        class_counts=counts,
        minimum_class_count=minimum_class_count,
        missing_or_blank_cell_count=missing,
        identifier_is_unique=identifier_is_unique,
        duplicate_group_count=duplicate_group_count,
        rows_in_duplicate_groups=duplicate_rows,
        conflicting_target_duplicate_group_count=conflicting_groups,
        source_schema_sha256=source_schema_sha256,
        record_identity_sha256=canonical_sha256({"record_ids": identifiers}),
        target_binding_sha256=canonical_sha256(
            {"record_ids": identifiers, "target_tokens": tokens}
        ),
        eligible=True,
    )


def build_v3_dataset_binding_receipt(
    manifest: V3DatasetBindingManifest,
    *,
    archive_directory: str | Path,
) -> V3DatasetBindingReceipt:
    """Build a deterministic receipt without splitting data or fitting a model."""

    checked = V3DatasetBindingManifest.model_validate(manifest.model_dump())
    manifest_sha256 = checked.canonical_sha256()
    directory = Path(archive_directory)
    audits = tuple(
        inspect_v3_dataset_archive(
            manifest_sha256=manifest_sha256,
            dataset=dataset,
            archive_path=directory / dataset.archive.file_name,
        )
        for dataset in checked.datasets
    )
    return V3DatasetBindingReceipt(
        manifest_sha256=manifest_sha256,
        datasets=audits,
        all_datasets_eligible=True,
        model_fitted=False,
        predictive_metrics_generated=False,
        sealed_outcomes_generated=False,
        registration_authorized=False,
        execution_authorized=False,
    )


def verify_v3_dataset_binding_receipt(
    observed: V3DatasetBindingReceipt,
    expected: V3DatasetBindingReceipt,
) -> None:
    """Fail closed unless the recomputed receipt is byte-semantically identical."""

    left = V3DatasetBindingReceipt.model_validate(observed.model_dump())
    right = V3DatasetBindingReceipt.model_validate(expected.model_dump())
    if left.canonical_sha256() != right.canonical_sha256():
        raise V3DatasetBindingError(
            "recomputed v3 dataset receipt differs from the tracked receipt"
        )
