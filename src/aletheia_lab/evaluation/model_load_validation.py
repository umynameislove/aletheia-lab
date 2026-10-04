"""Seal a prospective design without importing loaders or exposing validation outcomes."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.filesystem import publish_immutable_file

PROTOCOL_PATH = "configs/evaluation/model_load_validation_protocol.json"
PROTOCOL_SHA256 = "62fa6545781b22311aaaa6a84fc6611868e98f92f20e6f16fde07b8601cf64e0"
CODE_PATHS = (
    PROTOCOL_PATH,
    "src/aletheia_lab/evaluation/model_load_validation.py",
    "scripts/model_load_validation.py",
    "src/aletheia_lab/evaluation/model_load_contract.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "src/aletheia_lab/content_hashing.py",
    "src/aletheia_lab/filesystem.py",
)
MAX_PLAN_BYTES = 2_000_000


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("non-finite JSON value")


def _read_document(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError("document must be a bounded regular file")
    result = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_object,
        parse_constant=_constant,
    )
    if not isinstance(result, dict):
        raise ValueError("document must be a JSON object")
    return result


def _no_symlinks(path: Path) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("symlink path not permitted")


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode:
        raise ValueError("repository identity check failed")
    return result.stdout.strip()


def _root(root: Path) -> Path:
    path = root.absolute()
    _no_symlinks(path)
    path = path.resolve()
    if Path(_git(path, "rev-parse", "--show-toplevel")).resolve() != path:
        raise ValueError("root must be the repository checkout")
    return path


def _private(path: Path, root: Path) -> Path:
    destination = path.absolute()
    _no_symlinks(destination)
    destination = destination.resolve()
    if destination.is_relative_to(root):
        raise ValueError("private plan must remain outside the checkout")
    if not destination.parent.is_dir():
        raise ValueError("private plan requires an existing parent directory")
    if any((parent / ".git").exists() for parent in destination.parents):
        raise ValueError("private plan must not be inside another Git checkout")
    return destination


def load_protocol(root: Path) -> dict[str, Any]:
    """The canonical commitment rejects every design change, not only named fields."""
    path = root / PROTOCOL_PATH
    _no_symlinks(path)
    protocol = _read_document(path)
    if document_digest(protocol) != PROTOCOL_SHA256:
        raise ValueError("prospective protocol identity changed")
    return protocol


def census(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand design identities only; no statuses, scores, models or expected verdicts."""
    slots = []
    for backend in protocol["backends"]:
        for schedule in protocol["schedules"]:
            for branch in protocol["branches"]:
                unit = {
                    "backend": backend["id"],
                    "api": backend["api"],
                    "schedule": schedule["id"],
                    "family": schedule["family"],
                    "branch": branch,
                    "planned_target_entries": schedule["target_entries"],
                    "planned_auxiliary_entries": schedule["auxiliary_entries"],
                }
                slots.append({"slot_id": document_digest(unit), **unit})
    return slots


def counts(slots: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "planned_slots": len(slots),
        "loader_format_strata": len({slot["backend"] for slot in slots}),
        "lifecycle_families_per_stratum": len({slot["family"] for slot in slots}),
        "planned_load_slots": sum(slot["planned_target_entries"] > 0 for slot in slots),
        "planned_cache_slots": sum(slot["planned_target_entries"] == 0 for slot in slots),
        "observation_load_slots": sum(
            slot["branch"] == "observation" and slot["planned_target_entries"] > 0 for slot in slots
        ),
        "prevention_opportunities": sum(slot["branch"] == "prevention" for slot in slots),
        "max_target_native_entries": sum(slot["planned_target_entries"] for slot in slots),
        "max_auxiliary_native_entries": sum(slot["planned_auxiliary_entries"] for slot in slots),
    }


def _design(root: Path) -> dict[str, Any]:
    protocol = load_protocol(root)
    slots = census(protocol)
    totals = counts(slots)
    if len({slot["slot_id"] for slot in slots}) != len(slots):
        raise ValueError("duplicate design slot")
    exposed = {(unit["backend"], unit["api"]) for unit in protocol["prior_exposure"]}
    if any((slot["backend"], slot["api"]) in exposed for slot in slots):
        raise ValueError("development-exposed loader API selected")
    maximum = totals["max_target_native_entries"] + totals["max_auxiliary_native_entries"]
    if maximum != protocol["resources"]["max_native_entries_total_including_auxiliary"]:
        raise ValueError("native entry budget and census disagree")
    code_hashes = {}
    for relative in CODE_PATHS:
        path = root / relative
        _no_symlinks(path)
        code_hashes[relative] = file_sha256(path)
    return {
        "schema_version": "model-load-validation-plan/v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol": protocol,
        "code_sha256": code_hashes,
        "census": slots,
        "counts": totals,
        "protocol_locked": True,
        "runner_implemented": False,
        "execution_ready": False,
        "execution_authorized": False,
        "validation_outcomes_observed": False,
        "model_fitted": False,
        "native_loader_entries": 0,
        "provider_calls": 0,
        "execution_seal": None,
    }


def prepare(root: Path, plan_path: Path) -> dict[str, Any]:
    """Publish exactly one new private design lock; existing plans cannot be replaced."""
    root = _root(root)
    path = _private(plan_path, root)
    if path.exists():
        raise FileExistsError("retained plan already exists")
    plan = {**_design(root), "creation_git_head": _git(root, "rev-parse", "HEAD")}
    plan["plan_sha256"] = document_digest(plan)
    payload = (json.dumps(plan, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    # A racing identical creator is safe; never overwrite a non-identical existing lock.
    publish_immutable_file(path, payload)
    return summary(plan, "prediction_blind_validation_design_sealed")


def preflight(root: Path, plan_path: Path) -> dict[str, Any]:
    """Reconstruct the static lock; this is not an SDK or execution compatibility test."""
    root = _root(root)
    path = _private(plan_path, root)
    plan = _read_document(path)
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if plan.get("plan_sha256") != document_digest(unsigned):
        raise ValueError("retained plan digest mismatch")
    creation = plan.get("creation_git_head")
    if not isinstance(creation, str) or not re.fullmatch(r"[0-9a-f]{40}", creation):
        raise ValueError("invalid creation commit identity")
    _git(root, "merge-base", "--is-ancestor", creation, "HEAD")
    expected = {**_design(root), "creation_git_head": creation}
    if document_digest(unsigned) != document_digest(expected):
        raise ValueError("retained plan differs from the complete current design")
    return summary(plan, "offline_validation_design_preflight_pass")


def summary(plan: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "plan_sha256": plan["plan_sha256"],
        "protocol_sha256": plan["protocol_sha256"],
        **plan["counts"],
        "protocol_locked": True,
        "runner_implemented": False,
        "execution_ready": False,
        "execution_authorized": False,
        "validation_outcomes_observed": False,
        "runtime_compatibility_verified": False,
        "native_loader_entries": 0,
        "provider_calls": 0,
    }
