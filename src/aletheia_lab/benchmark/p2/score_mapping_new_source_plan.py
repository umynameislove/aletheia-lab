"""Prediction-blind bindings for the already frozen, two-source M5 design."""

from __future__ import annotations

import json
import platform
import subprocess
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import Field

from aletheia_lab.benchmark.p2 import score_mapping_new_source_protocol as inventory
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.score_mapping_new_source_wire import synthetic_wire_preflight
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
    SourceSpec,
    checked_private_directory,
    json_bytes,
    publish_json,
    validate_loaded_code_root,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

SCRIPT_PATH = "scripts/score_mapping_new_source_study.py"
PLAN_FILE = "execution-plan.json"
LEASE_FILE = "execution-lease.json"
RECEIPT_FILE = "execution-receipt.json"
MODEL_KINDS = ("logistic_regression", "hist_gradient_boosting")
INPUT_CONTRACTS = (
    "configs/evaluation/diagnosis_main_response_contract.json",
    "configs/evaluation/diagnosis_variant_fairness_freeze.json",
)


class NewSourceSpec(SourceSpec):
    # The existing binary source contract, with the two pinned CSV/ARFF members.
    member: str = Field(pattern=r"^[a-zA-Z0-9_]+\.(csv|arff)$")


def load_new_source(sources: Path, spec: dict[str, Any], seed: str) -> ParsedSource:
    rows = inventory._source_rows(sources, spec)
    groups = tuple(canonical_sha256(features) for features, _ in rows)
    buckets = tuple(
        int(content_sha256(f"{seed}\0{spec['dataset_id']}\0{group}".encode()), 16) % 10_000
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
    source = ParsedSource(
        NewSourceSpec.model_validate({key: spec[key] for key in NewSourceSpec.model_fields}),
        np.asarray([features for features, _ in rows], dtype=np.float64),
        tuple(target for _, target in rows),
        tuple(f"{spec['dataset_id']}:{index + 1}" for index in range(len(rows))),
        groups,
        partitions,
    )
    expected = inventory._audit_source(spec, rows, seed)
    if any(source.audit()[key] != expected[key] for key in ("membership_sha256", "partitions")):
        raise ProspectiveBindingError("runner membership differs from the source inventory")
    return source


def runtime_binding() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        **{
            name: version(name)
            for name in (
                "numpy",
                "scipy",
                "scikit-learn",
                "pydantic",
                "joblib",
                "threadpoolctl",
                "openai",
            )
        },
        "thread_limit": 1,
    }


def _git_binding(root: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    return {"source_commit": commit, "working_tree_clean": not status}


def build_plan(root: Path, directory: Path) -> tuple[dict[str, Any], list[ParsedSource]]:
    root = root.resolve(strict=True)
    validate_loaded_code_root(root)
    directory = checked_private_directory(root, directory)
    observed = inventory.audit_new_source_protocol(root=root, sources=directory / "sources")
    recorded = directory / "inventory.json"
    if recorded.is_symlink() or not recorded.is_file():
        raise ProspectiveBindingError("original prediction-blind inventory is required")
    if json.loads(recorded.read_bytes()) != observed:
        raise ProspectiveBindingError("source inventory changed; no source substitution is allowed")
    protocol = json.loads((root / inventory.PROTOCOL_PATH).read_bytes())
    paths = sorted(root.glob("src/**/*.py")) + [
        root / path
        for path in (
            SCRIPT_PATH,
            inventory.PROTOCOL_PATH,
            "pyproject.toml",
            *INPUT_CONTRACTS,
        )
    ]
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ProspectiveBindingError("bound code/config must consist of regular repository files")
    sources = [
        load_new_source(directory / "sources", spec, protocol["split"]["seed"])
        for spec in protocol["sources"]
    ]
    plan = {
        "schema_version": "score-mapping-new-source-execution-plan/v1",
        "protocol_sha256": observed["protocol_sha256"],
        "protocol": protocol,
        "inventory_sha256": file_sha256(recorded),
        "code_sha256": {path.relative_to(root).as_posix(): file_sha256(path) for path in paths},
        "git": _git_binding(root),
        "runtime": runtime_binding(),
        "source_audits": [source.audit() for source in sources],
        "cell_census": protocol["analysis"]["cell_census"],
        "source_cluster_count": 2,
        "synthetic_wire_preflight": synthetic_wire_preflight(root),
        "provider_calls": 0,
        "final_predictions_computed": False,
        "authorization_required": "explicit-U3-owner-authorization-and-exact-plan-hash",
        "input_channel_scope": "common-frozen-A2-SDK-interface-not-LLM-efficacy",
    }
    return plan, sources


def preflight_study(root: Path, directory: Path) -> dict[str, Any]:
    plan, _ = build_plan(root, directory)
    return {
        "status": "prediction_blind_preflight_pass",
        "prospective_plan_sha256": content_sha256(json_bytes(plan)),
        "source_audits": plan["source_audits"],
        "git": plan["git"],
        "cell_count": 4,
        "source_cluster_count": 2,
        "provider_calls": 0,
        "final_predictions_computed": False,
        "execution_authorized": False,
        "synthetic_wire_preflight": plan["synthetic_wire_preflight"],
    }


def prepare_study(root: Path, directory: Path) -> dict[str, Any]:
    plan, _ = build_plan(root, directory)
    if not plan["git"]["working_tree_clean"]:
        raise ProspectiveBindingError("seal the runner from a committed, clean checkout")
    if (directory / LEASE_FILE).exists() or (directory / "cells").exists():
        raise ProspectiveBindingError("execution already leased; preparation cannot repeat")
    publish_json(directory / PLAN_FILE, plan)
    return {
        "status": "prediction_blind_plan_sealed",
        "plan_sha256": file_sha256(directory / PLAN_FILE),
        "cell_count": 4,
        "source_cluster_count": 2,
        "provider_calls": 0,
        "final_predictions_computed": False,
        "execution_authorized": False,
    }


def bound_plan(root: Path, directory: Path) -> tuple[dict[str, Any], list[ParsedSource]]:
    plan, sources = build_plan(root, directory)
    path = directory / PLAN_FILE
    if path.is_symlink() or not path.is_file() or path.read_bytes() != json_bytes(plan):
        raise ProspectiveBindingError(
            "prepared code, runtime, source, membership or inputs drifted"
        )
    if not plan["git"]["working_tree_clean"]:
        raise ProspectiveBindingError(
            "execution and verification require the sealed clean checkout"
        )
    return plan, sources
