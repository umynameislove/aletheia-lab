"""Prediction-blind M4 archive, subject/feature-group and split inventory.

No estimator is imported or fitted here. Available identifiers can prevent
observed duplicates from crossing partitions; they cannot establish complete
physical-patient or specimen independence beyond the supplied metadata.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.score_mapping_new_source_protocol import _checked_member_payload
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import SourceSpec
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

PROTOCOL_PATH = "configs/benchmark/model_artifact_binding_new_source_protocol.json"
FROZEN_PROTOCOL_SHA256 = "467b14666cead89b71a4163eafec3e16a8e8a9f2dcf06eda5052a26a744c58ec"
SOURCE_IDS = ("banknote_authentication", "wisconsin_diagnostic_breast_cancer")
EXPOSED_SOURCE_IDS = (
    "uci_default_of_credit_card_clients",
    "uci_online_shoppers_purchasing_intention",
    "magic_gamma",
    "spambase",
    "htru2",
    "rice_cammeo_osmancik",
)


class ArtifactSourceSpec(SourceSpec):
    member: str = Field(pattern=r"^[a-zA-Z0-9_]+\.(txt|data)$")
    source_family: str
    format: Literal["numeric-features-last-target", "private-id-target-numeric-features"]


def load_m4_protocol(root: Path) -> dict[str, Any]:
    path = root / PROTOCOL_PATH
    if path.is_symlink() or not path.is_file() or file_sha256(path) != FROZEN_PROTOCOL_SHA256:
        raise ValueError("prospective M4 protocol differs from its frozen bytes")
    protocol: Any = json.loads(path.read_bytes())
    if (
        not isinstance(protocol, dict)
        or protocol.get("schema_version") != "model-artifact-binding-new-source-protocol/v1"
        or protocol.get("status") != "prospective_design_locked_not_executed"
    ):
        raise ValueError("M4 prospective identity differs")
    if (
        tuple(s["dataset_id"] for s in protocol["sources"]) != SOURCE_IDS
        or tuple(protocol["historical_source_exclusions"]) != EXPOSED_SOURCE_IDS
        or len({s["source_family"] for s in protocol["sources"]}) != 2
    ):
        raise ValueError("M4 source provenance or exposure exclusions differ")
    if any(
        protocol.get(key) is not False
        for key in (
            "provider_calls_authorized",
            "final_execution_authorized",
            "scientific_admission_authorized",
        )
    ):
        raise ValueError("inventory cannot authorize execution or admission")
    return protocol


def read_m4_rows(
    directory: Path, spec: ArtifactSourceSpec
) -> tuple[tuple[tuple[float, ...], int, str | None], ...]:
    payload = _checked_member_payload(directory, spec.model_dump())
    parsed = []
    for row in csv.reader(io.StringIO(payload.decode("ascii", errors="strict"))):
        if spec.format == "numeric-features-last-target":
            features, token, subject = row[:-1], row[-1] if row else "", None
        else:
            if len(row) < 3 or not row[0].isdigit():
                raise ValueError("WDBC private subject ID is missing or invalid")
            subject, token, features = row[0], row[1], row[2:]
        if len(features) != spec.feature_count or token not in spec.target_encoding:
            raise ValueError("M4 source feature schema or target encoding differs")
        values = tuple(0.0 if float(value) == 0 else float(value) for value in features)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("missing or nonfinite features cannot be silently excluded")
        parsed.append((values, spec.target_encoding[token], subject))
    if len(parsed) != spec.row_count:
        raise ValueError("M4 source census differs; no row may be dropped")
    return tuple(parsed)


def component_groups(
    rows: tuple[tuple[tuple[float, ...], int, str | None], ...],
) -> tuple[str, ...]:
    """Union repeated features and supplied IDs without consulting targets."""

    parents = list(range(len(rows)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owners: dict[str, int] = {}
    tokens_by_row = []
    for index, (features, _, subject) in enumerate(rows):
        tokens = [canonical_sha256({"features": features})]
        if subject is not None:
            tokens.append(canonical_sha256({"subject": subject}))
        tokens_by_row.append(tokens)
        for token in tokens:
            if token in owners:
                parents[find(index)] = find(owners[token])
            else:
                owners[token] = index
    members: dict[int, set[str]] = defaultdict(set)
    for index, tokens in enumerate(tokens_by_row):
        members[find(index)].update(tokens)
    identities = {group: canonical_sha256(sorted(tokens)) for group, tokens in members.items()}
    return tuple(identities[find(index)] for index in range(len(rows)))


def source_inventory(spec: ArtifactSourceSpec, rows: Any, split: dict[str, Any]) -> dict[str, Any]:
    groups = component_groups(rows)
    partitions: dict[str, list[int]] = {name: [] for name in ("train", "calibration", "final")}
    for index, group in enumerate(groups):
        bucket = (
            int(content_sha256(f"{split['seed']}\0{spec.dataset_id}\0{group}".encode()), 16)
            % 10_000
        )
        name = "train" if bucket < 6000 else "calibration" if bucket < 8000 else "final"
        partitions[name].append(index)
    counts: dict[str, dict[str, Any]] = {
        name: {
            "rows": len(indices),
            "class_counts": [sum(rows[i][1] == y for i in indices) for y in (0, 1)],
        }
        for name, indices in partitions.items()
    }
    if any(
        min(c["class_counts"]) < split["minimum_class_count_per_partition"] for c in counts.values()
    ):
        raise ValueError("M4 source partition lacks the fixed minimum binary class support")
    feature_labels: dict[str, set[int]] = defaultdict(set)
    subjects = [subject for _, _, subject in rows if subject is not None]
    for features, target, _ in rows:
        feature_labels[canonical_sha256(features)].add(target)
    return {
        "dataset_id": spec.dataset_id,
        "source_family": spec.source_family,
        "archive_sha256": spec.archive_sha256,
        "member_sha256": spec.member_sha256,
        "row_count": len(rows),
        "feature_count": spec.feature_count,
        "component_count": len(set(groups)),
        "feature_duplicate_rows": len(rows) - len(feature_labels),
        "repeat_subject_rows": len(subjects) - len(set(subjects)),
        "conflicting_target_feature_groups": sum(
            len(labels) > 1 for labels in feature_labels.values()
        ),
        "membership_sha256": canonical_sha256(
            tuple(
                (f"{spec.dataset_id}:{i + 1}", groups[i], name)
                for name, indices in partitions.items()
                for i in indices
            )
        ),
        "ordered_features_sha256": canonical_sha256(tuple(features for features, _, _ in rows)),
        "row_target_sha256": canonical_sha256(
            tuple((f"{spec.dataset_id}:{i + 1}", target) for i, (_, target, _) in enumerate(rows))
        ),
        "partitions": counts,
        "no_observed_group_cross_partition": True,
        "subject_ID_used_as_feature": False,
        "physical_subject_independence_verified": False,
    }


def audit_m4_new_sources(*, root: Path, sources: Path) -> dict[str, Any]:
    protocol = load_m4_protocol(root)
    absolute = sources.absolute()
    if (
        any(p.is_symlink() for p in (absolute, *absolute.parents))
        or not absolute.is_dir()
        or absolute.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("M4 source archives must remain in a private nonsymlink directory")
    inventories = []
    for raw_spec in protocol["sources"]:
        spec = ArtifactSourceSpec.model_validate(raw_spec)
        inventories.append(source_inventory(spec, read_m4_rows(absolute, spec), protocol["split"]))
    return {
        "schema_version": "model-artifact-binding-new-source-inventory/v1",
        "status": "prediction_blind_source_inventory_pass",
        "protocol_sha256": file_sha256(root / PROTOCOL_PATH),
        "source_cluster_count": 2,
        "cell_count": 2,
        "row_count": sum(s["row_count"] for s in inventories),
        "sources": inventories,
        "model_fitted": False,
        "final_predictions_or_metrics_computed": False,
        "provider_calls": 0,
        "U4_authorized": False,
        "scientific_admission": False,
        "independence_scope": "observed-IDs-features-and-distinct-source-provenance-not-unobserved-subjects",
    }
