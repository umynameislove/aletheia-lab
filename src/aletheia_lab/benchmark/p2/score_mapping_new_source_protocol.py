"""Prediction-blind source inventory for the prospective M5 protocol.

This module checks public archive bytes, source rows and deterministic split
membership. It does not fit a model, compute a final metric, or authorize U3.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

PROTOCOL_PATH = "configs/benchmark/score_mapping_new_source_protocol.json"
FROZEN_PROTOCOL_SHA256 = "428ee882d1857967511f32deb5336588f4aa0d7c8ca2e26e2d01dfbb5866d10f"
SOURCE_IDS = ("htru2", "rice_cammeo_osmancik")
HISTORICAL_IDS = {
    "uci_default_of_credit_card_clients",
    "uci_online_shoppers_purchasing_intention",
    "magic_gamma",
    "spambase",
}


class NewSourceProtocolError(ValueError):
    """A source archive, parser, split, or scientific decision is inconsistent."""


def _checked_member_payload(directory: Path, spec: dict[str, Any]) -> bytes:
    path = directory / spec["archive_filename"]
    if (
        directory.is_symlink()
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != spec["archive_bytes"]
        or file_sha256(path) != spec["archive_sha256"]
    ):
        raise NewSourceProtocolError("public source archive differs from its frozen bytes")
    with ZipFile(path) as archive:
        matches = [item for item in archive.infolist() if item.filename == spec["member"]]
        if (
            len(matches) != 1
            or matches[0].file_size != spec["member_bytes"]
            or matches[0].flag_bits & 1
        ):
            raise NewSourceProtocolError("source member is missing, duplicated, or encrypted")
        payload = archive.read(matches[0])  # Never extract archive-provided paths.
    if (
        content_sha256(payload) != spec["member_sha256"]
        or file_sha256(path) != spec["archive_sha256"]
    ):
        raise NewSourceProtocolError("source member or archive changed during reading")
    return payload


def _source_rows(
    directory: Path, spec: dict[str, Any]
) -> tuple[tuple[tuple[float, ...], int], ...]:
    text = _checked_member_payload(directory, spec).decode("utf-8", errors="strict")
    if spec["format"] == "csv_no_header_numeric_last_target":
        lines = text.splitlines()
    elif spec["format"] == "arff_numeric_last_target":
        before_data, marker, after_data = text.partition("@DATA")
        attributes = [
            line.strip()
            for line in before_data.splitlines()
            if line.strip().lower().startswith("@attribute ")
        ]
        if (
            not marker
            or len(attributes) != spec["feature_count"] + 1
            or "{Cammeo, Osmancik}" not in attributes[-1]
        ):
            raise NewSourceProtocolError("Rice ARFF schema differs")
        lines = [
            line for line in after_data.splitlines() if line.strip() and not line.startswith("%")
        ]
    else:
        raise NewSourceProtocolError("source format is not frozen")
    rows = []
    for values in csv.reader(lines):
        if len(values) != spec["feature_count"] + 1 or values[-1] not in spec["target_encoding"]:
            raise NewSourceProtocolError("source row shape or target token differs")
        try:
            features = tuple(0.0 if float(value) == 0 else float(value) for value in values[:-1])
        except ValueError as exc:
            raise NewSourceProtocolError("source has a nonnumeric feature") from exc
        if not all(math.isfinite(value) for value in features):
            raise NewSourceProtocolError("source has missing or nonfinite features")
        rows.append((features, spec["target_encoding"][values[-1]]))
    if len(rows) != spec["row_count"]:
        raise NewSourceProtocolError("source row census differs; no row may be dropped")
    return tuple(rows)


def _audit_source(
    spec: dict[str, Any], rows: tuple[tuple[tuple[float, ...], int], ...], seed: str
) -> dict[str, Any]:
    groups: dict[str, set[int]] = defaultdict(set)
    partitions: dict[str, list[tuple[str, str, int]]] = {
        "train": [],
        "calibration": [],
        "final": [],
    }
    for index, (features, target) in enumerate(rows):
        group = canonical_sha256(features)
        groups[group].add(target)
        bucket = int(content_sha256(f"{seed}\0{spec['dataset_id']}\0{group}".encode()), 16) % 10_000
        partition = "train" if bucket < 6000 else "calibration" if bucket < 8000 else "final"
        partitions[partition].append((f"{spec['dataset_id']}:{index + 1}", group, target))
    membership = tuple(
        (record_id, group, partition)
        for partition, records in partitions.items()
        for record_id, group, _ in records
    )
    counts: dict[str, dict[str, Any]] = {}
    for partition, records in partitions.items():
        class_counts = [sum(target == value for _, _, target in records) for value in (0, 1)]
        if not all(count > 0 for count in class_counts):
            raise NewSourceProtocolError("a source partition lacks one of the binary classes")
        counts[partition] = {"rows": len(records), "class_counts": class_counts}
    return {
        "dataset_id": spec["dataset_id"],
        "source_family": spec["source_family"],
        "archive_sha256": spec["archive_sha256"],
        "member_sha256": spec["member_sha256"],
        "row_count": len(rows),
        "feature_count": spec["feature_count"],
        "feature_duplicate_rows": len(rows) - len(groups),
        "conflicting_target_feature_groups": sum(len(targets) > 1 for targets in groups.values()),
        "membership_sha256": canonical_sha256(membership),
        "partitions": counts,
    }


def audit_new_source_protocol(*, root: Path, sources: Path) -> dict[str, Any]:
    """Audit exact public sources and source-level independence, not M5 outcomes."""

    root = root.resolve(strict=True)
    protocol_path = root / PROTOCOL_PATH
    if protocol_path.is_symlink() or not protocol_path.is_file():
        raise NewSourceProtocolError("prospective protocol is missing")
    if file_sha256(protocol_path) != FROZEN_PROTOCOL_SHA256:
        raise NewSourceProtocolError("prospective design differs from its frozen SHA-256")
    protocol: Any = json.loads(protocol_path.read_text(encoding="utf-8"))
    if (
        not isinstance(protocol, dict)
        or protocol.get("schema_version") != "score-mapping-new-source-protocol/v1"
        or protocol.get("status") != "prospective_design_locked_not_executed"
        or tuple(item.get("dataset_id") for item in protocol.get("sources", [])) != SOURCE_IDS
        or len({item.get("source_family") for item in protocol["sources"]}) != 2
        or set(protocol.get("historical_source_exclusions", [])) != HISTORICAL_IDS
        or protocol.get("provider_calls_authorized") is not False
        or protocol.get("final_execution_authorized") is not False
        or protocol.get("scientific_admission_authorized") is not False
    ):
        raise NewSourceProtocolError(
            "protocol identity, source families or safety boundary differs"
        )
    split = protocol["split"]
    if (
        split.get("seed") != "m5-new-source-v1"
        or split.get("policy") != "feature-duplicate-group-hash-60-20-20"
        or split.get("partitions") != ["train", "calibration", "final"]
        or split.get("bucket_edges") != [0, 6000, 8000, 10000]
        or split.get("target_blind_assignment") is not True
        or split.get("no_group_cross_partition") is not True
        or protocol["analysis"].get("cell_census")
        != [f"{source}/{kind}" for source in SOURCE_IDS for kind in protocol["models"]]
        or protocol["analysis"].get("source_family_is_independent_unit") is not True
    ):
        raise NewSourceProtocolError("split or independent cell census differs")
    if sources.is_symlink() or not sources.is_dir() or sources.resolve().is_relative_to(root):
        raise NewSourceProtocolError("source archives must be kept privately outside the repo")
    audits = []
    for spec in protocol["sources"]:
        if (
            spec["license"] != "CC-BY-4.0"
            or set(spec["target_encoding"].values()) != {0, 1}
            or len(spec["target_encoding"]) != 2
            or spec["dataset_id"] in HISTORICAL_IDS
        ):
            raise NewSourceProtocolError(
                "source license, target or independence declaration differs"
            )
        audits.append(_audit_source(spec, _source_rows(sources, spec), split["seed"]))
    return {
        "schema_version": "score-mapping-new-source-inventory/v1",
        "status": "prediction_blind_source_inventory_pass",
        "protocol_sha256": file_sha256(protocol_path),
        "source_cluster_count": 2,
        "cell_count": 4,
        "sources": audits,
        "model_fitted": False,
        "final_predictions_or_metrics_computed": False,
        "provider_calls": 0,
        "u3_authorized": False,
    }
