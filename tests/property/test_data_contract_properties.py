"""Property tests for manifests, paths, and immutable artifacts.

Covered invariants:
  1.  Record inventory unique and complete
  2.  Row shuffle does not change identity (order-insensitive contract)
  3.  Add / remove / replace one member changes membership identity
  4.  Wrong dataset/split/census bind rejected at validator
  5.  model_copy / model_construct forge fails at re-validation
  6.  Unknown field top-level and nested rejected
  7.  Empty / blank / duplicate record ID rejected
  8.  POSIX absolute, Windows drive, UNC, '..' and mixed-separator paths rejected
  9.  Immutable writer is idempotent for same bytes
  10. Immutable writer refuses to overwrite different content
  11. Write failure leaves no partial staging artifact
  12. Symlink escape rejected (using make_symlink fixture)
  13. Serialized artifact contains no raw customer ID or absolute local path
  14. Two processes with PYTHONHASHSEED=1 and 999 produce byte-identical output
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.data.manifest import (
    RECORD_ID_HASH_SCHEMA_VERSION,
    RECORD_MEMBERSHIP_SCHEMA_VERSION,
    DatasetColumn,
    DatasetSnapshotManifest,
    manifest_artifact_bytes,
    record_inventory,
    record_membership_sha256,
    write_manifest,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimal reusable column schema for tests
_COLUMNS_2: tuple[DatasetColumn, ...] = (
    DatasetColumn(name="cid", logical_type="string", role="identifier", nullable=False),
    DatasetColumn(name="label", logical_type="string", role="target", nullable=False),
)

# ---------------------------------------------------------------------------
# Independent oracle
# Expected counts are computed independently,
# not reusing production measurement functions.
# ---------------------------------------------------------------------------


def _oracle_record_id_sha256(record_id: str) -> str:
    """Compute expected record-ID hash without calling the production function.

    Oracle: sort keys, compact separators, UTF-8, SHA-256 — all from first
    principles, NOT reusing record_id_sha256 or canonical_json.
    """
    json_str = json.dumps(
        {
            "record_id": record_id,
            "schema_version": RECORD_ID_HASH_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


def _oracle_record_membership_sha256(hashes: tuple[str, ...]) -> str:
    """Compute expected membership hash without calling the production function."""
    json_str = json.dumps(
        {
            "record_id_hashes": list(hashes),
            "schema_version": RECORD_MEMBERSHIP_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Minimal manifest builder
# ---------------------------------------------------------------------------


def _minimal_manifest(
    record_ids: list[str], **overrides: object
) -> DatasetSnapshotManifest:
    """Build the smallest valid DatasetSnapshotManifest for property tests."""
    inventory = record_inventory(record_ids)
    membership = record_membership_sha256(inventory)
    kwargs: dict[str, object] = {
        "dataset_id": "test01",
        "source_uri": "https://example.com/data.csv",
        "source_version": "v1",
        "preprocessing_version": "v1",
        "normalized_relative_path": "data/test.json",
        "dataset_sha256": "a" * 64,
        "size_bytes": 1024,
        "n_rows": len(record_ids),
        "n_cols": 2,
        "columns": _COLUMNS_2,
        "id_column": "cid",
        "target_column": "label",
        "record_id_hashes": inventory,
        "record_membership_sha256": membership,
    }
    kwargs.update(overrides)
    return DatasetSnapshotManifest(**kwargs)


def _unique_subdir(record_ids: list[str]) -> str:
    """Deterministic subdirectory derived from record content for test isolation.

    Prevents cross-example file conflicts when multiple Hypothesis examples
    share one tmp_path.
    """
    key = hashlib.sha256(str(sorted(record_ids)).encode()).hexdigest()[:16]
    return key


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def valid_record_ids(draw, *, min_size: int = 1, max_size: int = 8):  # type: ignore[no-untyped-def]
    """Unique list of valid ASCII record IDs for property tests."""
    numeric_ids = draw(
        st.lists(
            st.integers(min_value=0, max_value=1_000_000_000),
            min_size=min_size,
            max_size=max_size,
            unique=True,
        )
    )
    return [f"record-{numeric_id}" for numeric_id in numeric_ids]


# ---------------------------------------------------------------------------
# Record inventory is unique and complete
# ---------------------------------------------------------------------------


@given(record_ids=valid_record_ids(min_size=1, max_size=8))
@example(record_ids=["bob", "alice"])  # reversed alphabetical — sort boundary
@example(record_ids=["z"])             # single-record boundary
def test_record_inventory_unique_and_complete(record_ids: list[str]) -> None:
    """record_inventory produces a sorted, unique, complete hash tuple.

    Expected hash values verified against an independent oracle built from
    json.dumps + hashlib without calling record_id_sha256 or record_inventory
    without calling a production counting helper.
    """
    inventory = record_inventory(record_ids)

    # Completeness: one hash per input record
    assert len(inventory) == len(record_ids)

    # Uniqueness: no duplicate hashes
    assert len(set(inventory)) == len(record_ids)

    # Sort order: ascending lexicographic
    assert inventory == tuple(sorted(inventory))

    # Independent oracle: compute without the production function
    oracle = tuple(sorted(_oracle_record_id_sha256(rid) for rid in record_ids))
    assert inventory == oracle


# ---------------------------------------------------------------------------
# Row shuffle does not change inventory
# ---------------------------------------------------------------------------


@given(record_ids=valid_record_ids(min_size=2, max_size=8))
@example(record_ids=["bob", "alice"])
@example(record_ids=["z", "a", "m", "b"])
def test_row_shuffle_does_not_change_inventory(record_ids: list[str]) -> None:
    """Shuffling input order does not change inventory or membership SHA-256.

    Metamorphic property: two orderings of the same record set are equivalent.
    """
    reversed_ids = list(reversed(record_ids))

    assert record_inventory(record_ids) == record_inventory(reversed_ids)

    inv_fwd = record_inventory(record_ids)
    inv_rev = record_inventory(reversed_ids)
    assert record_membership_sha256(inv_fwd) == record_membership_sha256(inv_rev)


# ---------------------------------------------------------------------------
# Adding, removing, or replacing a member changes membership
# ---------------------------------------------------------------------------


@given(all_ids=valid_record_ids(min_size=3, max_size=7))
@example(all_ids=["alice", "bob", "charlie"])
def test_add_member_changes_membership(all_ids: list[str]) -> None:
    """Adding one new record changes the membership SHA-256."""
    record_ids, new_id = all_ids[:-1], all_ids[-1]
    original = record_membership_sha256(record_inventory(record_ids))
    extended = record_membership_sha256(record_inventory([*record_ids, new_id]))
    assert original != extended


@given(record_ids=valid_record_ids(min_size=2, max_size=6))
@example(record_ids=["alice", "bob"])
def test_remove_member_changes_membership(record_ids: list[str]) -> None:
    """Removing one record changes the membership SHA-256."""
    original = record_membership_sha256(record_inventory(record_ids))
    reduced = record_membership_sha256(record_inventory(record_ids[1:]))
    assert original != reduced


@given(all_ids=valid_record_ids(min_size=3, max_size=7))
@example(all_ids=["alice", "bob", "charlie"])
def test_replace_member_changes_membership(all_ids: list[str]) -> None:
    """Replacing one record with a different one changes membership SHA-256."""
    record_ids, replacement = all_ids[:-1], all_ids[-1]
    original = record_membership_sha256(record_inventory(record_ids))
    replaced = record_membership_sha256(
        record_inventory([replacement, *record_ids[1:]])
    )
    assert original != replaced


# ---------------------------------------------------------------------------
# Incorrect source bindings are rejected
# ---------------------------------------------------------------------------


def test_wrong_membership_sha256_rejected_at_construction() -> None:
    """A forged record_membership_sha256 fails the cross-field validator.

    The model validator recomputes the membership hash from record_id_hashes
    and rejects any mismatch — preventing wrong dataset/census binds.
    """
    inventory = record_inventory(["rec-001", "rec-002"])
    with pytest.raises(ValidationError, match="record_membership_sha256"):
        DatasetSnapshotManifest(
            dataset_id="test01",
            source_uri="https://example.com/data.csv",
            source_version="v1",
            preprocessing_version="v1",
            normalized_relative_path="data/test.json",
            dataset_sha256="a" * 64,
            size_bytes=1024,
            n_rows=2,
            n_cols=2,
            columns=_COLUMNS_2,
            id_column="cid",
            target_column="label",
            record_id_hashes=inventory,
            record_membership_sha256="0" * 64,  # deliberately forged
        )


def test_wrong_n_rows_rejected_at_construction() -> None:
    """Declared n_rows not matching actual inventory size is rejected."""
    inventory = record_inventory(["rec-001", "rec-002"])
    membership = record_membership_sha256(inventory)
    with pytest.raises(ValidationError, match="n_rows"):
        DatasetSnapshotManifest(
            dataset_id="test01",
            source_uri="https://example.com/data.csv",
            source_version="v1",
            preprocessing_version="v1",
            normalized_relative_path="data/test.json",
            dataset_sha256="a" * 64,
            size_bytes=1024,
            n_rows=99,  # wrong: inventory has exactly 2 records
            n_cols=2,
            columns=_COLUMNS_2,
            id_column="cid",
            target_column="label",
            record_id_hashes=inventory,
            record_membership_sha256=membership,
        )


# ---------------------------------------------------------------------------
# Model-copy and model-construct forgeries fail revalidation
# ---------------------------------------------------------------------------


def test_model_copy_forge_rejected_by_revalidation() -> None:
    """A forged field introduced via model_copy is caught at model_validate.

    model_copy bypasses field validators; model_validate re-runs all of them.
    """
    manifest = _minimal_manifest(["rec-001", "rec-002"])
    forged = manifest.model_copy(update={"record_membership_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="record_membership_sha256"):
        DatasetSnapshotManifest.model_validate(forged.model_dump(warnings=False))


def test_model_construct_forge_rejected_by_revalidation() -> None:
    """A forged field via model_construct is caught at model_validate.

    model_construct skips __init__ validators; model_validate enforces them.
    """
    manifest = _minimal_manifest(["rec-001"])
    forged = DatasetSnapshotManifest.model_construct(
        **{**manifest.model_dump(warnings=False), "record_membership_sha256": "0" * 64}
    )
    with pytest.raises(ValidationError, match="record_membership_sha256"):
        DatasetSnapshotManifest.model_validate(forged.model_dump(warnings=False))


# ---------------------------------------------------------------------------
# Unknown fields are rejected at every depth
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_rejected() -> None:
    """DatasetSnapshotManifest rejects any unknown extra top-level field."""
    inventory = record_inventory(["rec-001"])
    with pytest.raises(ValidationError, match="Extra inputs"):
        DatasetSnapshotManifest(
            dataset_id="test01",
            source_uri="https://example.com/data.csv",
            source_version="v1",
            preprocessing_version="v1",
            normalized_relative_path="data/test.json",
            dataset_sha256="a" * 64,
            size_bytes=1024,
            n_rows=1,
            n_cols=2,
            columns=_COLUMNS_2,
            id_column="cid",
            target_column="label",
            record_id_hashes=inventory,
            record_membership_sha256=record_membership_sha256(inventory),
            INJECTED_FIELD="poison",  # must be rejected (extra="forbid")
        )


def test_unknown_nested_field_rejected() -> None:
    """DatasetColumn rejects unknown nested fields."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        DatasetColumn(
            name="cid",
            logical_type="string",
            role="identifier",
            nullable=False,
            hidden_grade="A+",  # must be rejected
        )


