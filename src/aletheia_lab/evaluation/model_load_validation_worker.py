"""Private worker entry points; authorization is checked before any SDK action."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation import model_load_validation as design
from aletheia_lab.evaluation.model_load_validation_adapters import (
    NativeAdapter,
    build_artifacts,
    environment,
    no_network,
)
from aletheia_lab.evaluation.model_load_validation_lifecycle import run_slot
from aletheia_lab.evaluation.model_load_validation_replay import annotate
from aletheia_lab.evaluation.model_load_validation_run import (
    artifact_inventory,
    check_seal,
    code_hashes,
    lease_document,
    publish,
    read_signed,
    require_document,
)
from aletheia_lab.project.identity import content_sha256


def prepare_worker(root: Path, plan: Path, study: Path) -> dict[str, Any]:
    root = design._root(root)
    study = design._private(study, root)
    report = design.preflight(root, plan)
    authority = read_signed(study / "preparation.json", "request_sha256")
    require_document(
        authority,
        {
            "schema_version": "model-load-preparation-authority/v1",
            "plan_sha256": report["plan_sha256"],
            "code_sha256": code_hashes(root),
            "environment": environment(),
            "git_head": design._git(root, "rev-parse", "HEAD"),
            "max_local_fits": 2,
            "native_entries_authorized": 0,
        },
        "request_sha256",
    )
    if design._git(root, "status", "--porcelain"):
        raise ValueError("artifact preparation lacks unchanged scoped authority")
    os.umask(0o077)
    directory = study / "artifacts"
    directory.mkdir(mode=0o700, exist_ok=False)  # Consume before fitting; no retry.
    with no_network():
        artifacts = build_artifacts()
        if set(artifacts) != {"onnxruntime", "skops"}:
            raise ValueError("artifact preparation changed its backend census")
        for backend, pair in artifacts.items():
            if set(pair) != {"A", "B"}:
                raise ValueError("artifact preparation changed its A/B census")
            for name, raw in pair.items():
                if type(raw) is not bytes or not 0 < len(raw) <= 262144:
                    raise ValueError("prepared artifact violates buffer budget")
                from aletheia_lab.filesystem import publish_immutable_file

                if publish_immutable_file(directory / f"{backend}-{name}.buffer", raw) != "created":
                    raise FileExistsError("artifact already exists")
    artifact_inventory(study)
    if code_hashes(root) != authority["code_sha256"] or environment() != authority["environment"]:
        raise ValueError("artifact setup changed its bound code or environment")
    return {
        "status": "artifacts_prepared_without_native_load",
        "local_fits_completed": 2,
        "native_loader_entries": 0,
        "provider_calls": 0,
    }


def slot_worker(root: Path, plan: Path, study: Path, index: int) -> dict[str, Any]:
    root, study, seal = check_seal(root, plan, study)
    lease = read_signed(study / "lease.json", "lease_sha256")
    require_document(
        lease, lease_document(seal["seal_sha256"]), "lease_sha256", "native-execution authority"
    )
    protocol = design.load_protocol(root)
    slots = design.census(protocol)
    if type(index) is not int or not 0 <= index < len(slots):
        raise ValueError("slot index outside fixed census")
    slot = slots[index]
    schedule = next(value for value in protocol["schedules"] if value["id"] == slot["schedule"])
    artifacts = {
        name: (study / "artifacts" / f"{slot['backend']}-{name}.buffer").read_bytes()
        for name in ("A", "B")
    }
    for name, raw in artifacts.items():
        if {"sha256": content_sha256(raw), "size": len(raw)} != seal["artifacts"][slot["backend"]][
            name
        ]:
            raise ValueError("actual offered buffer changed after seal check")
    os.umask(0o077)
    with no_network():
        row = run_slot(
            study / f"work-{index:02d}",
            slot,
            schedule,
            artifacts,
            seal["environment"]["packages"][slot["backend"]],
            NativeAdapter,
        )
        publish(study / f"slot-raw-{index:02d}.json", row)
        digests = {
            name: value["sha256"] for name, value in seal["artifacts"][slot["backend"]].items()
        }
        row = annotate(row, digests, study / f"provenance-{index:02d}")
    if code_hashes(root) != seal["code_sha256"] or environment() != seal["environment"]:
        raise ValueError("execution code or environment changed inside worker")
    publish(study / f"slot-{index:02d}.json", row)
    return {"status": "slot_worker_complete", "slot_id": slot["slot_id"]}
