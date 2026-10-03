"""Prevalidation development on one real SDK workflow, never final validation."""

from __future__ import annotations

import platform
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Scope,
    completion_monitor,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_mlflow import (
    SCHEDULES,
    NativeWorkflow,
    local_sdk,
    native_frame,
)
from aletheia_lab.evaluation.model_load_observability import _observation
from aletheia_lab.evaluation.model_load_provenance import (
    dependency_versions,
    document_digest,
    provenance_checker,
    verify_chain,
)
from aletheia_lab.project.identity import content_sha256

CODE_PATHS = (
    "src/aletheia_lab/evaluation/model_load_contract.py",
    "src/aletheia_lab/evaluation/model_load_runtime.py",
    "src/aletheia_lab/evaluation/model_load_observability.py",
    "src/aletheia_lab/evaluation/model_load_mlflow.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "src/aletheia_lab/evaluation/model_load_provenance_study.py",
    "scripts/model_load_provenance.py",
)


def reference(row: dict[str, Any], artifacts: dict[str, str]) -> dict[str, Any]:
    """Rehash private native-unpickler buffers; no observer/checker is consulted."""
    if row["terminal"] != "completed":
        return {"verdict": "unknown", "eligibility": "intended_load"}
    schedule = row["schedule"]
    if schedule["mode"] == "cache":
        if row["target_buffers"]:
            raise ValueError("cache exclusion unexpectedly invoked the loader")
        return {"verdict": None, "eligibility": "no_new_load"}
    pinned = (
        schedule["retried"]
        and schedule["retry"] == "inherit"
        or (not schedule["retried"] and schedule["policy"] == "pin_at_acceptance")
    )
    snapshot = row["accepted_snapshot"] if pinned else row["load_boundary_snapshot"]
    expected = artifacts[{"1": "A", "2": "B"}[snapshot["version"]]]
    if snapshot["digest"] != expected:
        raise ValueError("SDK version snapshot is inconsistent with generated artifacts")
    consumed = [content_sha256(bytes.fromhex(item["raw_hex"])) for item in row["target_buffers"]]
    if not consumed or any(digest not in artifacts.values() for digest in consumed):
        raise ValueError("unsupported native deserializer census")
    return {
        "verdict": "violation"
        if len(consumed) > 1 or any(d != expected for d in consumed)
        else "compliant",
        "eligibility": "load",
        "required_digest": expected,
        "consumed": consumed,
    }


def _decisions(row: dict[str, Any], directory: Path) -> dict[str, Any]:
    if row["terminal"] != "completed":
        return {
            method: {
                "verdict": "unknown",
                "eligibility": "intended_load",
                "reason": "technical_failure",
            }
            for method in ("S", "T", "P")
        }
    observation = _observation(row["observation"])
    provenance = provenance_checker(observation, directory / "scoped")
    return {
        "S": asdict(receipt_checker(observation)),
        "T": asdict(completion_monitor(observation)),
        "P": {
            **asdict(provenance.decision),
            "verifier_calls": provenance.verifier_calls,
            "artifact_rules_passed": provenance.artifact_rules_passed,
        },
    }


def _path_verification(row: dict[str, Any], directory: Path) -> str:
    """Artifact-chain result only: it is NOT mapped to model-load compliance."""
    if row["terminal"] != "completed" or not row["target_buffers"]:
        return "not_applicable"
    item = row["target_buffers"][0]
    return verify_chain(
        {"model.pkl": item["before_sha256"]},
        {"model.pkl": item["after_sha256"]},
        directory / "path-only",
    )