# ---------------------------------------------------------------------------
# Invalid record identifiers are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "bad_ids"),
    [
        ("empty string", [""]),
        ("whitespace only", ["   "]),
        ("leading space", [" alice"]),
        ("trailing space", ["alice "]),
        ("duplicate entries", ["alice", "alice"]),
        ("empty in non-empty list", ["alice", ""]),
    ],
)
def test_bad_record_ids_rejected(description: str, bad_ids: list[str]) -> None:
    """Empty, blank, space-padded, and duplicate record IDs are rejected."""
    with pytest.raises(ValueError):
        record_inventory(bad_ids)


# ---------------------------------------------------------------------------
# Path traversal is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "bad_path"),
    [
        ("POSIX absolute", "/etc/passwd"),
        ("Windows drive path", "C:\\file.json"),
        ("UNC path", "//server/share/file.json"),
        ("parent traversal", "../escape/secret.json"),
        ("double-dot nested", "data/../../../etc/passwd"),
        ("mixed separator backslash", "dir\\file.json"),
        ("current dir dot", "./data/file.json"),
    ],
)
def test_path_traversal_rejected_at_manifest_construction(
    description: str, bad_path: str
) -> None:
    """Absolute, traversing and mixed-separator paths rejected at manifest
    construction via the _portable_relative_path field validator.
    """
    inventory = record_inventory(["rec-001"])
    membership = record_membership_sha256(inventory)
    with pytest.raises((ValidationError, ValueError)):
        DatasetSnapshotManifest(
            dataset_id="test01",
            source_uri="https://example.com/data.csv",
            source_version="v1",
            preprocessing_version="v1",
            normalized_relative_path=bad_path,
            dataset_sha256="a" * 64,
            size_bytes=1024,
            n_rows=1,
            n_cols=2,
            columns=_COLUMNS_2,
            id_column="cid",
            target_column="label",
            record_id_hashes=inventory,
            record_membership_sha256=membership,
        )


