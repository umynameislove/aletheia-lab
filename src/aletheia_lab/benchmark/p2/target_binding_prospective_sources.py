"""Pinned new sources and outcome-blind membership for target-binding validation."""

from __future__ import annotations

import csv
import io
import json
import platform
import re
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal
from zipfile import ZipFile

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import content_sha256

PROTOCOL_PATH = "configs/benchmark/target_binding_prospective_protocol.json"
SCRIPT_PATH = "scripts/target_binding_prospective.py"
PARTITIONS = ("train", "calibration", "final")


class ProspectiveBindingError(ValueError):
    """A frozen source, membership, or execution binding disagrees."""


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    archive_filename: str = Field(pattern=r"^[a-z][a-z0-9_-]*\.zip$")
    url: str = Field(pattern=r"^https://archive\.ics\.uci\.edu/static/public/")
    doi: str
    license: Literal["CC-BY-4.0"]
    archive_bytes: int = Field(gt=0, le=4_000_000)
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    member: str = Field(pattern=r"^[a-zA-Z0-9_-]+\.data$")
    member_bytes: int = Field(gt=0, le=4_000_000)
    member_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=2, le=50_000)
    feature_count: int = Field(ge=1, le=100)
    target_encoding: dict[str, int]

    @field_validator("target_encoding")
    @classmethod
    def binary_encoding(cls, value: dict[str, int]) -> dict[str, int]:
        if len(value) != 2 or set(value.values()) != {0, 1}:
            raise ValueError("exactly two source label tokens are required")
        return value


class ProspectiveProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["target-binding-prospective-protocol/v1"]
    sources: tuple[SourceSpec, SourceSpec]
    models: tuple[Literal["logistic_regression"], Literal["hist_gradient_boosting"]]
    split_seed: Literal["target-binding-prospective-v1"]
    split_policy: Literal["feature-duplicate-group-hash-60-20-20"]
    preprocessing: Literal["train-only-standard-scaler-no-imputation"]
    calibration: Literal["separate-calibration-partition-logit-clip1e-12-iter200-tol1e-9"]
    calibration_application_clip: float
    dose: Literal[1]
    target_selector: Literal["target-binding-development-v1/20-shards/cyclic-donor"]
    mapping_selector: Literal["m5-dev-v1-2026-09-25/20-shards"]
    matcher: Literal["existing-greedy-opposite-label-swaps-footprint-bounded"]
    visible_metric_decimals: Literal[6]
    maximum_absolute_loss_gap: float
    metric: Literal["reference-prior-standardized-log-loss"]
    failure_policy: Literal["retain-all-four-cells-no-source-model-dose-or-tolerance-substitution"]
    retry_policy: Literal["none-after-lease"]
    claim_scope: Literal[
        "finite-source-algorithmic-control-not-llm-efficacy-or-global-zero-leakage"
    ]

    @field_validator("calibration_application_clip", "maximum_absolute_loss_gap")
    @classmethod
    def fixed_precision(cls, value: float, info: ValidationInfo) -> float:
        expected = {"calibration_application_clip": 1e-15, "maximum_absolute_loss_gap": 5e-7}
        if value != expected.get(info.field_name or ""):
            raise ValueError("calibration application and matcher precision are fixed")
        return value

    @model_validator(mode="after")
    def distinct_new_sources(self) -> ProspectiveProtocol:
        ids = {source.dataset_id for source in self.sources}
        archives = {source.archive_sha256 for source in self.sources}
        names = {source.archive_filename for source in self.sources}
        if len(ids) != 2 or len(archives) != 2 or len(names) != 2:
            raise ValueError("two distinct source clusters and archives are required")
        if any(re.search("credit|shopper", value) for value in ids):
            raise ValueError("historical development sources are not new source clusters")
        return self


@dataclass(frozen=True)
class ParsedSource:
    spec: SourceSpec
    features: NDArray[np.float64]
    targets: tuple[int, ...]
    record_ids: tuple[str, ...]
    group_ids: tuple[str, ...]
    partitions: dict[str, tuple[int, ...]]

    def audit(self) -> dict[str, Any]:
        membership = tuple(
            (self.record_ids[index], self.group_ids[index], partition)
            for partition in PARTITIONS
            for index in self.partitions[partition]
        )
        return {
            "dataset_id": self.spec.dataset_id,
            "row_count": len(self.record_ids),
            "duplicate_feature_rows": len(self.record_ids) - len(set(self.group_ids)),
            "membership_sha256": canonical_sha256(membership),
            "partitions": {
                name: {
                    "rows": len(indices),
                    "class_counts": [
                        sum(self.targets[index] == label for index in indices) for label in (0, 1)
                    ],
                }
                for name, indices in self.partitions.items()
            },
        }


def _member_bytes(path: Path, spec: SourceSpec) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ProspectiveBindingError("source archive must be a regular nonsymlink file")
    if path.stat().st_size != spec.archive_bytes or file_sha256(path) != spec.archive_sha256:
        raise ProspectiveBindingError("source archive byte binding differs")
    with ZipFile(path) as archive:
        selected = [item for item in archive.infolist() if item.filename == spec.member]
        if len(selected) != 1:
            raise ProspectiveBindingError("source member is missing or duplicated")
        member = selected[0]
        if member.file_size != spec.member_bytes or member.flag_bits & 1:
            raise ProspectiveBindingError("source member size or encryption differs")
        payload = archive.read(member)  # Read the pinned member; never extract archive paths.
    if content_sha256(payload) != spec.member_sha256 or file_sha256(path) != spec.archive_sha256:
        raise ProspectiveBindingError("source member bytes or archive changed")
    return payload


