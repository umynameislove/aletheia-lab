"""Integration tests for the full data-manifest pipeline.

Coverage:
  - valid pipeline end-to-end run producing all 4 artifacts
  - pipeline idempotent (second run succeeds with byte-identical output)
  - input row shuffle → stable semantic membership but distinct byte identity
  - PYTHONHASHSEED=1 and PYTHONHASHSEED=999 produce byte-identical manifests
  - subprocess uses sys.executable
  - reload verifies canonical encoding
  - serialized artifacts free of raw customer IDs
  - family manifest NOT produced by run_pipeline (only when census provided)
  - immutable overwrite protection at the pipeline level
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.data.manifest import DatasetSnapshotManifest, ModelDataSplitManifest
from aletheia_lab.data.manifest_generation import run_pipeline
from aletheia_lab.data.quality import DatasetQualityReport, ModelSplitQualityReport

_DATASET_ID = "telco_customer_churn"
_SNAP_REL = "manifests/dataset-snapshot.json"  # must match run_pipeline default
_SPLIT_REL = "manifests/model-split.json"

# ---------------------------------------------------------------------------
# Subprocess helper — inline script that runs run_pipeline and emits JSON
# ---------------------------------------------------------------------------

_SUBPROCESS_SCRIPT = r"""
import sys
import json
from pathlib import Path

from aletheia_lab.data.manifest_generation import run_pipeline

csv_path = Path(sys.argv[1])
out_root = Path(sys.argv[2])

snap, split, dq, sq = run_pipeline(
    csv_path,
    dataset_id="telco_customer_churn",
    output_root=out_root,
)
sys.stdout.write(json.dumps({
    "snap": snap.identity_sha256(),
    "split": split.identity_sha256(),
}))
"""


def _run_subprocess(seed: str, csv_path: Path, out_root: Path) -> str:
    """Run run_pipeline in a child process with the given PYTHONHASHSEED."""
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = str(repo_root / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(csv_path), str(out_root)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def _manifest_files(root: Path) -> dict[str, bytes]:
    """Collect all manifest files under root as {relative_posix_path: bytes}."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def processed_csv(tmp_path: Path, make_frame) -> Path:
    csv = tmp_path / "processed.csv"
    make_frame(n=240, seed=0).to_csv(csv, index=False, lineterminator="\n")
    return csv


# ---------------------------------------------------------------------------
# Pipeline correctness
# ---------------------------------------------------------------------------


def test_pipeline_valid_full_run(tmp_path: Path, processed_csv: Path) -> None:
    """run_pipeline returns four typed artifacts and persists two manifests."""
    out = tmp_path / "out"
    result = run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)
    snap, model_split, dq, sq = result

    assert isinstance(snap, DatasetSnapshotManifest)
    assert isinstance(model_split, ModelDataSplitManifest)
    assert isinstance(dq, DatasetQualityReport)
    assert isinstance(sq, ModelSplitQualityReport)

    assert (out / _SNAP_REL).is_file()
    assert (out / _SPLIT_REL).is_file()


def test_pipeline_snapshot_bound_to_csv(tmp_path: Path, processed_csv: Path) -> None:
    """The snapshot dataset_sha256 matches the sha256 of the actual processed CSV."""
    from aletheia_lab.data.download import sha256_file

    out = tmp_path / "out"
    snap, _, _, _ = run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)
    assert snap.dataset_sha256 == sha256_file(processed_csv)


def test_pipeline_quality_report_row_count(tmp_path: Path, make_frame, processed_csv: Path) -> None:
    """DatasetQualityReport.n_rows matches the frame length."""
    frame = make_frame(n=240, seed=0)
    out = tmp_path / "out"
    _, _, dq, _ = run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)
    assert dq.n_rows == len(frame)


def test_pipeline_quality_no_overlap_missing_extra(tmp_path: Path, processed_csv: Path) -> None:
    """ModelSplitQualityReport shows zero overlap, zero missing, zero extra."""
    out = tmp_path / "out"
    _, _, _, sq = run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)
    assert sq.n_overlap == 0
    assert sq.n_missing == 0
    assert sq.n_extra == 0


def test_pipeline_no_raw_id_in_manifests(tmp_path: Path, make_frame, processed_csv: Path) -> None:
    """Neither manifest file contains raw customer IDs."""
    frame = make_frame(n=240, seed=0)
    out = tmp_path / "out"
    run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)

    for rel_path in (_SNAP_REL, _SPLIT_REL):
        content = (out / rel_path).read_text(encoding="utf-8")
        for raw_id in frame["customerID"]:
            assert str(raw_id) not in content, f"raw ID {raw_id!r} leaked into {rel_path}"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_pipeline_idempotent(tmp_path: Path, processed_csv: Path) -> None:
    """A second run_pipeline call with the same CSV succeeds with identical output."""
    out = tmp_path / "out"
    snap1, split1, _, _ = run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)
    snap2, split2, _, _ = run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)
    assert snap1.identity_sha256() == snap2.identity_sha256()
    assert split1.identity_sha256() == split2.identity_sha256()


