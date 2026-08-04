"""Unit tests for manifest_generation builders (job_02.md §2.2 – §2.4).

Coverage (job_02.md §2.7):
  - valid snapshot/split/family synthetic round-trip
  - duplicate/blank ID, unknown target, missing column
  - source checksum/bytes change
  - dataset/split/census replay rejection
  - overlap/missing/extra record at model level
  - forged count/rate via model_copy and model_construct
  - immutable overwrite protection
  - serialized artifact free of raw customer IDs
  - path security: traversal, Windows drive, UNC, symlink
  - no self-declared pass/eligibility fields
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.baseline.loader import DatasetSchemaError
from aletheia_lab.baseline.schema import ID_COLUMN, TARGET_COLUMN
from aletheia_lab.benchmark.p2.contracts import FamilyCensus, FamilyCensusEntry
from aletheia_lab.data.manifest import (
    BenchmarkFamilySplitManifest,
    DatasetColumn,
    DatasetSnapshotManifest,
    FamilySplitAssignment,
    ManifestContractError,
    ModelDataSplitManifest,
    RecordSplit,
    SplitRatios,
    load_manifest,
    record_inventory,
    record_membership_sha256,
    validate_benchmark_family_split,
    validate_model_data_split,
)
from aletheia_lab.data.manifest_generation import (
    build_benchmark_family_split,
    build_dataset_snapshot,
    build_model_data_split,
)

_DATASET_ID = "telco_customer_churn"
_SNAP_REL = "manifests/snapshot.json"
_SPLIT_REL = "manifests/model-split.json"
_FAMILY_REL = "manifests/family-split.json"

_FORBIDDEN_FIELDS = frozenset(
    {"passed", "valid", "verdict", "eligible", "expected_behavior"}
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, frame: pd.DataFrame) -> Path:
    """Write frame to a canonical CSV and return its path."""
    frame.to_csv(path, index=False, lineterminator="\n")
    return path


def _synthetic_census(n: int = 4) -> tuple[FamilyCensus, list[FamilySplitAssignment]]:
    """Build a minimal FamilyCensus with n entries and matching assignments.

    FamilyCensusEntry requires ``case_family_id == f"p2-family-{proposed_family_sha256}"``.
    """
    _splits = ("dev", "main", "human_audit", "organic_validity")
    entries = []
    for i in range(n):
        # 64-hex-char fingerprint: repeat a distinct hex digit 64 times
        fingerprint = f"{i % 16:x}" * 64
        entries.append(
            FamilyCensusEntry(
                case_family_id=f"p2-family-{fingerprint}",  # must equal namespaced fingerprint
                candidate_id=f"p2-candidate-{i:064x}",
                fault_type="data_drift",
                family_class="stable_control",
                proposed_family_sha256=fingerprint,
            )
        )
    census = FamilyCensus(schema_version="p2-family-census/1", entries=tuple(entries))
    assignments = [
        FamilySplitAssignment(
            case_family_id=e.case_family_id,
            split=_splits[i % len(_splits)],
        )
        for i, e in enumerate(entries)
    ]
    return census, assignments


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def processed_csv(tmp_path: Path, make_frame) -> Path:
    """240-row synthetic processed CSV in tmp_path."""
    return _write_csv(tmp_path / "processed.csv", make_frame(n=240, seed=0))


@pytest.fixture
def snap_out(
    tmp_path: Path, processed_csv: Path
) -> tuple[DatasetSnapshotManifest, Path, Path]:
    """(snapshot, csv_path, output_root) tuple for the synthetic CSV."""
    out = tmp_path / "out"
    snap = build_dataset_snapshot(
        processed_csv,
        dataset_id=_DATASET_ID,
        output_root=out,
        relative_path=_SNAP_REL,
    )
    return snap, processed_csv, out


# ---------------------------------------------------------------------------
# §2.2 — Dataset snapshot
# ---------------------------------------------------------------------------


def test_snapshot_valid_round_trip(snap_out: tuple) -> None:
    """build_dataset_snapshot writes a manifest that reload_manifest returns identically."""
    snap, _, out = snap_out
    reloaded = load_manifest(
        output_root=out,
        relative_path=_SNAP_REL,
        model_type=DatasetSnapshotManifest,
    )
    assert snap.identity_sha256() == reloaded.identity_sha256()


def test_snapshot_idempotent_second_write(tmp_path: Path, processed_csv: Path) -> None:
    """A second build_dataset_snapshot call with the same CSV succeeds (idempotent)."""
    out = tmp_path / "out"
    s1 = build_dataset_snapshot(
        processed_csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    s2 = build_dataset_snapshot(
        processed_csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    assert s1.identity_sha256() == s2.identity_sha256()


def test_snapshot_overwrite_different_content_rejected(
    tmp_path: Path, make_frame, processed_csv: Path
) -> None:
    """A second build call with different CSV content at the same path raises FileExistsError."""
    out = tmp_path / "out"
    build_dataset_snapshot(
        processed_csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    # Overwrite CSV with different content
    _write_csv(processed_csv, make_frame(n=240, seed=99))
    with pytest.raises(FileExistsError):
        build_dataset_snapshot(
            processed_csv,
            dataset_id=_DATASET_ID,
            output_root=out,
            relative_path=_SNAP_REL,
        )


def test_snapshot_duplicate_ids(tmp_path: Path, make_frame) -> None:
    """A CSV with duplicate IDs causes DatasetSchemaError."""
    frame = make_frame(n=240, seed=0)
    frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    csv = _write_csv(tmp_path / "p.csv", frame)
    with pytest.raises(DatasetSchemaError, match="duplicate"):
        build_dataset_snapshot(
            csv,
            dataset_id=_DATASET_ID,
            output_root=tmp_path / "out",
            relative_path=_SNAP_REL,
        )


def test_snapshot_blank_id(tmp_path: Path, make_frame) -> None:
    """A CSV with a blank ID causes DatasetSchemaError."""
    frame = make_frame(n=240, seed=0)
    frame.loc[0, ID_COLUMN] = "   "
    csv = _write_csv(tmp_path / "p.csv", frame)
    with pytest.raises(DatasetSchemaError, match="blank"):
        build_dataset_snapshot(
            csv,
            dataset_id=_DATASET_ID,
            output_root=tmp_path / "out",
            relative_path=_SNAP_REL,
        )


def test_snapshot_unknown_target_label(tmp_path: Path, make_frame) -> None:
    """An unknown target label causes DatasetSchemaError."""
    frame = make_frame(n=240, seed=0)
    frame.loc[0, TARGET_COLUMN] = "Maybe"
    csv = _write_csv(tmp_path / "p.csv", frame)
    with pytest.raises(DatasetSchemaError, match="unexpected"):
        build_dataset_snapshot(
            csv,
            dataset_id=_DATASET_ID,
            output_root=tmp_path / "out",
            relative_path=_SNAP_REL,
        )


def test_snapshot_missing_required_column(tmp_path: Path, make_frame) -> None:
    """A CSV missing a required column causes DatasetSchemaError."""
    frame = make_frame(n=240, seed=0).drop(columns=["tenure"])
    csv = _write_csv(tmp_path / "p.csv", frame)
    with pytest.raises(DatasetSchemaError, match="missing"):
        build_dataset_snapshot(
            csv,
            dataset_id=_DATASET_ID,
            output_root=tmp_path / "out",
            relative_path=_SNAP_REL,
        )


def test_snapshot_extra_column_ignored(tmp_path: Path, make_frame) -> None:
    """A CSV with an extra column beyond the Telco schema succeeds.

    Extra columns are ignored by load_processed (only required columns are
    validated). The manifest uses the fixed schema from _build_telco_columns(),
    so the extra column must NOT appear in the persisted manifest.
    """
    frame = make_frame(n=240, seed=0)
    frame["extra_marketing_flag"] = "Y"  # column thừa — beyond Telco schema
    csv = _write_csv(tmp_path / "p.csv", frame)
    out = tmp_path / "out"
    snap = build_dataset_snapshot(
        csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    col_names = {c.name for c in snap.columns}
    assert "extra_marketing_flag" not in col_names, (
        "extra column must not appear in the schema-fixed manifest"
    )


def test_snapshot_duplicate_column_name_handled(tmp_path: Path, make_frame) -> None:
    """A CSV with a duplicate column name is handled without silent ID corruption.

    pandas renames the second occurrence to 'tenure.1' (column trùng).
    The renamed duplicate is treated as an extra column and must NOT appear in
    the schema-fixed manifest. Required columns remain intact so the builder
    succeeds; any silent identity change from the extra data is impossible
    because the inventory uses the fixed 'customerID' column only.
    """
    frame = make_frame(n=240, seed=0)
    # Build raw CSV text with "tenure" column duplicated in the header
    header_cols = list(frame.columns) + ["tenure"]
    header_line = ",".join(header_cols)
    data_lines = []
    for row in frame.itertuples(index=False):
        values = list(row) + [row.tenure]
        data_lines.append(",".join(str(v) for v in values))
    content = header_line + "\n" + "\n".join(data_lines) + "\n"
    csv = tmp_path / "dup.csv"
    csv.write_text(content, encoding="utf-8")

    out = tmp_path / "out"
    snap = build_dataset_snapshot(
        csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    col_names = {c.name for c in snap.columns}
    # The pandas-renamed duplicate ("tenure.1") must not appear in the manifest
    assert "tenure.1" not in col_names, (
        "duplicate column (renamed by pandas) must not leak into the schema-fixed manifest"
    )
    # The original "tenure" column must be in the schema
    assert "tenure" in col_names


def test_snapshot_serialized_no_raw_customer_id(
    tmp_path: Path, make_frame
) -> None:
    """Persisted manifest JSON must not contain raw customer IDs."""
    frame = make_frame(n=240, seed=0)
    csv = _write_csv(tmp_path / "p.csv", frame)
    out = tmp_path / "out"
    build_dataset_snapshot(
        csv, dataset_id=_DATASET_ID, output_root=out, relative_path=_SNAP_REL
    )
    content = (out / _SNAP_REL).read_text(encoding="utf-8")
    for raw_id in frame[ID_COLUMN]:
        assert str(raw_id) not in content, f"raw ID {raw_id!r} leaked into manifest"


def test_snapshot_no_forbidden_fields() -> None:
    """DatasetSnapshotManifest must not expose pass/eligibility/verdict fields."""
    assert not (set(DatasetSnapshotManifest.model_fields) & _FORBIDDEN_FIELDS), (
        f"forbidden fields: {set(DatasetSnapshotManifest.model_fields) & _FORBIDDEN_FIELDS}"
    )


# ---------------------------------------------------------------------------
# §2.3 — Model-data split
# ---------------------------------------------------------------------------


def test_split_valid_round_trip(snap_out: tuple) -> None:
    """build_model_data_split writes a manifest that reload_manifest returns identically."""
    snap, csv, out = snap_out
    split = build_model_data_split(
        snap, csv, output_root=out, relative_path=_SPLIT_REL
    )
    reloaded = load_manifest(
        output_root=out, relative_path=_SPLIT_REL, model_type=ModelDataSplitManifest
    )
    assert split.identity_sha256() == reloaded.identity_sha256()


def test_split_idempotent_second_write(snap_out: tuple) -> None:
    """A second build_model_data_split call with the same data succeeds (idempotent)."""
    snap, csv, out = snap_out
    s1 = build_model_data_split(snap, csv, output_root=out, relative_path=_SPLIT_REL)
    s2 = build_model_data_split(snap, csv, output_root=out, relative_path=_SPLIT_REL)
    assert s1.identity_sha256() == s2.identity_sha256()


def test_split_no_forbidden_fields() -> None:
    """ModelDataSplitManifest must not expose pass/eligibility/verdict fields."""
    assert not (set(ModelDataSplitManifest.model_fields) & _FORBIDDEN_FIELDS), (
        f"forbidden fields: {set(ModelDataSplitManifest.model_fields) & _FORBIDDEN_FIELDS}"
    )


def test_split_serialized_no_raw_customer_id(
    snap_out: tuple, make_frame
) -> None:
    """Persisted split manifest JSON must not contain raw customer IDs."""
    snap, csv, out = snap_out
    build_model_data_split(snap, csv, output_root=out, relative_path=_SPLIT_REL)
    content = (out / _SPLIT_REL).read_text(encoding="utf-8")
    # All synthetic IDs follow the "{i:05d}-SYNTH" pattern
    for i in range(240):
        assert f"{i:05d}-SYNTH" not in content, "raw ID leaked into split manifest"


def test_split_dataset_replay_rejected(
    snap_out: tuple, tmp_path: Path, make_frame
) -> None:
    """A split bound to snapshot_A is rejected when validated against snapshot_B."""
    snap, csv, out = snap_out
    split = build_model_data_split(snap, csv, output_root=out, relative_path=_SPLIT_REL)

    frame2 = make_frame(n=240, seed=99)
    csv2 = tmp_path / "p2.csv"
    _write_csv(csv2, frame2)
    out2 = tmp_path / "out2"
    snap2 = build_dataset_snapshot(
        csv2, dataset_id=_DATASET_ID, output_root=out2, relative_path=_SNAP_REL
    )

    with pytest.raises(ManifestContractError):
        validate_model_data_split(snap2, split)


def test_split_overlap_rejects_at_construction() -> None:
    """ModelDataSplitManifest rejects overlapping record sets at construction time."""
    ids = tuple(f"r-{i:04d}" for i in range(20))
    train_ids = ids[:14]
    val_ids = ids[14:17]
    test_ids = ids[17:]
    # Overlap: include val_ids[0] in train
    overlap_train_ids = train_ids + (val_ids[0],)

    def _rs(name: str, rec_ids: tuple[str, ...], pos: int) -> RecordSplit:
        inv = record_inventory(rec_ids)
        return RecordSplit(
            name=name,  # type: ignore[arg-type]
            record_id_hashes=inv,
            membership_sha256=record_membership_sha256(inv),
            n_records=len(inv),
            n_positive=pos,
            n_negative=len(inv) - pos,
            positive_rate=pos / len(inv),
        )

    with pytest.raises(ValidationError, match="disjoint"):
        ModelDataSplitManifest(
            dataset_id="telco_customer_churn",
            dataset_sha256="a" * 64,
            dataset_identity_sha256="b" * 64,
            n_rows=20,
            train=_rs("train", overlap_train_ids, 5),
            validation=_rs("validation", val_ids, 1),
            test=_rs("test", test_ids, 1),
        )


def test_split_missing_records_detected_by_validation() -> None:
    """validate_model_data_split detects when splits cover fewer records than the snapshot."""
    # Build a dataset with 20 records, split covers only 18
    dataset_ids = tuple(f"d-{i:04d}" for i in range(20))
    split_ids = dataset_ids[:18]  # missing last 2
    train_ids = split_ids[:13]
    val_ids = split_ids[13:16]
    test_ids = split_ids[16:]

    inventory = record_inventory(dataset_ids)


    columns = (
        DatasetColumn(
            name="customerID", logical_type="string", role="identifier", nullable=False
        ),
        DatasetColumn(
            name="Churn", logical_type="category", role="target", nullable=False
        ),
    )
    dataset = DatasetSnapshotManifest(
        dataset_id="telco_customer_churn",
        source_uri=(
            "https://raw.githubusercontent.com/IBM/"
            "telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv"
        ),
        source_version="sha256:" + "a" * 64,
        preprocessing_version="telco-clean/v1",
        normalized_relative_path="data/processed/telco.csv",
        dataset_sha256="a" * 64,
        size_bytes=128,
        n_rows=len(inventory),
        n_cols=len(columns),
        columns=columns,
        id_column="customerID",
        target_column="Churn",
        record_id_hashes=inventory,
        record_membership_sha256=record_membership_sha256(inventory),
    )

    def _rs(name: str, rec_ids: tuple[str, ...], pos: int) -> RecordSplit:
        inv = record_inventory(rec_ids)
        return RecordSplit(
            name=name,  # type: ignore[arg-type]
            record_id_hashes=inv,
            membership_sha256=record_membership_sha256(inv),
            n_records=len(inv),
            n_positive=pos,
            n_negative=len(inv) - pos,
            positive_rate=pos / len(inv),
        )

    split = ModelDataSplitManifest(
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset.dataset_sha256,
        dataset_identity_sha256=dataset.identity_sha256(),
        n_rows=len(split_ids),
        train=_rs("train", train_ids, 5),
        validation=_rs("validation", val_ids, 1),
        test=_rs("test", test_ids, 1),
        ratios=SplitRatios(),
    )
    with pytest.raises(ManifestContractError):
        validate_model_data_split(dataset, split)


def test_forged_record_split_model_copy_fails_revalidation() -> None:
    """model_copy tampering on RecordSplit is detected when the dump is re-validated."""
    ids = tuple(f"r-{i:04d}" for i in range(14))
    inv = record_inventory(ids)
    valid = RecordSplit(
        name="train",  # type: ignore[arg-type]
        record_id_hashes=inv,
        membership_sha256=record_membership_sha256(inv),
        n_records=14,
        n_positive=5,
        n_negative=9,
        positive_rate=5 / 14,
    )
    # Forge: raise n_positive without adjusting n_records
    forged = valid.model_copy(update={"n_positive": 999, "positive_rate": 999 / 14})
    with pytest.raises(ValidationError):
        RecordSplit.model_validate(forged.model_dump(warnings=False))


def test_forged_record_split_model_construct_fails_revalidation() -> None:
    """model_construct bypasses validators but re-validation exposes the tamper."""
    ids = tuple(f"r-{i:04d}" for i in range(14))
    inv = record_inventory(ids)
    forged = RecordSplit.model_construct(
        name="train",
        record_id_hashes=inv,
        membership_sha256=record_membership_sha256(inv),
        n_records=14,
        n_positive=999,  # impossible: 999 > 14
        n_negative=9,
        positive_rate=999 / 14,
    )
    with pytest.raises(ValidationError):
        RecordSplit.model_validate(forged.model_dump(warnings=False))


# ---------------------------------------------------------------------------
# §2.4 — Benchmark family split
# ---------------------------------------------------------------------------


def test_family_split_valid_round_trip(tmp_path: Path) -> None:
    """build_benchmark_family_split writes a manifest that reload_manifest returns identically."""
    census, assignments = _synthetic_census(n=4)
    out = tmp_path / "out"
    fam = build_benchmark_family_split(
        census, assignments, output_root=out, relative_path=_FAMILY_REL
    )
    reloaded = load_manifest(
        output_root=out,
        relative_path=_FAMILY_REL,
        model_type=BenchmarkFamilySplitManifest,
    )
    assert fam.identity_sha256() == reloaded.identity_sha256()


def test_family_split_no_forbidden_fields() -> None:
    """BenchmarkFamilySplitManifest must not expose pass/eligibility/verdict fields."""
    assert not (set(BenchmarkFamilySplitManifest.model_fields) & _FORBIDDEN_FIELDS)


def test_family_split_census_replay_rejected(tmp_path: Path) -> None:
    """validate_benchmark_family_split rejects a split bound to a different census."""
    census_a, assignments_a = _synthetic_census(n=4)
    # Build a different census by appending an extra entry
    # case_family_id must match f"p2-family-{proposed_family_sha256}"
    extra_fp = "9" * 64
    extra_entry = FamilyCensusEntry(
        case_family_id=f"p2-family-{extra_fp}",
        candidate_id=f"p2-candidate-{'9' * 64}",
        fault_type="data_drift",
        family_class="stable_control",
        proposed_family_sha256=extra_fp,
    )
    census_b = FamilyCensus(
        schema_version="p2-family-census/1",
        entries=census_a.entries + (extra_entry,),
    )
    out = tmp_path / "out"
    fam = build_benchmark_family_split(
        census_a, assignments_a, output_root=out, relative_path=_FAMILY_REL
    )
    with pytest.raises(ManifestContractError):
        validate_benchmark_family_split(census_b, fam)


def test_family_split_extra_assignment_rejected(tmp_path: Path) -> None:
    """An extra assignment (ID not in census) is rejected by validate_benchmark_family_split."""
    census, assignments = _synthetic_census(n=4)
    # Add an extra assignment for a non-existent family ID
    extra = FamilySplitAssignment(case_family_id="p2-fam-9999", split="dev")
    extra_assignments = assignments + [extra]
    out = tmp_path / "out"
    with pytest.raises((ManifestContractError, ValidationError)):
        build_benchmark_family_split(
            census, extra_assignments, output_root=out, relative_path=_FAMILY_REL
        )


# ---------------------------------------------------------------------------
# Path security
# ---------------------------------------------------------------------------


def test_traversal_attack_rejected(snap_out: tuple) -> None:
    """A relative_path with '..' components escaping the root is rejected."""
    snap, csv, out = snap_out
    with pytest.raises(ValueError, match="parent"):
        build_model_data_split(
            snap, csv, output_root=out, relative_path="../evil.json"
        )


def test_windows_drive_rejected(snap_out: tuple) -> None:
    """A Windows drive-letter path in relative_path is rejected."""
    snap, csv, out = snap_out
    with pytest.raises(ValueError, match="Windows drive"):
        build_model_data_split(
            snap, csv, output_root=out, relative_path="C:\\evil.json"
        )


def test_unc_path_rejected(snap_out: tuple) -> None:
    """A UNC path in relative_path is rejected ('Windows drive or UNC path')."""
    snap, csv, out = snap_out
    # //server/share is detected as a Windows UNC absolute path
    with pytest.raises(ValueError, match="Windows"):
        build_model_data_split(
            snap, csv, output_root=out, relative_path="//server/share/evil.json"
        )


def test_symlink_output_root_rejected(
    tmp_path: Path, make_frame, make_symlink
) -> None:
    """A symlinked output root is rejected by write_manifest."""
    frame = make_frame(n=240, seed=0)
    csv = _write_csv(tmp_path / "p.csv", frame)
    real_out = tmp_path / "real_out"
    real_out.mkdir()
    sym_out = tmp_path / "sym_out"
    make_symlink(sym_out, real_out)
    with pytest.raises(ValueError, match="symlink"):
        build_dataset_snapshot(
            csv,
            dataset_id=_DATASET_ID,
            output_root=sym_out,
            relative_path=_SNAP_REL,
        )
