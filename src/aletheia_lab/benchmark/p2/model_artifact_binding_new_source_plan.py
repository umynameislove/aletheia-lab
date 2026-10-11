"""Prediction-blind code, runtime and inventory seal for the fixed M4 design."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from aletheia_lab.benchmark.p2 import model_artifact_binding_new_source_protocol as inventory
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_cells import (
    synthetic_reader_preflight,
)
from aletheia_lab.benchmark.p2.score_mapping_new_source_plan import (
    INPUT_CONTRACTS,
    _git_binding,
    runtime_binding,
)
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
    checked_private_directory,
    json_bytes,
    publish_json,
    validate_loaded_code_root,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

SCRIPT_PATH = "scripts/model_artifact_binding_new_source_study.py"
PLAN_FILE = "execution-plan.json"
LEASE_FILE = "execution-lease.json"
RECEIPT_FILE = "execution-receipt.json"


def load_source(directory: Path, spec: Any, split: Any) -> ParsedSource:
    spec = inventory.ArtifactSourceSpec.model_validate(spec)
    rows = inventory.read_m4_rows(directory, spec)
    groups = inventory.component_groups(rows)
    buckets = tuple(
        int(content_sha256(f"{split['seed']}\0{spec.dataset_id}\0{group}".encode()), 16) % 10_000
        for group in groups
    )
    edges = split["bucket_edges"]
    partitions = {
        name: tuple(i for i, bucket in enumerate(buckets) if lower <= bucket < upper)
        for name, lower, upper in zip(
            ("train", "calibration", "final"), edges[:-1], edges[1:], strict=True
        )
    }
    data = ParsedSource(
        spec,
        np.asarray([x for x, _, _ in rows], dtype=np.float64),
        tuple(y for _, y, _ in rows),
        tuple(f"{spec.dataset_id}:{i + 1}" for i in range(len(rows))),
        groups,
        partitions,
    )
    expected = inventory.source_inventory(spec, rows, split)
    if any(data.audit()[key] != expected[key] for key in ("membership_sha256", "partitions")):
        raise ProspectiveBindingError("runner membership differs from the original inventory")
    return data


def build_plan(root: Path, directory: Path) -> tuple[dict[str, Any], list[ParsedSource]]:
    root = root.resolve(strict=True)
    validate_loaded_code_root(root)
    directory = checked_private_directory(root, directory)
    protocol = inventory.load_m4_protocol(root)
    observed = inventory.audit_m4_new_sources(root=root, sources=directory / "sources")
    recorded = directory / "inventory.json"
    if (
        recorded.is_symlink()
        or not recorded.is_file()
        or json.loads(recorded.read_bytes()) != observed
    ):
        raise ProspectiveBindingError("the original prediction-blind inventory differs")
    paths = sorted(root.glob("src/**/*.py")) + [
        root / name
        for name in (SCRIPT_PATH, inventory.PROTOCOL_PATH, "pyproject.toml", *INPUT_CONTRACTS)
    ]
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ProspectiveBindingError("bound code/config must be regular repository files")
    sources = [
        load_source(directory / "sources", spec, protocol["split"]) for spec in protocol["sources"]
    ]
    plan = {
        "schema_version": "model-artifact-binding-new-source-execution-plan/v1",
        "protocol_sha256": observed["protocol_sha256"],
        "protocol": protocol,
        "inventory_sha256": file_sha256(recorded),
        "code_sha256": {path.relative_to(root).as_posix(): file_sha256(path) for path in paths},
        "git": _git_binding(root),
        "runtime": runtime_binding(),
        "source_audits": observed["sources"],
        "cell_census": protocol["analysis"]["cell_census"],
        "source_cluster_count": 2,
        "synthetic_reader_preflight": synthetic_reader_preflight(root),
        "model_fitted": False,
        "final_predictions_computed": False,
        "provider_calls": 0,
        "authorization_required": "explicit-U4-owner-authorization-and-exact-plan-hash",
        "mechanism_admitted": False,
    }
    return plan, sources


def preflight_study(root: Path, directory: Path) -> dict[str, Any]:
    plan, _ = build_plan(root, directory)
    return {
        "status": "prediction_blind_preflight_pass",
        "prospective_plan_sha256": content_sha256(json_bytes(plan)),
        "inventory_sha256": plan["inventory_sha256"],
        "protocol_sha256": plan["protocol_sha256"],
        "source_audits": plan["source_audits"],
        "git": plan["git"],
        "cell_count": 2,
        "source_cluster_count": 2,
        "synthetic_reader_preflight": plan["synthetic_reader_preflight"],
        "model_fitted": False,
        "final_predictions_computed": False,
        "provider_calls": 0,
        "execution_authorized": False,
        "mechanism_admitted": False,
    }


def prepare_study(root: Path, directory: Path) -> dict[str, Any]:
    plan, _ = build_plan(root, directory)
    if not plan["git"]["working_tree_clean"]:
        raise ProspectiveBindingError("seal the runner from a committed clean checkout")
    if any(
        (directory / name).exists() or (directory / name).is_symlink()
        for name in (LEASE_FILE, RECEIPT_FILE, "cells")
    ):
        raise ProspectiveBindingError("execution already began; preparation cannot repeat")
    publish_json(directory / PLAN_FILE, plan)
    return {
        "status": "prediction_blind_plan_sealed",
        "plan_sha256": file_sha256(directory / PLAN_FILE),
        "cell_count": 2,
        "source_cluster_count": 2,
        "model_fitted": False,
        "final_predictions_computed": False,
        "provider_calls": 0,
        "execution_authorized": False,
    }


def bound_plan(root: Path, directory: Path) -> tuple[dict[str, Any], list[ParsedSource]]:
    plan, sources = build_plan(root, directory)
    path = directory / PLAN_FILE
    if path.is_symlink() or not path.is_file() or path.read_bytes() != json_bytes(plan):
        raise ProspectiveBindingError(
            "sealed code/runtime/source/membership/input bindings drifted"
        )
    if not plan["git"]["working_tree_clean"]:
        raise ProspectiveBindingError("execution and replay require the sealed clean checkout")
    return plan, sources