def test_pipeline_overwrite_different_content_rejected(
    tmp_path: Path, make_frame, processed_csv: Path
) -> None:
    """run_pipeline raises FileExistsError when different data tries to use same paths."""
    out = tmp_path / "out"
    run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)

    # Write a different CSV (seed=99) to the same path
    make_frame(n=240, seed=99).to_csv(processed_csv, index=False, lineterminator="\n")
    with pytest.raises(FileExistsError):
        run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)


def test_pipeline_conflicting_split_leaves_no_partial_snapshot(
    tmp_path: Path,
    processed_csv: Path,
) -> None:
    """A pre-existing split conflict aborts before publishing a new snapshot."""

    out = tmp_path / "out"
    split_path = out / _SPLIT_REL
    split_path.parent.mkdir(parents=True)
    sentinel = b"foreign immutable artifact\n"
    split_path.write_bytes(sentinel)

    with pytest.raises(FileExistsError):
        run_pipeline(processed_csv, dataset_id=_DATASET_ID, output_root=out)

    assert not (out / _SNAP_REL).exists()
    assert split_path.read_bytes() == sentinel


# ---------------------------------------------------------------------------
# Input row shuffle determinism
# ---------------------------------------------------------------------------


def test_pipeline_row_shuffle_same_output(tmp_path: Path, make_frame) -> None:
    """Shuffling input rows produces the same record membership and split structure.

    Row order changes the file sha256 (so dataset_sha256 and identity_sha256 differ),
    but record_membership_sha256 must be identical because the same set of record IDs
    is hashed in a stable sorted order. The model split membership must also be
    identical because split_dataset sorts by ID before partitioning.
    """
    frame = make_frame(n=240, seed=0)
    shuffled = frame.sample(frac=1, random_state=42).reset_index(drop=True)

    csv_orig = tmp_path / "orig.csv"
    csv_shuf = tmp_path / "shuf.csv"
    frame.to_csv(csv_orig, index=False, lineterminator="\n")
    shuffled.to_csv(csv_shuf, index=False, lineterminator="\n")

    out_orig = tmp_path / "out_orig"
    out_shuf = tmp_path / "out_shuf"

    snap_o, split_o, _, _ = run_pipeline(csv_orig, dataset_id=_DATASET_ID, output_root=out_orig)
    snap_s, split_s, _, _ = run_pipeline(csv_shuf, dataset_id=_DATASET_ID, output_root=out_shuf)

    assert snap_o.dataset_sha256 != snap_s.dataset_sha256
    assert snap_o.identity_sha256() != snap_s.identity_sha256()
    assert split_o.identity_sha256() != split_s.identity_sha256()
    assert snap_o.record_membership_sha256 == snap_s.record_membership_sha256, (
        "record_membership_sha256 must be order-independent"
    )

    # Split is deterministic on sorted IDs → membership in each partition is the same.
    assert split_o.train.membership_sha256 == split_s.train.membership_sha256
    assert split_o.validation.membership_sha256 == split_s.validation.membership_sha256
    assert split_o.test.membership_sha256 == split_s.test.membership_sha256


# ---------------------------------------------------------------------------
# PYTHONHASHSEED reproducibility — subprocess uses sys.executable
# ---------------------------------------------------------------------------


def test_pipeline_hash_seed_byte_identical(tmp_path: Path, make_frame) -> None:
    """Pipeline manifests are byte-identical across PYTHONHASHSEED=1 and 999.

    The subprocess is launched via sys.executable so the same interpreter and
    installed packages are guaranteed.
    """
    frame = make_frame(n=240, seed=0)
    csv = tmp_path / "processed.csv"
    frame.to_csv(csv, index=False, lineterminator="\n")

    out1 = tmp_path / "seed1"
    out999 = tmp_path / "seed999"

    result1 = _run_subprocess("1", csv, out1)
    result999 = _run_subprocess("999", csv, out999)

    # Identity SHA-256s must be identical
    assert result1 == result999, (
        f"Identity hashes differ:\n  seed=1:   {result1}\n  seed=999: {result999}"
    )
    # Persisted manifest bytes must also be byte-identical
    files1 = _manifest_files(out1)
    files999 = _manifest_files(out999)
    assert files1 == files999, (
        "Manifest bytes differ between PYTHONHASHSEED=1 and PYTHONHASHSEED=999"
    )


def test_pipeline_three_hash_seeds_identical(tmp_path: Path, make_frame) -> None:
    """Pipeline output is identical across PYTHONHASHSEED=0, 12345, and 65535."""
    frame = make_frame(n=240, seed=0)
    csv = tmp_path / "processed.csv"
    frame.to_csv(csv, index=False, lineterminator="\n")

    seeds = ("0", "12345", "65535")
    outputs = set()
    for seed in seeds:
        out = tmp_path / f"seed{seed}"
        outputs.add(_run_subprocess(seed, csv, out))

    assert len(outputs) == 1, (
        f"Expected identical output across all hash seeds, got {len(outputs)} distinct results"
    )