def test_traversal_rejected_by_write_manifest(tmp_path: Path) -> None:
    """write_manifest rejects traversal relative_path at write time."""
    manifest = _minimal_manifest(["rec-001"])
    with pytest.raises(ValueError):
        write_manifest(manifest, output_root=tmp_path, relative_path="../escape.json")


# ---------------------------------------------------------------------------
# Immutable writer is idempotent for identical bytes
# ---------------------------------------------------------------------------


@given(record_ids=valid_record_ids(min_size=1, max_size=5))
@example(record_ids=["rec-001", "rec-002"])
def test_write_manifest_idempotent(record_ids: list[str]) -> None:
    """Writing the same manifest twice succeeds and returns the same Path.

    Uses tempfile.TemporaryDirectory instead of the tmp_path fixture so that
    each generated input gets a fresh isolated directory — avoids the
    HealthCheck.function_scoped_fixture health check without suppression.
    """
    import tempfile

    manifest = _minimal_manifest(record_ids)
    with tempfile.TemporaryDirectory() as root:
        root_path = Path(root)
        path1 = write_manifest(manifest, output_root=root_path, relative_path="m.json")
        path2 = write_manifest(manifest, output_root=root_path, relative_path="m.json")
        assert path1 == path2
        assert path1.read_bytes() == manifest_artifact_bytes(manifest)