def _numeric_row(row: list[str], spec: SourceSpec) -> tuple[list[float], int]:
    if len(row) != spec.feature_count + 1 or row[-1] not in spec.target_encoding:
        raise ProspectiveBindingError("source row width or label encoding differs")
    values = [float(value) for value in row[:-1]]
    if not np.isfinite(values).all():
        raise ProspectiveBindingError("missing or nonfinite features are not imputed or excluded")
    return [0.0 if value == 0 else value for value in values], spec.target_encoding[row[-1]]


def load_source(sources_dir: Path, spec: SourceSpec, *, seed: str) -> ParsedSource:
    if sources_dir.is_symlink():
        raise ProspectiveBindingError("source directory must not be a symlink")
    payload = _member_bytes(sources_dir / spec.archive_filename, spec)
    rows = list(csv.reader(io.StringIO(payload.decode("ascii", errors="strict"))))
    if len(rows) != spec.row_count:
        raise ProspectiveBindingError("source row census differs; no rows may be dropped")
    parsed = [_numeric_row(row, spec) for row in rows]
    features: NDArray[np.float64] = np.asarray([values for values, _ in parsed], dtype=np.float64)
    targets = tuple(label for _, label in parsed)
    groups = tuple(canonical_sha256(values) for values, _ in parsed)
    # Group membership consults features only, not targets or fitted scores.
    buckets = tuple(
        int(content_sha256(f"{seed}\0{spec.dataset_id}\0{group}".encode()), 16) % 10_000
        for group in groups
    )
    partitions = {
        name: tuple(index for index, bucket in enumerate(buckets) if lower <= bucket < upper)
        for name, lower, upper in (
            ("train", 0, 6000),
            ("calibration", 6000, 8000),
            ("final", 8000, 10000),
        )
    }
    return ParsedSource(
        spec,
        features,
        targets,
        tuple(f"{spec.dataset_id}:{index + 1}" for index in range(len(rows))),
        groups,
        partitions,
    )


def json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def publish_json(path: Path, payload: Any) -> None:
    publish_immutable_file(path, json_bytes(payload))


def checked_private_directory(root: Path, directory: Path) -> Path:
    absolute = directory.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ProspectiveBindingError("private study directory must not traverse symlinks")
    if absolute.resolve().is_relative_to(root.resolve()) or not absolute.is_dir():
        raise ProspectiveBindingError("private study directory must exist outside the repository")
    return absolute


def validate_loaded_code_root(root: Path) -> None:
    import aletheia_lab

    if Path(aletheia_lab.__file__).resolve() != (root / "src/aletheia_lab/__init__.py").resolve():
        raise ProspectiveBindingError(
            "loaded package is not the checkout whose code is being sealed"
        )


def build_plan(root: Path, directory: Path) -> tuple[dict[str, Any], list[ParsedSource]]:
    validate_loaded_code_root(root)
    directory = checked_private_directory(root, directory)
    protocol = ProspectiveProtocol.model_validate_json((root / PROTOCOL_PATH).read_bytes())
    sources = [
        load_source(directory / "sources", spec, seed=protocol.split_seed)
        for spec in protocol.sources
    ]
    paths = sorted(root.glob("src/**/*.py")) + [
        root / SCRIPT_PATH,
        root / PROTOCOL_PATH,
        root / "pyproject.toml",
    ]
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ProspectiveBindingError("bound code must consist of regular repository files")
    plan = {
        "schema_version": "target-binding-prospective-plan/v1",
        "protocol": protocol.model_dump(mode="json"),
        "code_sha256": {path.relative_to(root).as_posix(): file_sha256(path) for path in paths},
        "runtime": {
            "python": platform.python_version(),
            **{
                name: version(name)
                for name in (
                    "numpy",
                    "scipy",
                    "scikit-learn",
                    "pydantic",
                    "joblib",
                    "threadpoolctl",
                )
            },
            "thread_limit": 1,
        },
        "source_audits": [source.audit() for source in sources],
        "cell_census": [
            f"{source.dataset_id}/{kind}" for source in protocol.sources for kind in protocol.models
        ],
        "source_cluster_count": 2,
        "provider_calls": 0,
        "final_predictions_computed": False,
        "existing_results_mutated": False,
    }
    return plan, sources


def prepare_study(root: Path, directory: Path) -> dict[str, Any]:
    plan, _ = build_plan(root, directory)
    if (directory / "lease.json").exists():
        raise ProspectiveBindingError("execution already leased; preparation cannot be repeated")
    publish_json(directory / "plan.json", plan)
    eligible = all(
        all(count > 0 for count in partition["class_counts"])
        for audit in plan["source_audits"]
        for partition in audit["partitions"].values()
    )
    return {
        "status": "outcome_blind_preflight_pass" if eligible else "preflight_ineligible_retained",
        "plan_sha256": file_sha256(directory / "plan.json"),
        "cell_count": len(plan["cell_census"]),
        "source_cluster_count": 2,
        "provider_calls": 0,
        "final_predictions_computed": False,
        "source_audits": plan["source_audits"],
    }
