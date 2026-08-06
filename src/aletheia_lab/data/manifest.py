"""Strict, portable manifests for dataset and benchmark membership.

The three manifests in this module deliberately describe different units:

* :class:`DatasetSnapshotManifest` identifies one immutable dataset file and
  a raw-ID-free, domain-separated record inventory;
* :class:`ModelDataSplitManifest` partitions those record hashes into train,
  validation and test sets;
* :class:`BenchmarkFamilySplitManifest` partitions accepted benchmark family
  identifiers into development and evaluation roles.

No manifest contains a self-declared pass flag.  Schema validation proves
single-object invariants, while the public ``validate_*`` functions recompute
source and cross-artifact relationships at trust boundaries.  Persistence is
canonical, immutable and rooted: callers cannot overwrite different content or
escape an output root through an absolute, traversing or symlinked path.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Final, Literal, NoReturn, TypeVar, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.manifest import Split
from aletheia_lab.benchmark.p2.contracts import FamilyCensus
from aletheia_lab.data.download import sha256_file
from aletheia_lab.evidence.schema import canonical_json, sha256_text

# --------------------------------------------------------------------------- #
# Versioned domains and frozen project policy
# --------------------------------------------------------------------------- #

DATASET_SNAPSHOT_SCHEMA_VERSION: Final[Literal["p2-dataset-snapshot/v1"]] = "p2-dataset-snapshot/v1"
MODEL_DATA_SPLIT_SCHEMA_VERSION: Final[Literal["p2-model-data-split/v1"]] = "p2-model-data-split/v1"
BENCHMARK_FAMILY_SPLIT_SCHEMA_VERSION: Final[Literal["p2-benchmark-family-split/v1"]] = (
    "p2-benchmark-family-split/v1"
)

RECORD_ID_HASH_SCHEMA_VERSION: Final[Literal["p2-record-id-hash/v1"]] = "p2-record-id-hash/v1"
RECORD_MEMBERSHIP_SCHEMA_VERSION: Final[Literal["p2-record-membership/v1"]] = (
    "p2-record-membership/v1"
)
DATASET_IDENTITY_SCHEMA_VERSION: Final[Literal["p2-dataset-identity/v1"]] = "p2-dataset-identity/v1"
MODEL_SPLIT_IDENTITY_SCHEMA_VERSION: Final[Literal["p2-model-split-identity/v1"]] = (
    "p2-model-split-identity/v1"
)
FAMILY_CENSUS_BINDING_SCHEMA_VERSION: Final[Literal["p2-family-census-binding/v1"]] = (
    "p2-family-census-binding/v1"
)
FAMILY_SPLIT_IDENTITY_SCHEMA_VERSION: Final[Literal["p2-family-split-identity/v1"]] = (
    "p2-family-split-identity/v1"
)

MODEL_SPLIT_SEED: Final[Literal[42]] = 42
MODEL_SPLIT_STRATIFIED: Final[Literal[True]] = True
TRAIN_RATIO: Final[float] = 0.7
VALIDATION_RATIO: Final[float] = 0.15
TEST_RATIO: Final[float] = 0.15

MODEL_SPLIT_NAMES: Final[tuple[str, str, str]] = ("train", "validation", "test")
FAMILY_SPLIT_NAMES: Final[tuple[str, str, str, str]] = (
    "dev",
    "main",
    "human_audit",
    "organic_validity",
)

_SHA256_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
_IDENTIFIER_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$"
_MAX_RECORDS: Final[int] = 5_000_000
_MAX_COLUMNS: Final[int] = 4_096
_MAX_FAMILIES: Final[int] = 100_000
_FLOAT_TOLERANCE: Final[float] = 1e-12

Sha256 = Annotated[str, Field(pattern=_SHA256_PATTERN)]
DatasetId = Annotated[str, Field(pattern=_IDENTIFIER_PATTERN, max_length=128)]

LogicalType = Literal["string", "integer", "float", "boolean", "category"]
ColumnRole = Literal["identifier", "target", "numeric_feature", "categorical_feature"]
ModelSplitName = Literal["train", "validation", "test"]

_ManifestT = TypeVar("_ManifestT", bound=BaseModel)


class ManifestContractError(ValueError):
    """Raised when valid manifest objects disagree with their source or peers."""


def _fail(message: str) -> NoReturn:
    raise ManifestContractError(message)


class _StrictFrozenModel(BaseModel):
    """Deeply immutable contract node with no implicit coercion or extra data."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _revalidated(model: _ManifestT) -> _ManifestT:
    """Re-run validation after unsafe ``model_copy``/``model_construct`` calls."""

    return type(model).model_validate(model.model_dump(warnings=False))