# ---------------------------------------------------------------------------
# Immutable writer refuses different content
# ---------------------------------------------------------------------------


def test_write_manifest_refuses_overwrite_different_content(
    tmp_path: Path,
) -> None:
    """write_manifest raises FileExistsError when destination has different bytes."""
    manifest1 = _minimal_manifest(["rec-001"])
    manifest2 = _minimal_manifest(["rec-002"])  # different record → different bytes
    write_manifest(manifest1, output_root=tmp_path, relative_path="m.json")
    with pytest.raises(FileExistsError, match="non-identical immutable manifest"):
        write_manifest(manifest2, output_root=tmp_path, relative_path="m.json")


# ---------------------------------------------------------------------------
# Write failure leaves no staging artifact
# ---------------------------------------------------------------------------


def test_write_failure_no_staging_artifact(tmp_path: Path) -> None:
    """A failed write_manifest call removes any temporary staging file.

    Production implementation uses try/finally with stage.unlink(missing_ok=True)
    to prevent partial artifacts surviving a failed write.
    """
    manifest = _minimal_manifest(["rec-001"])
    dest = tmp_path / "m.json"
    dest.write_bytes(b"pre-existing different content -- triggers FileExistsError")
    with pytest.raises(FileExistsError):
        write_manifest(manifest, output_root=tmp_path, relative_path="m.json")
    stage_files = [f for f in tmp_path.iterdir() if f.name.endswith(".stage")]
    assert stage_files == [], (
        f"Staging artifacts left after write failure: {[f.name for f in stage_files]}"
    )


# ---------------------------------------------------------------------------
# Symlink escapes are rejected
# ---------------------------------------------------------------------------


def test_symlinked_output_root_rejected(
    tmp_path: Path, make_symlink: object
) -> None:
    """write_manifest rejects a symlinked output root directory.

    Uses the make_symlink fixture — does not call symlink_to directly.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    make_symlink(link_dir, real_dir)  # type: ignore[operator]
    manifest = _minimal_manifest(["rec-001"])
    with pytest.raises(ValueError, match="symlink"):
        write_manifest(manifest, output_root=link_dir, relative_path="m.json")


def test_symlinked_parent_directory_rejected(
    tmp_path: Path, make_symlink: object
) -> None:
    """write_manifest rejects a symlinked intermediate directory in the path."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_subdir = tmp_path / "link"
    make_symlink(link_subdir, real_dir)  # type: ignore[operator]
    manifest = _minimal_manifest(["rec-001"])
    with pytest.raises(ValueError, match="symlink"):
        write_manifest(
            manifest,
            output_root=tmp_path,
            relative_path="link/m.json",
        )


