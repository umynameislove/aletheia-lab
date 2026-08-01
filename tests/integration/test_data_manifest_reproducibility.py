"""Cross-process reproducibility for all three manifest contracts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = r"""
import json
import sys
from pathlib import Path

from aletheia_lab.benchmark.p2.contracts import FamilyCensus, FamilyCensusEntry
from aletheia_lab.data.manifest import (
    BenchmarkFamilySplitManifest,
    DatasetColumn,
    DatasetSnapshotManifest,
    FamilySplitAssignment,
    FamilySplitCounts,
    ModelDataSplitManifest,
    RecordSplit,
    SplitRatios,
    family_census_sha256,
    manifest_identity_sha256,
    record_inventory,
    record_membership_sha256,
    write_manifest,
)

root = Path(sys.argv[1])
record_ids = tuple(
    f"record-{index:04d}"
    for index in [*range(0, 20, 2), *range(1, 20, 2)]
)
inventory = record_inventory(record_ids)
columns = (
    DatasetColumn(name="customerID", logical_type="string", role="identifier", nullable=False),
    DatasetColumn(name="MonthlyCharges", logical_type="float", role="numeric_feature", nullable=False),
    DatasetColumn(name="Churn", logical_type="category", role="target", nullable=False),
)
dataset = DatasetSnapshotManifest(
    dataset_id="telco_customer_churn",
    source_uri=(
        "https://raw.githubusercontent.com/IBM/"
        "telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv"
    ),
    source_version="upstream-master@16320c9c",
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

def partition(name, ids, positives):
    hashes = record_inventory(ids)
    return RecordSplit(
        name=name,
        record_id_hashes=hashes,
        membership_sha256=record_membership_sha256(hashes),
        n_records=len(hashes),
        n_positive=positives,
        n_negative=len(hashes) - positives,
        positive_rate=positives / len(hashes),
    )

split = ModelDataSplitManifest(
    dataset_id=dataset.dataset_id,
    dataset_sha256=dataset.dataset_sha256,
    dataset_identity_sha256=dataset.identity_sha256(),
    n_rows=20,
    seed=42,
    stratified=True,
    ratios=SplitRatios(),
    id_column="customerID",
    target_column="Churn",
    train=partition("train", record_ids[:14], 5),
    validation=partition("validation", record_ids[14:17], 1),
    test=partition("test", record_ids[17:], 1),
)

census_entries = []
for index, digit in enumerate("abcd", start=1):
    fingerprint = digit * 64
    census_entries.append(
        FamilyCensusEntry(
            case_family_id=f"p2-family-{fingerprint}",
            candidate_id=f"p2-candidate-{index:064x}",
            fault_type="data_drift",
            family_class="stable_control",
            proposed_family_sha256=fingerprint,
        )
    )
census = FamilyCensus(schema_version="p2-family-census/1", entries=tuple(census_entries))
split_names = ("dev", "main", "human_audit", "organic_validity")
assignments = tuple(
    sorted(
        (
            FamilySplitAssignment(case_family_id=entry.case_family_id, split=split_names[index])
            for index, entry in enumerate(census.entries)
        ),
        key=lambda item: item.case_family_id,
    )
)
family_split = BenchmarkFamilySplitManifest(
    family_census_sha256=family_census_sha256(census),
    n_families=4,
    assignments=assignments,
    split_counts=FamilySplitCounts(dev=1, main=1, human_audit=1, organic_validity=1),
)

write_manifest(dataset, output_root=root, relative_path="dataset.json")
write_manifest(split, output_root=root, relative_path="model-split.json")
write_manifest(family_split, output_root=root, relative_path="family-split.json")
sys.stdout.write(
    json.dumps(
        {
            "dataset": manifest_identity_sha256(dataset),
            "model_split": manifest_identity_sha256(split),
            "family_split": manifest_identity_sha256(family_split),
        },
        sort_keys=True,
    )
)
"""


def _run(seed: str, output: Path) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = str(repo_root / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(output)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_all_manifests_are_byte_identical_across_hash_seeds(tmp_path: Path) -> None:
    first = tmp_path / "seed-1"
    second = tmp_path / "seed-999"
    assert _run("1", first) == _run("999", second)
    assert _files(first) == _files(second)


def test_three_independent_processes_produce_one_identity_set(tmp_path: Path) -> None:
    outputs = {_run(seed, tmp_path / f"seed-{seed}") for seed in ("0", "12345", "65535")}
    assert len(outputs) == 1