def _canonical_text(value: str, *, label: str, max_length: int = 256) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be non-blank and trimmed")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must already be Unicode NFC")
    if len(value) > max_length:
        raise ValueError(f"{label} exceeds the maximum length of {max_length}")
    return value


def _portable_relative_path(value: str) -> str:
    """Return a canonical POSIX relative path or reject it."""

    _canonical_text(value, label="relative path", max_length=1024)
    if "\\" in value or PureWindowsPath(value).is_absolute():
        raise ValueError("relative path must not be a Windows drive or UNC path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("//"):
        raise ValueError("relative path must not be absolute")
    if value in {".", ".."} or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("relative path must not contain empty, current or parent components")
    if pure.as_posix() != value:
        raise ValueError("relative path must use normalized POSIX syntax")
    return value


def _public_source_uri(value: str) -> str:
    """Reject local, credential-bearing and unstable source references."""

    _canonical_text(value, label="source URI", max_length=2048)
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("source URI must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source URI must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("source URI must not contain a query string or fragment")
    return value


def _ordered_sha256(*, schema_version: str, key: str, values: Sequence[str]) -> str:
    return sha256_text(canonical_json({"schema_version": schema_version, key: list(values)}))


def record_id_sha256(record_id: str) -> str:
    """Hash one canonical record ID without exposing the raw identifier."""

    record_id = _canonical_text(record_id, label="record ID", max_length=256)
    return sha256_text(
        canonical_json({"schema_version": RECORD_ID_HASH_SCHEMA_VERSION, "record_id": record_id})
    )


def record_inventory(record_ids: Sequence[str]) -> tuple[str, ...]:
    """Return a sorted, unique inventory of domain-separated record-ID hashes."""

    if isinstance(record_ids, str | bytes):
        raise TypeError("record inventory requires a sequence of complete record IDs")
    if not record_ids:
        raise ValueError("a record inventory must not be empty")
    if len(record_ids) > _MAX_RECORDS:
        raise ValueError("record inventory exceeds the supported size")
    normalized: list[str] = []
    seen: set[str] = set()
    for record_id in record_ids:
        canonical = _canonical_text(record_id, label="record ID", max_length=256)
        if canonical in seen:
            raise ValueError(f"record inventory contains duplicate ID {canonical!r}")
        seen.add(canonical)
        normalized.append(record_id_sha256(canonical))
    return tuple(sorted(normalized))


def record_membership_sha256(record_id_hashes: Sequence[str]) -> str:
    """Hash an already-sorted record inventory in a separate membership domain."""

    if isinstance(record_id_hashes, str | bytes):
        raise TypeError("record membership requires a sequence of SHA-256 digests")
    inventory = tuple(record_id_hashes)
    _validate_hash_inventory(inventory, label="record membership inventory")
    return _ordered_sha256(
        schema_version=RECORD_MEMBERSHIP_SCHEMA_VERSION,
        key="record_id_hashes",
        values=inventory,
    )


def _validate_hash_inventory(values: tuple[str, ...], *, label: str) -> None:
    if not values:
        raise ValueError(f"{label} must not be empty")
    if len(values) > _MAX_RECORDS:
        raise ValueError(f"{label} exceeds the supported size")
    if values != tuple(sorted(values)):
        raise ValueError(f"{label} must be sorted")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must contain unique hashes")
    if any(re.fullmatch(_SHA256_PATTERN, value) is None for value in values):
        raise ValueError(f"{label} contains an invalid SHA-256 digest")


# --------------------------------------------------------------------------- #
# Dataset snapshot
# --------------------------------------------------------------------------- #


class DatasetColumn(_StrictFrozenModel):
    """One ordered column in the processed dataset schema."""

    name: str = Field(max_length=256)
    logical_type: LogicalType
    role: ColumnRole
    nullable: bool

    @field_validator("name")
    @classmethod
    def _name_is_canonical(cls, value: str) -> str:
        return _canonical_text(value, label="column name")


class DatasetSnapshotManifest(_StrictFrozenModel):
    """One content-addressed processed dataset and its hashed record census."""

    schema_version: Literal["p2-dataset-snapshot/v1"] = DATASET_SNAPSHOT_SCHEMA_VERSION
    dataset_id: DatasetId
    source_uri: str
    source_version: str = Field(max_length=256)
    preprocessing_version: str = Field(max_length=256)
    normalized_relative_path: str
    dataset_sha256: Sha256
    size_bytes: int = Field(gt=0)
    n_rows: int = Field(gt=0, le=_MAX_RECORDS)
    n_cols: int = Field(gt=0, le=_MAX_COLUMNS)
    columns: tuple[DatasetColumn, ...]
    id_column: str = Field(max_length=256)
    target_column: str = Field(max_length=256)
    record_id_hashes: tuple[Sha256, ...]
    record_membership_sha256: Sha256

    @field_validator("source_uri")
    @classmethod
    def _source_is_public(cls, value: str) -> str:
        return _public_source_uri(value)

    @field_validator("source_version", "preprocessing_version", "id_column", "target_column")
    @classmethod
    def _text_is_canonical(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _canonical_text(value, label=str(field_name))

    @field_validator("normalized_relative_path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _portable_relative_path(value)

    @model_validator(mode="after")
    def _snapshot_is_self_consistent(self) -> DatasetSnapshotManifest:
        for column in self.columns:
            DatasetColumn.model_validate(column.model_dump(warnings=False))
        _validate_hash_inventory(self.record_id_hashes, label="record inventory")
        if self.n_rows != len(self.record_id_hashes):
            raise ValueError("n_rows must equal the unique record inventory size")
        if self.n_cols != len(self.columns):
            raise ValueError("n_cols must equal the number of column contracts")
        names = tuple(column.name for column in self.columns)
        if len(set(names)) != len(names):
            raise ValueError("dataset column names must be unique")
        if self.id_column == self.target_column:
            raise ValueError("id_column and target_column must be different")
        if self.id_column not in names or self.target_column not in names:
            raise ValueError("id_column and target_column must both exist in columns")
        identifier_roles = tuple(
            column.name for column in self.columns if column.role == "identifier"
        )
        target_roles = tuple(column.name for column in self.columns if column.role == "target")
        if identifier_roles != (self.id_column,):
            raise ValueError("exactly the declared id_column must have identifier role")
        if target_roles != (self.target_column,):
            raise ValueError("exactly the declared target_column must have target role")
        expected_membership = record_membership_sha256(self.record_id_hashes)
        if self.record_membership_sha256 != expected_membership:
            raise ValueError("record_membership_sha256 does not match record inventory")
        return self

    def identity_sha256(self) -> str:
        """Hash semantic snapshot identity, excluding the machine-local file location."""

        payload = self.model_dump(mode="json", exclude={"normalized_relative_path"})
        return sha256_text(
            canonical_json({"schema_version": DATASET_IDENTITY_SCHEMA_VERSION, "snapshot": payload})
        )


# --------------------------------------------------------------------------- #
# Model-data split
# --------------------------------------------------------------------------- #


class SplitRatios(_StrictFrozenModel):
    """The frozen, non-configurable benchmark split ratios."""

    train: float = TRAIN_RATIO
    validation: float = VALIDATION_RATIO
    test: float = TEST_RATIO

    @model_validator(mode="after")
    def _ratios_are_frozen(self) -> SplitRatios:
        if (self.train, self.validation, self.test) != (
            TRAIN_RATIO,
            VALIDATION_RATIO,
            TEST_RATIO,
        ):
            raise ValueError("split ratios are frozen at 0.70/0.15/0.15")
        return self


class RecordSplit(_StrictFrozenModel):
    """One exact split membership with recomputable label accounting."""

    name: ModelSplitName
    record_id_hashes: tuple[Sha256, ...]
    membership_sha256: Sha256
    n_records: int = Field(gt=0, le=_MAX_RECORDS)
    n_positive: int = Field(gt=0)
    n_negative: int = Field(gt=0)
    positive_rate: float = Field(ge=0.0, le=1.0)

    @field_validator("positive_rate", mode="before")
    @classmethod
    def _rate_is_finite(cls, value: object) -> object:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("positive_rate must be finite")
        return value

    @model_validator(mode="after")
    def _accounting_is_derived(self) -> RecordSplit:
        _validate_hash_inventory(self.record_id_hashes, label=f"{self.name} record inventory")
        if self.n_records != len(self.record_id_hashes):
            raise ValueError("n_records must equal the record inventory size")
        if self.n_positive + self.n_negative != self.n_records:
            raise ValueError("positive and negative counts must add up to n_records")
        expected_rate = self.n_positive / self.n_records
        if abs(self.positive_rate - expected_rate) > _FLOAT_TOLERANCE:
            raise ValueError("positive_rate must be derived from label counts")
        expected_membership = record_membership_sha256(self.record_id_hashes)
        if self.membership_sha256 != expected_membership:
            raise ValueError("membership_sha256 does not match record inventory")
        return self


class ModelDataSplitManifest(_StrictFrozenModel):
    """The exact, disjoint train/validation/test partition of one snapshot."""

    schema_version: Literal["p2-model-data-split/v1"] = MODEL_DATA_SPLIT_SCHEMA_VERSION
    dataset_id: DatasetId
    dataset_sha256: Sha256
    dataset_identity_sha256: Sha256
    n_rows: int = Field(gt=0, le=_MAX_RECORDS)
    seed: Literal[42] = MODEL_SPLIT_SEED
    stratified: Literal[True] = MODEL_SPLIT_STRATIFIED
    ratios: SplitRatios = Field(default_factory=SplitRatios)
    id_column: Literal["customerID"] = "customerID"
    target_column: Literal["Churn"] = "Churn"
    train: RecordSplit
    validation: RecordSplit
    test: RecordSplit

    @model_validator(mode="after")
    def _partition_is_exact(self) -> ModelDataSplitManifest:
        SplitRatios.model_validate(self.ratios.model_dump(warnings=False))
        partitions = (self.train, self.validation, self.test)
        for partition in partitions:
            RecordSplit.model_validate(partition.model_dump(warnings=False))
        if tuple(partition.name for partition in partitions) != MODEL_SPLIT_NAMES:
            raise ValueError("split fields must declare train, validation and test respectively")
        sets = tuple(set(partition.record_id_hashes) for partition in partitions)
        if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]:
            raise ValueError("record inventories must be disjoint across model-data splits")
        if sum(partition.n_records for partition in partitions) != self.n_rows:
            raise ValueError("split record counts must add up to n_rows")
        return self

    def identity_sha256(self) -> str:
        return sha256_text(
            canonical_json(
                {
                    "schema_version": MODEL_SPLIT_IDENTITY_SCHEMA_VERSION,
                    "model_data_split": self.model_dump(mode="json"),
                }
            )
        )


# --------------------------------------------------------------------------- #
# Benchmark-family split
# --------------------------------------------------------------------------- #


class FamilySplitAssignment(_StrictFrozenModel):
    """Assign one opaque accepted family identifier to exactly one split."""

    case_family_id: str = Field(max_length=128)
    split: Split

    @field_validator("case_family_id")
    @classmethod
    def _family_id_is_opaque_and_canonical(cls, value: str) -> str:
        return _canonical_text(value, label="case_family_id", max_length=128)


class FamilySplitCounts(_StrictFrozenModel):
    dev: int = Field(ge=0)
    main: int = Field(ge=0)
    human_audit: int = Field(ge=0)
    organic_validity: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.dev + self.main + self.human_audit + self.organic_validity


class BenchmarkFamilySplitManifest(_StrictFrozenModel):
    """Partition accepted families without copying evaluator-side census data."""

    schema_version: Literal["p2-benchmark-family-split/v1"] = BENCHMARK_FAMILY_SPLIT_SCHEMA_VERSION
    family_census_sha256: Sha256
    n_families: int = Field(gt=0, le=_MAX_FAMILIES)
    assignments: tuple[FamilySplitAssignment, ...]
    split_counts: FamilySplitCounts

    @model_validator(mode="after")
    def _assignments_are_complete_and_counted(self) -> BenchmarkFamilySplitManifest:
        for assignment in self.assignments:
            FamilySplitAssignment.model_validate(assignment.model_dump(warnings=False))
        FamilySplitCounts.model_validate(self.split_counts.model_dump(warnings=False))
        if len(self.assignments) != self.n_families:
            raise ValueError("n_families must equal the assignment count")
        ids = tuple(assignment.case_family_id for assignment in self.assignments)
        if ids != tuple(sorted(ids)):
            raise ValueError("family assignments must be sorted by case_family_id")
        if len(ids) != len(set(ids)):
            raise ValueError("each case_family_id must be assigned exactly once")
        observed = {name: 0 for name in FAMILY_SPLIT_NAMES}
        for assignment in self.assignments:
            observed[assignment.split] += 1
        if observed != self.split_counts.model_dump():
            raise ValueError("split_counts must be recomputed from assignments")
        if self.split_counts.total != self.n_families:
            raise ValueError("split counts must add up to n_families")
        return self

    def identity_sha256(self) -> str:
        return sha256_text(
            canonical_json(
                {
                    "schema_version": FAMILY_SPLIT_IDENTITY_SCHEMA_VERSION,
                    "benchmark_family_split": self.model_dump(mode="json"),
                }
            )
        )


Manifest = DatasetSnapshotManifest | ModelDataSplitManifest | BenchmarkFamilySplitManifest
_MANIFEST_TYPES: Final[tuple[type[BaseModel], ...]] = (
    DatasetSnapshotManifest,
    ModelDataSplitManifest,
    BenchmarkFamilySplitManifest,
)


def family_census_sha256(census: FamilyCensus) -> str:
    """Hash the complete validated census in a dedicated binding domain."""

    census = _revalidated(census)
    return sha256_text(
        canonical_json(
            {
                "schema_version": FAMILY_CENSUS_BINDING_SCHEMA_VERSION,
                "family_census": census.model_dump(mode="json"),
            }
        )
    )


def validate_dataset_snapshot_source(
    manifest: DatasetSnapshotManifest,
    *,
    source_file: str | Path,
    record_ids: Sequence[str],
    columns: Sequence[DatasetColumn],
) -> DatasetSnapshotManifest:
    """Recompute every source-backed snapshot field available at this boundary."""

    manifest = _revalidated(manifest)
    path = Path(source_file)
    if path.is_symlink() or not path.is_file():
        _fail("dataset source must be an existing regular file, not a symlink")
    if sha256_file(path) != manifest.dataset_sha256:
        _fail("dataset_sha256 does not match source file bytes")
    if path.stat().st_size != manifest.size_bytes:
        _fail("size_bytes does not match source file bytes")
    expected_inventory = record_inventory(record_ids)
    if expected_inventory != manifest.record_id_hashes:
        _fail("record inventory does not match source record IDs")
    if len(record_ids) != manifest.n_rows:
        _fail("n_rows does not match source record IDs")
    validated_columns = tuple(
        DatasetColumn.model_validate(column.model_dump()) for column in columns
    )
    if validated_columns != manifest.columns:
        _fail("column contracts do not match the source schema")
    return manifest


def validate_model_data_split(
    dataset: DatasetSnapshotManifest,
    split: ModelDataSplitManifest,
) -> ModelDataSplitManifest:
    """Bind a model-data partition to the exact dataset record census."""

    dataset = _revalidated(dataset)
    split = _revalidated(split)
    if split.dataset_id != dataset.dataset_id:
        _fail("model-data split dataset_id differs from dataset snapshot")
    if split.dataset_sha256 != dataset.dataset_sha256:
        _fail("model-data split dataset_sha256 differs from dataset snapshot")
    if split.dataset_identity_sha256 != dataset.identity_sha256():
        _fail("model-data split is not bound to this dataset identity")
    if split.n_rows != dataset.n_rows:
        _fail("model-data split n_rows differs from dataset snapshot")
    union = set(split.train.record_id_hashes)
    union.update(split.validation.record_id_hashes)
    union.update(split.test.record_id_hashes)
    if union != set(dataset.record_id_hashes):
        missing = len(set(dataset.record_id_hashes) - union)
        extra = len(union - set(dataset.record_id_hashes))
        _fail(f"model-data split membership differs from dataset; missing={missing}; extra={extra}")
    return split


def validate_benchmark_family_split(
    census: FamilyCensus,
    split: BenchmarkFamilySplitManifest,
) -> BenchmarkFamilySplitManifest:
    """Bind opaque family assignments to the complete accepted-family census."""

    census = _revalidated(census)
    split = _revalidated(split)
    expected_hash = family_census_sha256(census)
    if split.family_census_sha256 != expected_hash:
        _fail("benchmark-family split is not bound to this family census")
    census_ids = {entry.case_family_id for entry in census.entries}
    assigned_ids = {assignment.case_family_id for assignment in split.assignments}
    if assigned_ids != census_ids:
        missing = len(census_ids - assigned_ids)
        extra = len(assigned_ids - census_ids)
        _fail(f"family assignments differ from census; missing={missing}; extra={extra}")
    return split


# --------------------------------------------------------------------------- #
# Canonical, immutable persistence
# --------------------------------------------------------------------------- #


def manifest_artifact_bytes(manifest: Manifest) -> bytes:
    """Serialize a validated manifest with stable JSON types and one LF newline."""

    manifest = _validated_manifest(manifest)
    return canonical_json(manifest.model_dump(mode="json")).encode("utf-8") + b"\n"


def manifest_identity_sha256(manifest: Manifest) -> str:
    """Dispatch to the type-specific identity domain; no generic hash ambiguity."""

    manifest = _validated_manifest(manifest)
    if isinstance(manifest, DatasetSnapshotManifest):
        return manifest.identity_sha256()
    if isinstance(manifest, ModelDataSplitManifest):
        return manifest.identity_sha256()
    if isinstance(manifest, BenchmarkFamilySplitManifest):
        return manifest.identity_sha256()
    raise TypeError(f"unsupported manifest type: {type(manifest).__name__}")  # pragma: no cover


def _validated_manifest(manifest: object) -> Manifest:
    """Reject arbitrary BaseModels before they reach the manifest store."""

    if isinstance(manifest, DatasetSnapshotManifest):
        return DatasetSnapshotManifest.model_validate(manifest.model_dump(warnings=False))
    if isinstance(manifest, ModelDataSplitManifest):
        return ModelDataSplitManifest.model_validate(manifest.model_dump(warnings=False))
    if isinstance(manifest, BenchmarkFamilySplitManifest):
        return BenchmarkFamilySplitManifest.model_validate(manifest.model_dump(warnings=False))
    raise TypeError("only the three registered manifest contracts may be persisted")


def _safe_destination(
    output_root: str | Path,
    relative_path: str,
    *,
    create_parents: bool,
) -> tuple[Path, Path]:
    relative_path = _portable_relative_path(relative_path)
    root = Path(output_root)
    if root.is_symlink():
        raise ValueError("manifest output root must not be a symlink")
    if create_parents:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.is_dir():
        raise FileNotFoundError("manifest output root does not exist")
    resolved_root = root.resolve()
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    for part in PurePosixPath(relative_path).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("manifest destination contains a symlinked directory")
        if create_parents:
            current.mkdir(exist_ok=True)
        elif not current.is_dir():
            raise FileNotFoundError("manifest parent directory does not exist")
    if destination.is_symlink():
        raise ValueError("manifest destination must not be a symlink")
    try:
        destination.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("manifest destination escapes output root") from exc
    return resolved_root, destination


def _read_manifest_bytes(output_root: str | Path, relative_path: str) -> bytes:
    _, path = _safe_destination(output_root, relative_path, create_parents=False)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("manifest path is not an existing regular file")
    return path.read_bytes()


def write_manifest(
    manifest: Manifest,
    *,
    output_root: str | Path,
    relative_path: str,
) -> Path:
    """Persist one manifest without ever replacing different existing bytes.

    A fully written, fsynced sibling staging file is atomically hard-linked to
    the final name.  Hard-link creation fails if another writer has already
    published the destination, closing the check-then-replace race that an
    ``os.replace`` implementation would leave open.
    """

    payload = manifest_artifact_bytes(manifest)
    _, destination = _safe_destination(output_root, relative_path, create_parents=True)
    if destination.exists():
        if destination.is_file() and destination.read_bytes() == payload:
            return destination
        raise FileExistsError("refusing to overwrite a non-identical immutable manifest")

    fd, stage_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".stage", dir=destination.parent
    )
    stage = Path(stage_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(os.fspath(stage), os.fspath(destination), follow_symlinks=False)
    except BaseException:
        stage.unlink(missing_ok=True)
        raise
    finally:
        stage.unlink(missing_ok=True)
    if destination.read_bytes() != payload:
        raise OSError("persisted manifest bytes differ from the validated payload")
    return destination


def write_manifest_set(
    entries: Sequence[tuple[Manifest, str]],
    *,
    output_root: str | Path,
) -> tuple[Path, ...]:
    """Publish a set of manifests without leaving partial files on failure.

    Every payload and destination is validated before the first final file is
    linked. Existing byte-identical files are retained for idempotency; a
    conflicting file aborts the whole operation. If publication of any staged
    entry fails, only files created by this call are removed.
    """

    if not entries:
        raise ValueError("manifest set must not be empty")

    prepared: list[tuple[bytes, Path]] = []
    destinations: set[Path] = set()
    for manifest, relative_path in entries:
        payload = manifest_artifact_bytes(manifest)
        _, destination = _safe_destination(
            output_root,
            relative_path,
            create_parents=True,
        )
        if destination in destinations:
            raise ValueError("manifest set contains duplicate destination paths")
        destinations.add(destination)
        prepared.append((payload, destination))

    missing: list[tuple[bytes, Path]] = []
    for payload, destination in prepared:
        if destination.exists():
            if destination.is_file() and destination.read_bytes() == payload:
                continue
            raise FileExistsError("refusing to overwrite a non-identical immutable manifest")
        missing.append((payload, destination))

    stages: list[Path] = []
    created: list[Path] = []
    try:
        for payload, destination in missing:
            fd, stage_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".stage",
                dir=destination.parent,
            )
            stage = Path(stage_name)
            stages.append(stage)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

        for (payload, destination), stage in zip(missing, stages, strict=True):
            os.link(os.fspath(stage), os.fspath(destination), follow_symlinks=False)
            created.append(destination)
            if destination.read_bytes() != payload:
                raise OSError("persisted manifest bytes differ from the validated payload")
    except BaseException:
        for destination in reversed(created):
            destination.unlink(missing_ok=True)
        raise
    finally:
        for stage in stages:
            stage.unlink(missing_ok=True)

    return tuple(destination for _, destination in prepared)


def load_manifest(
    *,
    output_root: str | Path,
    relative_path: str,
    model_type: type[_ManifestT],
) -> _ManifestT:
    """Load one canonical manifest and reject unknown model types or byte drift."""

    if model_type not in _MANIFEST_TYPES:
        raise TypeError("model_type must be one of the three manifest contracts")
    payload = _read_manifest_bytes(output_root, relative_path)
    parsed = model_type.model_validate_json(payload)
    if payload != manifest_artifact_bytes(cast(Manifest, parsed)):
        raise ValueError("manifest is valid JSON but not canonically encoded")
    return parsed