# ---------------------------------------------------------------------------
# Serialized artifacts contain no raw customer identifiers or absolute paths
# ---------------------------------------------------------------------------


def test_artifact_contains_no_raw_customer_ids() -> None:
    """Serialized manifest artifact does not expose raw record IDs.

    record_inventory stores only SHA-256 hashes.  Raw IDs here use uppercase
    letters outside [a-f] that cannot appear in any SHA-256 hex digest, so
    their presence in the artifact is an unambiguous contract breach.
    """
    record_ids = [
        "RAWCUST-SUBSCRIBER-001-GHIJK",
        "RAWCUST-SUBSCRIBER-002-MNOPQ",
        "RAWCUST-SUBSCRIBER-003-RSTUV",
    ]
    manifest = _minimal_manifest(record_ids)
    artifact_str = manifest_artifact_bytes(manifest).decode("utf-8")
    for record_id in record_ids:
        assert record_id not in artifact_str, (
            f"Raw customer ID {record_id!r} found in serialized artifact"
        )


def test_artifact_normalized_path_is_relative() -> None:
    """The stored normalized_relative_path is relative — no absolute machine-local
    paths appear in the artifact.
    """
    manifest = _minimal_manifest(["rec-001"])
    artifact_str = manifest_artifact_bytes(manifest).decode("utf-8")
    assert not manifest.normalized_relative_path.startswith("/")
    assert ":\\" not in artifact_str
    # Only the declared source_uri should contain "://"
    assert "://" not in artifact_str.replace("https://example.com/data.csv", "")


# ---------------------------------------------------------------------------
# Hash-seed invariance across processes
# ---------------------------------------------------------------------------

# Subprocess script: uses installed package, no sys.path hacks.
# sys.executable ensures no hard-coded 'python' or 'python3'.
_HASH_SEED_SCRIPT = "\n".join([
    "import json",
    "from aletheia_lab.data.manifest import (",
    "    DatasetColumn, DatasetSnapshotManifest,",
    "    manifest_artifact_bytes, record_inventory, record_membership_sha256,",
    ")",
    'record_ids = ["alice-001", "bob-002", "charlie-003", "diana-004"]',
    "inventory = record_inventory(record_ids)",
    "membership = record_membership_sha256(inventory)",
    "columns = (",
    '    DatasetColumn(name="cid", logical_type="string", role="identifier", nullable=False),',
    '    DatasetColumn(name="label", logical_type="string", role="target", nullable=False),',
    ")",
    "manifest = DatasetSnapshotManifest(",
    '    dataset_id="test01",',
    '    source_uri="https://example.com/data.csv",',
    '    source_version="v1",',
    '    preprocessing_version="v1",',
    '    normalized_relative_path="data/test.json",',
    '    dataset_sha256="a" * 64,',
    "    size_bytes=1024,",
    "    n_rows=4,",
    "    n_cols=2,",
    "    columns=columns,",
    '    id_column="cid",',
    '    target_column="label",',
    "    record_id_hashes=inventory,",
    "    record_membership_sha256=membership,",
    ")",
    "artifact_hex = manifest_artifact_bytes(manifest).hex()",
    "inventory_json = json.dumps(sorted(list(inventory)))",
    'print(json.dumps({"artifact": artifact_hex, "inventory": inventory_json}))',
])


def test_pythonhashseed_invariance() -> None:
    """Record inventory and manifest bytes are identical for PYTHONHASHSEED=1
    and PYTHONHASHSEED=999.

    Subprocess uses sys.executable — not hard-coded python or python3.
    """

    def run_with_seed(seed: str) -> str:
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return result.stdout.strip()

    output_seed_1 = run_with_seed("1")
    output_seed_999 = run_with_seed("999")

    assert output_seed_1 == output_seed_999, (
        f"Output differs between PYTHONHASHSEED=1 and PYTHONHASHSEED=999.\n"
        f"seed=1  : {output_seed_1[:120]}\n"
        f"seed=999: {output_seed_999[:120]}"
    )
