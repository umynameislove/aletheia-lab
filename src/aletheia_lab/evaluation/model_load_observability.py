"""Development census and independent byte/schedule replay for load observability.

Reference truth is reconstructed from control snapshots and raw loader buffers,
not the tested receipts or monitor. Replay never loads a retained pickle.
"""

from __future__ import annotations

import json
import platform
import tempfile
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Observation,
    Scope,
    completion_monitor,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_runtime import (
    Workflow,
    _decode_record,
    create_artifacts,
    run_episode,
    schedules,
)
from aletheia_lab.project.identity import content_sha256

WORKFLOWS: tuple[Workflow, ...] = ("sqlite_queue", "file_cache")
CODE_PATHS = (
    "src/aletheia_lab/evaluation/model_load_contract.py",
    "src/aletheia_lab/evaluation/model_load_runtime.py",
    "src/aletheia_lab/evaluation/model_load_observability.py",
    "scripts/model_load_observability.py",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def reference_from_control(row: dict[str, Any], artifacts: dict[str, str]) -> dict[str, Any]:
    """Separate control oracle; no candidate observation/parser/checker consulted."""
    truth = row["truth"]
    buffers = truth["raw_loader_buffers"]
    if not truth["closed"] or len(buffers) != truth["deserializer_invocations"]:
        raise ValueError("control census is incomplete")
    if not buffers:
        return {"verdict": None, "eligibility": "no_new_load", "consumed": []}
    scope = row["scope"]
    contract = truth["policy"]
    inherited = scope["attempt"] > 0 and contract["retry"] == "inherit"
    initially_pinned = scope["attempt"] == 0 and contract["policy"] == "pin_at_acceptance"
    snapshot = (
        truth["accepted_snapshot"] if inherited or initially_pinned else truth["selection_snapshot"]
    )
    expected = artifacts[snapshot[0]]
    consumed = [content_sha256(bytes.fromhex(buffer)) for buffer in buffers]
    violation = len(consumed) > 1 or any(digest != expected for digest in consumed)
    return {
        "verdict": "violation" if violation else "compliant",
        "eligibility": "load",
        "required_digest": expected,
        "required_revision": snapshot[1],
        "consumed": consumed,
    }


def _observation(value: dict[str, Any]) -> Observation:
    contract = value["contract"]
    if contract is not None:
        contract = {**contract, "artifact_domain": tuple(contract["artifact_domain"])}
    return Observation(
        LoadContract(**contract) if contract is not None else None,
        Scope(**value["scope"]),
        tuple(_decode_record(dict(record)) for record in value["records"]),
    )


def _decisions(row: dict[str, Any]) -> dict[str, Any]:
    return {
        view: {
            "S": asdict(receipt_checker(_observation(value))),
            "T": asdict(completion_monitor(_observation(value))),
        }
        for view, value in row["observations"].items()
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = sum(row["reference"]["eligibility"] == "load" for row in rows)
    outputs: dict[str, Any] = {}
    for view in ("native", "before", "after"):
        outputs[view] = {}
        for method in ("S", "T"):
            decisions = [(row["decisions"][view][method], row["reference"]) for row in rows]
            outputs[view][method] = _counts(decisions, eligible)
    return {
        "episode_count": len(rows),
        "workflow_count": len({row["workflow"] for row in rows}),
        "eligible_load_attempts": eligible,
        "no_new_load_attempts": len(rows) - eligible,
        "reference_status_counts": dict(
            Counter(row["reference"]["verdict"] or "no_new_load" for row in rows)
        ),
        "auxiliary_retry_attempts": sum("predecessor_attempt" in row["truth"] for row in rows),
        "cache_warmup_loads": sum(row["truth"]["cache_origin_digest"] is not None for row in rows),
        "comparisons": outputs,
        "same_evidence_S_T_disagreements": sum(
            (pair["S"]["verdict"], pair["S"]["eligibility"])
            != (pair["T"]["verdict"], pair["T"]["eligibility"])
            for row in rows
            for pair in row["decisions"].values()
        ),
        "transport": dict(sum((Counter(row["transport"]) for row in rows), Counter())),
    }


def _counts(
    decisions: list[tuple[dict[str, Any], dict[str, Any]]], eligible: int
) -> dict[str, Any]:
    false_compliance = false_violation = warranted = 0
    for result, reference in decisions:
        if result["verdict"] == "compliant":
            false_compliance += reference["verdict"] != "compliant"
        if result["verdict"] == "violation":
            false_violation += reference["verdict"] != "violation"
        warranted += (
            result["verdict"] in {"compliant", "violation"}
            and result["verdict"] == reference["verdict"]
        )
    return {
        "verdict_counts": dict(
            Counter(result["verdict"] or "no_new_load" for result, _ in decisions)
        ),
        "false_compliance": false_compliance,
        "false_violation": false_violation,
        "correct_identified_load_attempts": warranted,
        "warranted_coverage": warranted / eligible if eligible else None,
        "correct_no_new_load": sum(
            result["eligibility"] == reference["eligibility"] == "no_new_load"
            for result, reference in decisions
        ),
    }


def _disposition(summary: dict[str, Any]) -> str:
    if summary["same_evidence_S_T_disagreements"]:
        return "implementation_or_model_gap"
    if any(
        view[method][key]
        for view in summary["comparisons"].values()
        for method in ("S", "T")
        for key in ("false_compliance", "false_violation")
    ):
        return "implementation_or_reference_gap"
    # No novel method/frontier follows from simple receipts on controlled sources.
    return "narrow_controlled_feasibility_no_new_method_evidence"


def run_development(root: Path) -> dict[str, Any]:
    """Execute the complete two-workflow/12-episode exploratory census locally."""
    code_before = {path: file_sha256(root / path) for path in CODE_PATHS}
    rows = []
    with tempfile.TemporaryDirectory(prefix="aletheia-load-development-") as directory:
        workspace = Path(directory)
        artifacts = create_artifacts(workspace)
        identities = {name: artifact.digest for name, artifact in artifacts.items()}
        for workflow in WORKFLOWS:
            for episode in schedules(workflow):
                case = workspace / f"{workflow}-{episode.name}"
                case.mkdir()
                row = run_episode(case, workflow, episode, artifacts)
                row["observations"] = {
                    view: asdict(value) for view, value in row["observations"].items()
                }
                row["reference"] = reference_from_control(row, identities)
                row["decisions"] = _decisions(row)
                rows.append(row)
        if {name: file_sha256(artifact.path) for name, artifact in artifacts.items()} != identities:
            raise ValueError("locally created model artifacts mutated")
    if {path: file_sha256(root / path) for path in CODE_PATHS} != code_before:
        raise ValueError("executed code changed during the development census")
    summary = summarize(rows)
    report = {
        "schema_version": "model-load-observability-development/v1",
        "disposition": _disposition(summary),
        "code_sha256": code_before,
        "environment": {
            "python": platform.python_version(),
            "sqlite": __import__("sqlite3").sqlite_version,
            "joblib": version("joblib"),
            "scikit_learn": version("scikit-learn"),
        },
        "artifact_sha256": identities,
        "rows": rows,
        "summary": summary,
        "limits": "authored controls on two local implementations; trusted host; single-file loader input only; no deployed incident population, general runtime proof, LLM gain or measured capture-cost frontier",
        "provider_calls": 0,
        "historical_artifacts_read": False,
    }
    report["report_sha256"] = content_sha256(_canonical(report))
    verify_report(report, root=root)
    return report


def verify_report(report: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Independent schedule/byte replay and exact monitor recomputation; no model load."""
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    if report.get("schema_version") != "model-load-observability-development/v1":
        raise ValueError("unsupported report schema")
    if content_sha256(_canonical(unsigned)) != report.get("report_sha256"):
        raise ValueError("report identity mismatch")
    actual_code = {path: file_sha256(root / path) for path in CODE_PATHS}
    if actual_code != report["code_sha256"]:
        raise ValueError("report is bound to different executed code")
    expected = {
        (workflow, episode.name) for workflow in WORKFLOWS for episode in schedules(workflow)
    }
    rows = report["rows"]
    if (
        len(rows) != len(expected)
        or {(row["workflow"], row["episode"]) for row in rows} != expected
    ):
        raise ValueError("development census is not complete and unique")
    for row in rows:
        _verify_schedule(row, report["artifact_sha256"])
        if reference_from_control(row, report["artifact_sha256"]) != row["reference"]:
            raise ValueError("independent control reference does not reproduce")
        if _decisions(row) != row["decisions"]:
            raise ValueError("observed-evidence decisions do not reproduce")
    summary = summarize(rows)
    if summary != report["summary"] or _disposition(summary) != report["disposition"]:
        raise ValueError("aggregate/disposition does not reproduce")
    return {"verification": "pass", "report_sha256": report["report_sha256"], **summary}


def _verify_schedule(row: dict[str, Any], identities: dict[str, str]) -> None:
    """Replay fixed policy/scope bindings as well as the retained bytes."""
    episode = next(item for item in schedules(row["workflow"]) if item.name == row["episode"])
    scope = Scope(f"{row['workflow']}:{episode.name}", int(episode.second_attempt))
    contract = LoadContract(episode.policy, tuple(identities.values()), episode.retry)
    if row["scope"] != asdict(scope) or _canonical(row["truth"]["policy"]) != _canonical(
        asdict(contract)
    ):
        raise ValueError("control scope/policy differs from the fixed schedule")
    if set(row["observations"]) != {"native", "before", "after"}:
        raise ValueError("observation census differs from the fixed schedule")
    for value in row["observations"].values():
        observation = _observation(value)
        if observation.scope != scope or observation.contract != contract:
            raise ValueError("monitor input differs from the fixed control contract")
    if row["truth"]["accepted_snapshot"] != ["A", 0]:
        raise ValueError("control acceptance snapshot changed")
    expected_selection = None if episode.cached else ["A", 0] if episode.overlap else ["B", 1]
    if row["truth"]["selection_snapshot"] != expected_selection:
        raise ValueError("actual selection is inconsistent with the runtime schedule")
    if ("predecessor_attempt" in row["truth"]) != episode.second_attempt:
        raise ValueError("retry control census changed")
    if (row["truth"]["cache_origin_digest"] is not None) != episode.cached:
        raise ValueError("cache control census changed")