def critical_pair(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {row["schedule"]["name"]: row for row in rows}
    healthy, race = by_name["version_pin_legal"], by_name["same_path_restored_fault"]
    if healthy["terminal"] != "completed" or race["terminal"] != "completed":
        return {"established": False, "reason": "technical_failure"}
    equal = healthy["native_fact_frame"] == race["native_fact_frame"]
    opposite = {healthy["reference"]["verdict"], race["reference"]["verdict"]} == {
        "compliant",
        "violation",
    }
    return {
        "established": equal and opposite,
        "same_native_fact_frame": equal,
        "opposite_reference_status": opposite,
        "native_frame_sha256": document_digest(healthy["native_fact_frame"]),
        "definition": "SDK version snapshots, URI, registered metadata, MLmodel and pre/post weights hashes",
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row["schedule"]["mode"] != "cache"]
    comparisons: dict[str, Any] = {}
    for method in ("S", "T", "P"):
        decisions = [
            (row["decisions"][method]["verdict"], row["reference"]["verdict"]) for row in eligible
        ]
        identified = sum(
            verdict in {"compliant", "violation"} and verdict == truth
            for verdict, truth in decisions
        )
        comparisons[method] = {
            "correct_identified": identified,
            "denominator": len(eligible),
            "warranted_coverage": identified / len(eligible),
            "false_compliance": sum(v == "compliant" and t != "compliant" for v, t in decisions),
            "false_violation": sum(v == "violation" and t != "violation" for v, t in decisions),
            "verdict_counts": dict(Counter(v for v, _ in decisions)),
        }
    return {
        "episode_count": len(rows),
        "workflow_cluster_count": 1,
        "planned_load_attempts": len(eligible),
        "completed_load_attempts": sum(row["terminal"] == "completed" for row in eligible),
        "cache_only_attempts": len(rows) - len(eligible),
        "technical_failure_count": sum(row["terminal"] != "completed" for row in rows),
        "retained_auxiliary_native_loads": sum(
            len(row.get("auxiliary_buffers", [])) for row in rows
        ),
        "reference_status_counts": dict(
            Counter(row["reference"]["verdict"] or "no_new_load" for row in rows)
        ),
        "comparisons": comparisons,
        "same_evidence_disagreements": sum(
            len(
                {
                    (row["decisions"][m]["verdict"], row["decisions"][m]["eligibility"])
                    for m in ("S", "T", "P")
                }
            )
            != 1
            for row in rows
        ),
        "real_scoped_verifier_calls": sum(
            row["decisions"]["P"].get("verifier_calls", 0) for row in rows
        ),
        "path_only_verification_counts": dict(
            Counter(row["path_only_verification"] for row in rows)
        ),
        "critical_pair": critical_pair(rows),
    }


def disposition(summary: dict[str, Any]) -> str:
    if summary["technical_failure_count"]:
        return "incomplete_development_census"
    if summary["same_evidence_disagreements"] or any(
        item["false_compliance"] or item["false_violation"]
        for item in summary["comparisons"].values()
    ):
        return "baseline_or_contract_gap"
    if summary["critical_pair"]["established"]:
        return "bounded_capture_finding_no_new_checker_advantage"
    return "no_discriminating_capture_finding"


def run_development(root: Path) -> dict[str, Any]:
    packages = dependency_versions()
    code = {path: file_sha256(root / path) for path in CODE_PATHS}
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="model-load-provenance-") as temp:
        workspace = Path(temp).resolve()
        with local_sdk(workspace) as sdk:
            workflow = NativeWorkflow(workspace, sdk)
            for schedule in SCHEDULES:
                try:
                    row = workflow.run(schedule)
                    row["reference"] = reference(row, workflow.digests)
                    row["decisions"] = _decisions(row, workspace / schedule.name)
                    row["path_only_verification"] = _path_verification(
                        row, workspace / schedule.name
                    )
                # Third-party SDK errors have several Exception base classes.
                # Preserve each planned slot, without swallowing user interrupts.
                except Exception as exc:
                    row = {
                        "schedule": asdict(schedule),
                        "terminal": "technical_failure",
                        "error_type": type(exc).__name__,
                    }
                    row["reference"] = reference(row, workflow.digests)
                    row["decisions"] = _decisions(row, workspace / schedule.name)
                    row["path_only_verification"] = _path_verification(
                        row, workspace / schedule.name
                    )
                rows.append(row)
            artifacts = workflow.digests
    if code != {path: file_sha256(root / path) for path in CODE_PATHS}:
        raise ValueError("code changed during development execution")
    summary = summarize(rows)
    report = {
        "schema_version": "model-load-provenance-development/v1",
        "study_role": "prevalidation_development_not_held_out",
        "artifact_sha256": artifacts,
        "rows": rows,
        "summary": summary,
        "code_sha256": code,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "packages": packages,
        },
        "disposition": disposition(summary),
        "validation_locked": False,
        "provider_calls": 0,
        "historical_artifacts_read": False,
    }
    report["report_sha256"] = document_digest(report)
    return report


