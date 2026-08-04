"""Builders that produce validated P2 data manifests from processed artifacts.

Each builder accepts concrete source artifacts, delegates all hashing,
validation and persistence to the official API in ``data.manifest``, and
returns an immutable, validated manifest object.  Raw customer IDs,
timestamps, absolute paths and usernames are never stored in any artifact.

Source-of-truth rules (job_02.md §2.1):
  - ``record_inventory``          — the only way to hash record IDs
  - ``manifest_identity_sha256``  — the only way to compute identity hashes
  - ``manifest_artifact_bytes``   — the only serialisation path
  - ``write_manifest``            — the only persistence path
  - ``load_manifest``             — the only load path
  - ``sha256_file``               — the only file-checksum path
  - ``get_source``                — the only source of pinned dataset metadata
  - ``ID_COLUMN``, ``TARGET_COLUMN``, ``FEATURE_COLUMNS`` — canonical column names
  - ``split_dataset``             — deterministic record-split implementation
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from aletheia_lab.baseline.loader import (
    LoadedSplits,
    SplitData,
    assert_no_overlap,
    load_processed,
    split_dataset,
)
from aletheia_lab.baseline.schema import (
    CATEGORICAL_FEATURES,
    ID_COLUMN,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
)
from aletheia_lab.benchmark.p2.contracts import FamilyCensus
from aletheia_lab.data.download import sha256_file
from aletheia_lab.data.manifest import (
    FAMILY_SPLIT_NAMES,
    TEST_RATIO,
    TRAIN_RATIO,
    VALIDATION_RATIO,
    BenchmarkFamilySplitManifest,
    DatasetColumn,
    DatasetSnapshotManifest,
    FamilySplitAssignment,
    FamilySplitCounts,
    ManifestContractError,
    ModelDataSplitManifest,
    ModelSplitName,
    RecordSplit,
    family_census_sha256,
    load_manifest,
    record_inventory,
    record_membership_sha256,
    validate_benchmark_family_split,
    validate_dataset_snapshot_source,
    validate_model_data_split,
    write_manifest,
)
from aletheia_lab.data.quality import (
    DatasetQualityReport,
    ModelSplitQualityReport,
    measure_dataset,
    measure_model_split,
)
from aletheia_lab.data.sources import get_source

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Canonical preprocessing version embedded in every Telco snapshot.
_PREPROCESSING_VERSION: str = "telco-clean/v1"

#: Numeric columns whose Telco-schema logical type is integer (not float).
_INTEGER_NUMERIC: frozenset[str] = frozenset({"SeniorCitizen", "tenure"})

#: Frozen split ratios forwarded to split_dataset (must match manifest.py constants).
_SPLIT_RATIOS: dict[str, float] = {
    "train": TRAIN_RATIO,
    "validation": VALIDATION_RATIO,
    "test": TEST_RATIO,
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_telco_columns() -> tuple[DatasetColumn, ...]:
    """Return ordered DatasetColumn contracts for the processed Telco schema.

    Order: identifier → numeric features → categorical features → target,
    matching the column ordering in ``baseline/schema.py``.
    """
    columns: list[DatasetColumn] = [
        DatasetColumn(
            name=ID_COLUMN,
            logical_type="string",
            role="identifier",
            nullable=False,
        ),
    ]
    for name in NUMERIC_FEATURES:
        if name in _INTEGER_NUMERIC:
            columns.append(
                DatasetColumn(
                    name=name,
                    logical_type="integer",
                    role="numeric_feature",
                    nullable=False,
                )
            )
        else:
            columns.append(
                DatasetColumn(
                    name=name,
                    logical_type="float",
                    role="numeric_feature",
                    nullable=False,
                )
            )
    for name in CATEGORICAL_FEATURES:
        columns.append(
            DatasetColumn(
                name=name,
                logical_type="category",
                role="categorical_feature",
                nullable=False,
            )
        )
    columns.append(
        DatasetColumn(
            name=TARGET_COLUMN,
            logical_type="category",
            role="target",
            nullable=False,
        )
    )
    return tuple(columns)


def _to_record_split(split_data: SplitData, name: ModelSplitName) -> RecordSplit:
    """Convert one SplitData partition into a RecordSplit contract.

    Counts and rate are recomputed from the binary target vector; the caller
    must never pass pre-computed counts.  ``SplitData.target`` is already
    encoded as 0/1 integers by ``split_dataset``.

    Args:
        split_data: One partition returned by ``split_dataset``.
        name: The canonical split name (must be a ``ModelSplitName`` literal).

    Returns:
        A validated ``RecordSplit`` with recomputed inventory and label counts.
    """
    ids: list[str] = [str(v) for v in split_data.ids.tolist()]
    hashes = record_inventory(ids)
    n_records = len(hashes)
    n_positive = int(split_data.target.sum())
    n_negative = n_records - n_positive
    return RecordSplit(
        name=name,
        record_id_hashes=hashes,
        membership_sha256=record_membership_sha256(hashes),
        n_records=n_records,
        n_positive=n_positive,
        n_negative=n_negative,
        positive_rate=n_positive / n_records,
    )


# ---------------------------------------------------------------------------
# Public builders — §2.2: Dataset snapshot producer
# ---------------------------------------------------------------------------


def build_dataset_snapshot(
    processed_path: str | Path,
    *,
    dataset_id: str,
    output_root: str | Path,
    relative_path: str,
) -> DatasetSnapshotManifest:
    """Build and persist a DatasetSnapshotManifest from a processed CSV.

    Steps (job_02.md §2.2):
    1. Resolve the pinned source record via ``get_source``.
    2. Load and schema-validate the processed CSV via ``load_processed``.
    3. Extract raw record IDs from ``ID_COLUMN`` — never from the DataFrame index.
    4. Hash the inventory via ``record_inventory``.
    5. Compute the processed-file checksum via ``sha256_file``.
    6. Assemble the manifest with ``source_version = "sha256:<pin>"`` and
       ``preprocessing_version = "telco-clean/v1"``.
    7. Cross-validate source-file claims via ``validate_dataset_snapshot_source``.
    8. Persist atomically via ``write_manifest``.

    Args:
        processed_path: Path to the processed CSV (regular file, not a symlink).
        dataset_id: Registered dataset ID; must be present in ``data/sources.py``.
        output_root: Root directory for manifest output.
        relative_path: Canonical POSIX-relative path inside ``output_root``
            where the manifest JSON is written.  Also recorded inside the manifest
            as ``normalized_relative_path``.

    Returns:
        The validated, persisted ``DatasetSnapshotManifest``.

    Raises:
        KeyError: ``dataset_id`` is not registered in ``sources.py``.
        DatasetSchemaError: The processed CSV does not match the expected schema.
        ManifestContractError: The manifest is inconsistent with the source file.
        FileExistsError: A different manifest already exists at ``relative_path``.
    """
    processed_path = Path(processed_path)
    source = get_source(dataset_id)
    frame = load_processed(processed_path)

    record_ids: list[str] = [str(v) for v in frame[ID_COLUMN].tolist()]
    inventory = record_inventory(record_ids)
    file_sha256 = sha256_file(processed_path)
    size_bytes = int(processed_path.stat().st_size)
    columns = _build_telco_columns()

    manifest = DatasetSnapshotManifest(
        dataset_id=dataset_id,
        source_uri=source.url,
        source_version=f"sha256:{source.sha256}",
        preprocessing_version=_PREPROCESSING_VERSION,
        normalized_relative_path=relative_path,
        dataset_sha256=file_sha256,
        size_bytes=size_bytes,
        n_rows=len(inventory),
        n_cols=len(columns),
        columns=columns,
        id_column=ID_COLUMN,
        target_column=TARGET_COLUMN,
        record_id_hashes=inventory,
        record_membership_sha256=record_membership_sha256(inventory),
    )
    validate_dataset_snapshot_source(
        manifest,
        source_file=processed_path,
        record_ids=record_ids,
        columns=columns,
    )
    write_manifest(manifest, output_root=output_root, relative_path=relative_path)
    return manifest


# ---------------------------------------------------------------------------
# Public builders — §2.3: Model-data split producer
# ---------------------------------------------------------------------------


def build_model_data_split(
    snapshot: DatasetSnapshotManifest,
    processed_path: str | Path,
    *,
    output_root: str | Path,
    relative_path: str,
) -> ModelDataSplitManifest:
    """Build and persist a ModelDataSplitManifest from a dataset snapshot.

    Steps (job_02.md §2.3):
    1. Reload and validate the processed CSV via ``load_processed``.
    2. Produce a deterministic stratified partition via ``split_dataset``
       (seed=42, stratified=True, ratios=0.70/0.15/0.15).
    3. Verify disjoint membership via ``assert_no_overlap``.
    4. Recompute each split's record inventory from ``SplitData.ids`` and
       label counts from the binary target vector.
    5. Assemble the manifest bound to this exact snapshot identity.
    6. Cross-validate the full partition against the dataset inventory via
       ``validate_model_data_split``.
    7. Persist atomically via ``write_manifest``.

    Args:
        snapshot: The validated ``DatasetSnapshotManifest`` that this split
            partitions.  The split is cryptographically bound to this identity.
        processed_path: Path to the same processed CSV that produced ``snapshot``.
        output_root: Root directory for manifest output.
        relative_path: Canonical POSIX-relative path inside ``output_root``
            where the manifest JSON is written.

    Returns:
        The validated, persisted ``ModelDataSplitManifest``.

    Raises:
        DatasetSchemaError: The processed CSV fails schema validation or has
            overlap between splits.
        ManifestContractError: The assembled split does not cover the exact
            dataset inventory, or the snapshot binding is inconsistent.
        FileExistsError: A different manifest already exists at ``relative_path``.
    """
    processed_path = Path(processed_path)
    frame = load_processed(processed_path)

    splits: LoadedSplits = split_dataset(
        frame,
        dataset_id=snapshot.dataset_id,
        dataset_sha256=snapshot.dataset_sha256,
        seed=42,
        ratios=_SPLIT_RATIOS,
        stratified=True,
    )
    assert_no_overlap(splits)

    train_split = _to_record_split(splits.train, "train")
    validation_split = _to_record_split(splits.validation, "validation")
    test_split = _to_record_split(splits.test, "test")

    model_split = ModelDataSplitManifest(
        dataset_id=snapshot.dataset_id,
        dataset_sha256=snapshot.dataset_sha256,
        dataset_identity_sha256=snapshot.identity_sha256(),
        n_rows=len(snapshot.record_id_hashes),
        train=train_split,
        validation=validation_split,
        test=test_split,
    )
    validate_model_data_split(snapshot, model_split)
    write_manifest(model_split, output_root=output_root, relative_path=relative_path)
    return model_split


# ---------------------------------------------------------------------------
# Public builders — §2.4: Benchmark family split adapter
# ---------------------------------------------------------------------------


def build_benchmark_family_split(
    census: FamilyCensus,
    assignments: Sequence[FamilySplitAssignment],
    *,
    output_root: str | Path,
    relative_path: str,
) -> BenchmarkFamilySplitManifest:
    """Wrap accepted-family assignments into a BenchmarkFamilySplitManifest.

    This is a pure adapter (job_02.md §2.4): it does not choose family IDs,
    split names or ratios.  The caller is responsible for providing a
    ``FamilyCensus`` from the research team and a complete set of
    ``FamilySplitAssignment`` objects that cover every entry in that census.
    The adapter sorts, counts, binds and validates; it decides nothing.

    Steps (job_02.md §2.4):
    1. Sort assignments by ``case_family_id`` (required by manifest contract).
    2. Recompute ``FamilySplitCounts`` from the sorted assignments.
    3. Hash the census via ``family_census_sha256`` to bind this split to it.
    4. Assemble ``BenchmarkFamilySplitManifest``.
    5. Cross-validate census coverage via ``validate_benchmark_family_split``.
    6. Persist atomically via ``write_manifest``.

    Args:
        census: The validated ``FamilyCensus`` that defines the complete set of
            accepted benchmark family identifiers.
        assignments: One ``FamilySplitAssignment`` per census entry.  The
            caller must cover all census entries; missing or extra assignments
            cause ``validate_benchmark_family_split`` to fail.
        output_root: Root directory for manifest output.
        relative_path: Canonical POSIX-relative path inside ``output_root``
            where the manifest JSON is written.

    Returns:
        The validated, persisted ``BenchmarkFamilySplitManifest``.

    Raises:
        ManifestContractError: Assignments do not cover the census, or
            split counts are inconsistent.
        FileExistsError: A different manifest already exists at ``relative_path``.
    """
    sorted_assignments: tuple[FamilySplitAssignment, ...] = tuple(
        sorted(assignments, key=lambda a: a.case_family_id)
    )

    counts: dict[str, int] = {name: 0 for name in FAMILY_SPLIT_NAMES}
    for assignment in sorted_assignments:
        counts[str(assignment.split)] += 1

    split_counts = FamilySplitCounts(
        dev=counts["dev"],
        main=counts["main"],
        human_audit=counts["human_audit"],
        organic_validity=counts["organic_validity"],
    )

    family_split = BenchmarkFamilySplitManifest(
        family_census_sha256=family_census_sha256(census),
        n_families=len(sorted_assignments),
        assignments=sorted_assignments,
        split_counts=split_counts,
    )
    validate_benchmark_family_split(census, family_split)
    write_manifest(family_split, output_root=output_root, relative_path=relative_path)
    return family_split


# ---------------------------------------------------------------------------
# Public builders — §2.6: Full data pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    processed_path: str | Path,
    *,
    dataset_id: str,
    output_root: str | Path,
    snapshot_relative_path: str = "manifests/dataset-snapshot.json",
    split_relative_path: str = "manifests/model-split.json",
) -> tuple[
    DatasetSnapshotManifest,
    ModelDataSplitManifest,
    DatasetQualityReport,
    ModelSplitQualityReport,
]:
    """Orchestrate the complete P2 data pipeline from processed CSV to quality reports.

    Pipeline stages (job_02.md §2.6):
    1. Load and schema-validate the processed CSV.
    2. Build and persist the DatasetSnapshotManifest.
    3. Build and persist the ModelDataSplitManifest.
    4. Measure dataset quality (DatasetQualityReport).
    5. Measure model-split quality (ModelSplitQualityReport).
    6. Cross-artifact re-validation on the in-memory manifests.
    7. Reload from disk and verify canonical encoding and cross-artifact binding.

    The pipeline is idempotent: running it twice against the same processed CSV
    produces byte-identical manifests; the second run returns the same objects
    without raising ``FileExistsError``.

    Family manifests are deliberately excluded: they require a validated
    ``FamilyCensus`` and researcher-provided assignments and must not be
    auto-populated.

    Args:
        processed_path: Path to the processed CSV file.
        dataset_id: Registered dataset ID.
        output_root: Root directory for all manifest output.
        snapshot_relative_path: POSIX-relative path inside ``output_root``
            for the dataset snapshot manifest.
        split_relative_path: POSIX-relative path inside ``output_root``
            for the model-data split manifest.

    Returns:
        A 4-tuple of ``(snapshot, model_split, dataset_quality, split_quality)``.

    Raises:
        DatasetSchemaError: The processed CSV does not match the expected schema.
        ManifestContractError: Any cross-artifact validation fails.
        FileExistsError: Different manifest content exists at a destination path.
    """
    processed_path = Path(processed_path)

    # Stage 1: Load and validate the processed CSV upfront
    frame = load_processed(processed_path)

    # Stage 2: Build and persist dataset snapshot
    snapshot = build_dataset_snapshot(
        processed_path,
        dataset_id=dataset_id,
        output_root=output_root,
        relative_path=snapshot_relative_path,
    )

    # Stage 3: Build and persist model-data split manifest
    model_split = build_model_data_split(
        snapshot,
        processed_path,
        output_root=output_root,
        relative_path=split_relative_path,
    )

    # Stage 4: Recompute deterministic splits for quality measurement.
    # split_dataset is deterministic; this reproduces the same partition used
    # by build_model_data_split without threading internal state across functions.
    splits: LoadedSplits = split_dataset(
        frame,
        dataset_id=snapshot.dataset_id,
        dataset_sha256=snapshot.dataset_sha256,
        seed=42,
        ratios=_SPLIT_RATIOS,
        stratified=True,
    )

    # Stage 5: Measure dataset and split quality from source artifacts
    dataset_quality = measure_dataset(snapshot, frame)
    split_quality = measure_model_split(snapshot, model_split, splits)

    # Stage 6: Cross-artifact re-validation (defense-in-depth)
    validate_model_data_split(snapshot, model_split)

    # Stage 7: Reload from disk and verify canonical encoding + binding
    reloaded_snapshot = load_manifest(
        output_root=output_root,
        relative_path=snapshot_relative_path,
        model_type=DatasetSnapshotManifest,
    )
    reloaded_split = load_manifest(
        output_root=output_root,
        relative_path=split_relative_path,
        model_type=ModelDataSplitManifest,
    )
    if reloaded_snapshot.identity_sha256() != snapshot.identity_sha256():
        raise ManifestContractError(
            "reloaded snapshot identity does not match the written snapshot"
        )
    if reloaded_split.identity_sha256() != model_split.identity_sha256():
        raise ManifestContractError(
            "reloaded split identity does not match the written split"
        )
    validate_model_data_split(reloaded_snapshot, reloaded_split)

    return snapshot, model_split, dataset_quality, split_quality