def verify_report(report: dict[str, Any], root: Path) -> dict[str, Any]:
    _verify_header(report, root)
    rows = report["rows"]
    with tempfile.TemporaryDirectory(prefix="provenance-replay-") as temp:
        workspace = Path(temp)
        for index, row in enumerate(rows):
            validate_capture(row, report["artifact_sha256"])
            if reference(row, report["artifact_sha256"]) != row["reference"]:
                raise ValueError("control reference changed")
            directory = workspace / str(index)
            directory.mkdir()
            if _decisions(row, directory) != row["decisions"]:
                raise ValueError("scoped verification changed")
            if _path_verification(row, directory) != row["path_only_verification"]:
                raise ValueError("native path verification changed")
    summary = summarize(rows)
    if summary != report["summary"] or disposition(summary) != report["disposition"]:
        raise ValueError("aggregate or interpretation changed")
    return {
        "verification": "pass",
        "report_sha256": report["report_sha256"],
        "disposition": report["disposition"],
        "validation_locked": False,
        **summary,
    }


def _verify_header(report: dict[str, Any], root: Path) -> None:
    dependency_versions()
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    if document_digest(unsigned) != report.get("report_sha256"):
        raise ValueError("report identity changed")
    if report["schema_version"] != "model-load-provenance-development/v1":
        raise ValueError("unsupported report")
    if report["code_sha256"] != {path: file_sha256(root / path) for path in CODE_PATHS}:
        raise ValueError("code identities changed")
    if (
        report["study_role"] != "prevalidation_development_not_held_out"
        or report["validation_locked"]
    ):
        raise ValueError("development cannot be relabeled validation")
    if report["provider_calls"] != 0 or report["historical_artifacts_read"]:
        raise ValueError("execution scope changed")
    rows = report["rows"]
    if report["environment"]["packages"] != dependency_versions():
        raise ValueError("unsupported recorded dependency versions")
    if (
        set(report["artifact_sha256"]) != {"A", "B"}
        or len(set(report["artifact_sha256"].values())) != 2
    ):
        raise ValueError("generated artifact domain changed")
    if [row["schedule"] for row in rows] != [asdict(schedule) for schedule in SCHEDULES]:
        raise ValueError("fixed development census changed")


def validate_capture(row: dict[str, Any], artifacts: dict[str, str]) -> None:
    """Check recorded control/capture bindings before recomputing any verdict.

    This is consistency replay of a trusted local producer, not authentication
    of an arbitrary self-rehashed report or fresh execution of retained pickle.
    """
    if row["terminal"] == "technical_failure":
        if set(row) != {
            "schedule",
            "terminal",
            "error_type",
            "reference",
            "decisions",
            "path_only_verification",
        }:
            raise ValueError("failed slot contains unverified successful capture")
        return
    if row["terminal"] != "completed":
        raise ValueError("unsupported terminal state")
    schedule = row["schedule"]
    scope = Scope(schedule["name"], int(schedule["retried"]))
    observation = _observation(row["observation"])
    contract = LoadContract(schedule["policy"], tuple(artifacts.values()), schedule["retry"])
    if (
        row["scope"] != asdict(scope)
        or observation.scope != scope
        or observation.contract != contract
    ):
        raise ValueError("observer scope or policy changed")
    for key, name, version in (
        ("accepted_snapshot", "A", "1"),
        ("load_boundary_snapshot", "B", "2"),
    ):
        if row[key] != {
            "model_name": "model-load-development",
            "version": version,
            "source": f"source-{name}",
            "digest": artifacts[name],
        }:
            raise ValueError("SDK snapshot binding changed")
    buffers = row["target_buffers"]
    auxiliary = row["auxiliary_buffers"]
    _validate_buffers(schedule, buffers, auxiliary, artifacts)
    if row["native_fact_frame"] != native_frame(
        row["accepted_snapshot"], row["load_boundary_snapshot"], buffers
    ):
        raise ValueError("native metadata frame changed")
    _validate_observer(row, observation, artifacts)


def _validate_buffers(
    schedule: dict[str, Any],
    buffers: list[dict[str, Any]],
    auxiliary: list[dict[str, Any]],
    artifacts: dict[str, str],
) -> None:
    if len(buffers) != int(schedule["mode"] != "cache") or len(auxiliary) != int(
        schedule["retried"] or schedule["mode"] == "cache"
    ):
        raise ValueError("native deserializer count changed")
    loaded = "A" if schedule["mode"] in {"version", "restored_path"} else "B"
    for item in buffers:
        _validate_buffer(
            item,
            artifacts,
            loaded,
            "B" if schedule["mode"] == "restored_path" else loaded,
            schedule["mode"] in {"alias", "drop"},
        )
    for item in auxiliary:
        _validate_buffer(item, artifacts, "A", "A", False)


def _validate_buffer(
    item: dict[str, Any],
    artifacts: dict[str, str],
    path_artifact: str,
    buffer_artifact: str,
    alias: bool,
) -> None:
    version = "1" if path_artifact == "A" else "2"
    uri = "models:/model-load-development" + ("@champion" if alias else f"/{version}")
    metadata = yaml.safe_load(item["registered_model_meta"])
    # Pinned SDK preserves string versions for version URIs, but its alias
    # resolver returns an integer. Accept only the exact native branch format.
    native_version: str | int = int(version) if alias else version
    if metadata != {"model_name": "model-load-development", "model_version": native_version}:
        raise ValueError("native registered metadata changed")
    if (
        item["uri"] != uri
        or item["before_sha256"] != artifacts[path_artifact]
        or item["after_sha256"] != artifacts[path_artifact]
    ):
        raise ValueError("native path or URI changed")
    if content_sha256(item["model_metadata_text"].encode()) != item["model_metadata_sha256"]:
        raise ValueError("native MLmodel hash changed")
    if content_sha256(bytes.fromhex(item["raw_hex"])) != artifacts[buffer_artifact]:
        raise ValueError("native offered buffer changed")


def _validate_observer(row: dict[str, Any], observation: Any, artifacts: dict[str, str]) -> None:
    schedule, scope = row["schedule"], observation.scope
    records = [record for record in observation.records if record.scope == scope]
    selected = [record for record in records if record.kind == "selection"]
    pinned = (
        schedule["retried"]
        and schedule["retry"] == "inherit"
        or (not schedule["retried"] and schedule["policy"] == "pin_at_acceptance")
    )
    version = 1 if pinned else 2
    phase = schedule["retry"] if scope.attempt else schedule["policy"]
    if len(selected) != 1 or (
        selected[0].digest,
        selected[0].revision,
        selected[0].phase,
        selected[0].selection,
    ) != (
        artifacts["A" if pinned else "B"],
        version,
        phase,
        f"{scope.request}:{scope.attempt}:{version}",
    ):
        raise ValueError("authoritative selection record changed")
    root_scope = Scope(scope.request, 0)
    parents = [
        record
        for record in observation.records
        if record.scope == root_scope and record.kind == "selection"
    ]
    if scope.attempt:
        if (
            len(parents) != 1
            or parents[0].digest != artifacts["A"]
            or parents[0].revision != 1
            or parents[0].phase != schedule["policy"]
        ):
            raise ValueError("root selection witness changed")
        if schedule["retry"] == "inherit" and (
            selected[0].parent_scope,
            selected[0].parent_selection,
        ) != (root_scope, parents[0].selection):
            raise ValueError("retry inheritance binding changed")
    loads = [record for record in records if record.kind == "load"]
    expected_loads = 0 if schedule["mode"] in {"cache", "drop"} else 1
    if len(loads) != expected_loads or any(
        record.selection != selected[0].selection for record in loads
    ):
        raise ValueError("buffer evidence count or join changed")
    if loads and loads[0].digest != content_sha256(
        bytes.fromhex(row["target_buffers"][0]["raw_hex"])
    ):
        raise ValueError("observer is not bound to the offered buffer")
    closures = [record.load_count for record in records if record.kind == "closure"]
    if closures != [int(schedule["mode"] != "cache")]:
        raise ValueError("attempt closure changed")
    if row["transport"]["dropped"] != int(schedule["mode"] == "drop"):
        raise ValueError("observer transport disposition changed")
