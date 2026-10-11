"""Hash-only replay and matched evidence comparison for a bounded application slice.

Raw snapshots are private and never deserialized by this module. The operator
pin is a declared research overlay, NOT a native MLServer authorization rule.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast

from aletheia_lab.evaluation.model_load_application import ARMS, prepare_artifacts
from aletheia_lab.evaluation.model_load_provenance import document_digest, verify_chain
from aletheia_lab.project.identity import content_sha256

SOURCE = {
    "application": "SeldonIO/MLServer",
    "release": "1.7.1",
    "commit": "1d1f3ee42f96744d809aca941ed2925347d198e9",
    "license": "Apache-2.0",
    "url": "https://github.com/SeldonIO/MLServer/tree/1d1f3ee42f96744d809aca941ed2925347d198e9",
    "mlserver_wheel_sha256": "d2ac7502915bb5311343a878aa1fb614f9b0f2436ee550a6137c1872a4dca193",
    "wheel_digest_role": "public release provenance metadata; not a local wheel verification",
    "selection_basis": "externally authored repository-load/reload/resident-object lifecycle; not selected for positive comparator gap",
}
CONTRACT = {
    "unit": "one REST operation within one serial lifecycle; dependent operations/arms",
    "operator_overlay": "a successful explicit load must reconstruct from the pinned stable opened inode; cached inference is not a new load",
    "native_policy": "mutable model name/version/URI; no immutable-hash authorization; failed reload retains prior resident model",
    "snapshot_assumption": "fresh trusted single-file uncompressed artifacts; opened inode immutable from first open through reconstruction; atomic pathname replacement only",
    "boundary": "actual ASGI REST handlers; parallel_workers=0; response cache enabled; no socket server or multiprocess deployment",
    "critical_frame": "settings, native model metadata/readiness/load status, pre/post pathname hashes; excludes predictions",
    "baselines": "native path/metadata ablation; S scoped receipt; actual signed in-toto rules on the same digest/census/pin evidence as S",
    "maximum_fits": 2,
    "maximum_loader_calls": 8,
    "arms": list(ARMS),
    "protected_validation": False,
    "provider_calls": 0,
    "same_inode_mutation": "out-of-model; buffered-read falsifier retained in unit tests; snapshot cannot certify every consumed byte",
    "provenance_replay": "report/rule-result consistency; ephemeral signatures verified during execution, not retained for independent signature replay",
}
VERSIONS = {
    "mlserver": "1.7.1",
    "mlserver-sklearn": "1.7.1",
    "joblib": "1.6.0",
    "httpx": "0.27.2",
    "in-toto": "3.0.0",
    "securesystemslib": "1.5.1",
}
CODE = (
    "src/aletheia_lab/evaluation/model_load_application.py",
    "src/aletheia_lab/evaluation/model_load_application_capture.py",
    "src/aletheia_lab/evaluation/model_load_application_analysis.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "scripts/model_load_application.py",
)
WORKER_CODE = (
    "import json,sys; from pathlib import Path; "
    "from aletheia_lab.evaluation.model_load_application import worker; "
    "print(json.dumps(worker(sys.argv[1],Path(sys.argv[2]),Path(sys.argv[3]))))"
)
NORMAL_SCOPES = (
    "initial-load",
    "readiness",
    "metadata",
    "initial-inference",
    "repeat-response-cache",
    "resident-after-path-update",
    "explicit-reload",
    "inference-after-reload",
    "failed-reload",
    "inference-after-failed-reload",
    "unload",
    "inference-after-unload",
)
PAIR_SCOPES = (*NORMAL_SCOPES[:5], *NORMAL_SCOPES[-2:])
PAIR_RELATIONS = {
    "repeat_response_equal",
    "response_cache_avoids_recomputation",
    "unload_emptied_registry",
}
NORMAL_RELATIONS = PAIR_RELATIONS | {
    "resident_model_and_object_reused",
    "explicit_reload_replaced_model_and_object",
    "reload_readiness_transition",
    "failed_reload_retained_previous_model",
}
UPSTREAM = {
    "registry": ("mlserver.registry", "registry.py"),
    "repository": ("mlserver.repository.repository", "repository.py"),
    "dataplane": ("mlserver.handlers.dataplane", "dataplane.py"),
    "repository_handlers": ("mlserver.handlers.model_repository", "model_repository.py"),
    "rest_app": ("mlserver.rest.app", "app.py"),
    "sklearn_runtime": ("mlserver_sklearn.sklearn", "sklearn.py"),
}
UPSTREAM_SHA256 = {
    "registry": "384b864f7243e7ec6dbc403b25ceafe0945c1693198ca07514bd454141c4ccba",
    "repository": "442ab9b1e9bc1d18d5e412db2ebe05bf59723bf5b0f880ad5ef37cbce27b8248",
    "dataplane": "f6e120776da64e085c2f7dfee1ef54d8a9282791d9d5abc2aaa0775d1c197fc6",
    "repository_handlers": "90b12c40a54f7d8e48d1ab10e03e7df0c5704ee0234e8904f49b87597d1a21df",
    "rest_app": "3b029d0fd95c0cda073f948565ae73128f6d7f5ab8293c90152de48ae57ef258",
    "sklearn_runtime": "208c86ee1b674a1e6991b2ec948d7110fe75a0264a5a2f5f43d7bf26104e8312",
}


def bindings(root: Path) -> dict[str, str]:
    return {name: content_sha256((root / name).read_bytes()) for name in CODE}


def environment() -> dict[str, Any]:
    try:
        observed = {name: version(name) for name in VERSIONS}
    except PackageNotFoundError as exc:
        raise ValueError("pinned optional application runtime is not installed") from exc
    if observed != VERSIONS:
        raise ValueError("install the pinned application optional dependency set")
    upstream: dict[str, str] = {}
    for key, (name, _) in UPSTREAM.items():
        path = importlib.import_module(name).__file__
        if path is None:
            raise ValueError("application source unavailable")
        upstream[key] = content_sha256(Path(path).read_bytes())
    if upstream != UPSTREAM_SHA256:
        raise ValueError("installed application source differs from pinned upstream commit")
    return {
        "versions": observed,
        "upstream_source_sha256": upstream,
        "scikit_learn": version("scikit-learn"),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


def _scoped(records: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    return [record for record in records if record["scope"] == scope]


def validate_evidence(evidence: dict[str, Any], digests: dict[str, str]) -> None:
    """Occurrence/census integrity, separate from desired semantic behavior."""
    scopes = NORMAL_SCOPES if evidence["arm"] in ("native", "captured") else PAIR_SCOPES
    operations = evidence["rows"]
    if (
        tuple(row["scope"] for row in operations) != scopes
        or evidence["artifact_sha256"] != digests
    ):
        raise ValueError("operation census or artifact domain changed")
    for row in operations:
        _validate_operation(row, digests)
    totals = {
        key: sum(row["counts"][key] for row in operations)
        for key in ("loader_calls", "reconstructions", "prediction_computations")
    }
    if evidence["counts"] != totals or totals["loader_calls"] > 3:
        raise ValueError("native cumulative census changed")
    references, receipts = evidence["reference"], evidence["receipts"]
    expected_scopes = (
        []
        if evidence["arm"] == "native"
        else [row["scope"] for row in operations if row["counts"]["reconstructions"] == 1]
    )
    if [row["scope"] for row in references] != expected_scopes or [
        row["scope"] for row in receipts
    ] != expected_scopes:
        raise ValueError("reference/receipt occurrence alignment changed")
    for raw, receipt in zip(references, receipts, strict=True):
        payload = bytes.fromhex(raw["raw_hex"])
        digest = content_sha256(payload)
        if (
            not 0 < len(payload) <= 262_144
            or digest not in digests.values()
            or digest != receipt["descriptor_sha256"]
        ):
            raise ValueError("reference/receipt domain or identity changed")
    _validate_frame(evidence, digests)


def _validate_frame(evidence: dict[str, Any], digests: dict[str, str]) -> None:
    frame = evidence["path_provenance_frame"]
    rows = {row["scope"]: row for row in evidence["rows"]}
    expected_settings = {
        "name": "probe",
        "implementation": "mlserver_sklearn.SKLearnModel",
        "parameters": {"version": "1", "uri": "model.joblib"},
        "cache_enabled": True,
    }
    expected = {
        "model_settings": expected_settings,
        "load_status": rows["initial-load"]["status"],
        "ready_status": rows["readiness"]["status"],
        "metadata": rows["metadata"]["body"],
        "path_pre_sha256": digests["A"],
        "path_post_sha256": digests["A"],
    }
    if frame != expected:
        raise ValueError("declared path frame is absent or not grounded to operations/domain")


def _validate_operation(row: dict[str, Any], digests: dict[str, str]) -> None:
    scope = row["scope"]
    route, method = "/v2/models/probe/infer", "POST"
    if scope in ("initial-load", "explicit-reload", "failed-reload", "unload"):
        route = "/v2/repository/models/probe/" + ("unload" if scope == "unload" else "load")
    elif scope in ("readiness", "metadata"):
        method, route = "GET", "/v2/models/probe" + ("/ready" if scope == "readiness" else "")
    if (row["method"], row["route"]) != (method, route):
        raise ValueError("operation route changed")
    if set(row["counts"]) != {"loader_calls", "reconstructions", "prediction_computations"} or any(
        type(value) is not int or not 0 <= value <= 1 for value in row["counts"].values()
    ):
        raise ValueError("invalid native operation census")
    pin = (
        digests["A"]
        if scope == "initial-load"
        else digests["B"]
        if scope in ("explicit-reload", "failed-reload")
        else None
    )
    if row["operator_pin_sha256"] != pin:
        raise ValueError("operator overlay changed")


def semantic_checks(evidence: dict[str, Any]) -> bool:
    """Do not accept equally wrong normal runs as instrument transparency."""
    rows = {row["scope"]: row for row in evidence["rows"]}
    normal = evidence["arm"] in ("native", "captured")
    relations = NORMAL_RELATIONS if normal else PAIR_RELATIONS
    scopes = NORMAL_SCOPES if normal else PAIR_SCOPES
    if (
        set(evidence["relations"]) != relations
        or not all(value is True for value in evidence["relations"].values())
        or tuple(rows) != scopes
        or evidence["unsupported"]
    ):
        return False
    statuses = all(
        row["status"]
        == (
            404
            if row["scope"] == "inference-after-unload"
            else row["status"]
            if row["scope"] == "failed-reload"
            else 200
        )
        for row in rows.values()
    )
    expected = [1, 1] if evidence["arm"] == "path-replaced" else [0, 0]
    initial = rows["initial-inference"]["body"].get("outputs", [{}])[0].get("data")
    if not statuses or initial != expected or evidence["socket_connection_attempts"] != 0:
        return False
    if not all(_expected_counts(row) for row in rows.values()):
        return False
    if evidence["arm"] not in ("native", "captured"):
        return bool(
            evidence["counts"]
            == {
                "loader_calls": 1,
                "reconstructions": 1,
                "prediction_computations": 1,
            }
        )
    return bool(
        evidence["counts"]
        == {"loader_calls": 2, "reconstructions": 2, "prediction_computations": 4}
        and rows["failed-reload"]["status"] == 422
        and rows["failed-reload"]["counts"]
        == {"loader_calls": 0, "reconstructions": 0, "prediction_computations": 0}
        and rows["resident-after-path-update"]["body"].get("outputs", [{}])[0].get("data") == [0, 0]
        and rows["inference-after-reload"]["body"].get("outputs", [{}])[0].get("data") == [1, 1]
        and rows["inference-after-failed-reload"]["body"].get("outputs", [{}])[0].get("data")
        == [1, 1]
    )


def _expected_counts(row: dict[str, Any]) -> bool:
    scope = row["scope"]
    load = scope in ("initial-load", "explicit-reload")
    predict = scope in (
        "initial-inference",
        "resident-after-path-update",
        "inference-after-reload",
        "inference-after-failed-reload",
    )
    return bool(
        row["counts"]
        == {
            "loader_calls": int(load),
            "reconstructions": int(load),
            "prediction_computations": int(predict),
        }
    )


def reference(row: dict[str, Any], raw_records: list[dict[str, Any]]) -> str:
    """Independent predicate reads the private raw sink, not S/P verdicts."""
    calls, reconstructions = row["counts"]["loader_calls"], row["counts"]["reconstructions"]
    records = _scoped(raw_records, row["scope"])
    if calls == 0 and reconstructions == 0 and not records:
        return "no_new_load"
    if row["status"] != 200 or calls != 1 or reconstructions != 1 or len(records) != 1:
        return "unknown"
    raw = bytes.fromhex(records[0]["raw_hex"])
    if not raw or len(raw) > 262_144 or row["operator_pin_sha256"] is None:
        raise ValueError("reference outside declared domain")
    return "compliant" if content_sha256(raw) == row["operator_pin_sha256"] else "violation"


def receipt_decision(row: dict[str, Any], receipts: list[dict[str, Any]]) -> str:
    records = _scoped(receipts, row["scope"])
    calls, reconstructions = row["counts"]["loader_calls"], row["counts"]["reconstructions"]
    if calls == 0 and reconstructions == 0 and not records:
        return "no_new_load"
    if row["status"] != 200 or (calls, reconstructions) != (1, 1) or len(records) != 1:
        return "unknown"
    pin = row["operator_pin_sha256"]
    if pin is None:
        return "unknown"
    return "compliant" if records[0]["descriptor_sha256"] == pin else "violation"


def compare(rows: list[dict[str, Any]], *, directory: Path | None = None) -> dict[str, Any]:
    """With directory execute real in-toto; replay checks stored comparator records."""
    comparisons: list[dict[str, Any]] = []
    for result in rows:
        if result["status"] != "completed" or result["arm"] == "native":
            continue
        for row in result["evidence"]["rows"]:
            expected = reference(row, result["evidence"]["reference"])
            s = receipt_decision(row, result["evidence"]["receipts"])
            p = s
            calls = 0
            verification: str | None = None
            if s in ("compliant", "violation"):
                receipt = _scoped(result["evidence"]["receipts"], row["scope"])[0]
                calls = 1
                if directory is not None:
                    verification = verify_chain(
                        {"artifact": row["operator_pin_sha256"]},
                        {"artifact": receipt["descriptor_sha256"]},
                        directory / f"{result['arm']}-{row['scope']}",
                    )
                else:
                    stored = result["comparators"][row["scope"]]
                    verification = stored["verification"]
                if verification not in ("pass", "artifact_rule_mismatch"):
                    raise ValueError(
                        "provenance verification did not yield a supported rule result"
                    )
                equality = receipt["descriptor_sha256"] == row["operator_pin_sha256"]
                if (verification == "pass") != equality:
                    raise ValueError("stored artifact-rule result contradicts represented digests")
                p = "compliant" if verification == "pass" else "violation"
            record = {
                "reference": expected,
                "S": s,
                "P": p,
                "verification": verification,
                "verifier_calls": calls,
            }
            if directory is None and record != result["comparators"][row["scope"]]:
                raise ValueError("stored comparator result changed")
            result.setdefault("comparators", {})[row["scope"]] = record
            comparisons.append(record)
    return {
        "planned_operation_denominator": 26,
        "operation_denominator": len(comparisons),
        "unscored_planned_operations": 26 - len(comparisons),
        "reference_counts": dict(Counter(row["reference"] for row in comparisons)),
        "S_reference_agreement": sum(row["S"] == row["reference"] for row in comparisons),
        "P_reference_agreement": sum(row["P"] == row["reference"] for row in comparisons),
        "actual_in_toto_verifier_calls": sum(row["verifier_calls"] for row in comparisons),
        "planned_explicit_load_operations": 5,
        "decided_load_operations": sum(
            row["reference"] in ("compliant", "violation") for row in comparisons
        ),
        "unknown_load_operations": sum(row["reference"] == "unknown" for row in comparisons),
        "failed_explicit_load_operations": sum(
            op["status"] >= 400
            and op["scope"] in ("initial-load", "explicit-reload", "failed-reload")
            for result in rows
            if result["status"] == "completed" and result["arm"] != "native"
            for op in result["evidence"]["rows"]
        ),
        "comparator_superiority_supported": False,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = {row["arm"]: row["evidence"] for row in rows if row["status"] == "completed"}
    native, captured = completed.get("native"), completed.get("captured")
    transparent = bool(
        native
        and captured
        and all(native[key] == captured[key] for key in ("rows", "counts", "relations"))
    )
    legal, replaced = completed.get("path-legal"), completed.get("path-replaced")
    equal_frame = bool(
        legal and replaced and legal["path_provenance_frame"] == replaced["path_provenance_frame"]
    )
    distinct = bool(legal and replaced and legal["reference"] != replaced["reference"])
    evidence = list(completed.values())
    semantic_pass = all(semantic_checks(row) for row in evidence) and len(completed) == 4
    return {
        "completed_arms": len(completed),
        "native_census_complete": len(completed) == 4,
        "failed_arms_with_unknown_partial_native_census": 4 - len(completed),
        "planned_http_operations": 38,
        "native_lifecycle_semantics_checked": semantic_pass,
        "planned_arms": len(ARMS),
        "source_cluster_count": 1,
        "transparency_on_normal_lifecycle": transparent,
        "equal_declared_path_frame": equal_frame,
        "different_descriptor_snapshots": distinct,
        "loader_calls": sum(row["counts"]["loader_calls"] for row in evidence),
        "reconstruction_entries": sum(row["counts"]["reconstructions"] for row in evidence),
        "http_operation_count": sum(len(row["rows"]) for row in evidence),
        "http_failure_count": sum(op["status"] >= 400 for row in evidence for op in row["rows"]),
        "reference_snapshot_bytes": sum(
            len(bytes.fromhex(raw["raw_hex"])) for row in evidence for raw in row["reference"]
        ),
        "socket_connection_attempts": sum(row["socket_connection_attempts"] for row in evidence),
        "disposition": "bounded_application_capture_transfer_no_new_checker_advantage"
        if semantic_pass and transparent and equal_frame and distinct
        else "incomplete_or_failed_application_development",
        "population_inference": False,
        "native_hash_policy_violation_claimed": False,
    }


def run(root: Path) -> dict[str, Any]:
    if os.name == "nt":
        raise ValueError("held-descriptor atomic-path control not qualified on Windows")
    code = bindings(root)
    env = environment()
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aletheia-application-") as name:
        directory = Path(name)
        digests = prepare_artifacts(directory / "artifacts")
        plan = {
            "source": SOURCE,
            "contract": CONTRACT,
            "code_bindings": code,
            "environment": env,
            "artifact_sha256": digests,
        }
        plan_sha = document_digest(plan)  # Bound before comparative REST outcomes.
        child_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OPENAI_", "ANTHROPIC_", "AWS_", "AZURE_"))
        }
        child_env["PYTHONPATH"] = str(root / "src")
        for arm in ARMS:
            row: dict[str, Any] = {"arm": arm, "status": "worker_failed", "evidence": None}
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        WORKER_CODE,
                        arm,
                        str(directory / arm),
                        str(directory / "artifacts"),
                    ],
                    cwd=directory,
                    env=child_env,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
                row["returncode"] = completed.returncode
                if completed.returncode == 0:
                    row["evidence"] = json.loads(completed.stdout)
                    row["status"] = "completed"
            except subprocess.TimeoutExpired:
                row["status"] = "timeout"
            rows.append(row)
        comparison = compare(rows, directory=directory / "signatures")
    summary = summarize(rows)
    for row in rows:
        if row["status"] == "completed":
            validate_evidence(row["evidence"], digests)
    if summary["loader_calls"] > CONTRACT["maximum_loader_calls"]:
        raise ValueError("aggregate native-call budget exceeded")
    bindings_unchanged = code == bindings(root) and env == environment()
    report = {
        "schema_version": "model-load-application-development/v1",
        "plan": plan,
        "plan_sha256": plan_sha,
        "rows": rows,
        "comparison": comparison,
        "summary": summary,
        "provider_calls": 0,
        "protected_validation_executed": False,
        "implementation_unchanged": bindings_unchanged,
    }
    report["report_sha256"] = document_digest(report)
    return report


def verify(root: Path, path: Path) -> dict[str, Any]:
    report = cast(dict[str, Any], json.loads(path.read_bytes()))
    claimed = report.pop("report_sha256")
    if (
        claimed != document_digest(report)
        or report["schema_version"] != "model-load-application-development/v1"
    ):
        raise ValueError("report integrity failed")
    plan = report["plan"]
    if (
        plan["source"] != SOURCE
        or plan["contract"] != CONTRACT
        or plan["code_bindings"] != bindings(root)
        or plan["environment"] != environment()
    ):
        raise ValueError("source/contract/implementation/environment binding changed")
    if report["plan_sha256"] != document_digest(plan) or [
        row["arm"] for row in report["rows"]
    ] != list(ARMS):
        raise ValueError("plan or full census changed")
    if report["summary"]["loader_calls"] > CONTRACT["maximum_loader_calls"]:
        raise ValueError("aggregate native-call budget changed")
    for row in report["rows"]:
        if row["status"] == "completed":
            if row["evidence"]["arm"] != row["arm"]:
                raise ValueError("worker identity changed")
            validate_evidence(row["evidence"], plan["artifact_sha256"])
    if report["comparison"] != compare(report["rows"]) or report["summary"] != summarize(
        report["rows"]
    ):
        raise ValueError("independent replay differs")
    if (
        report["provider_calls"] != 0
        or report["protected_validation_executed"] is not False
        or report["implementation_unchanged"] is not True
    ):
        raise ValueError("execution scope changed")
    report["report_sha256"] = claimed
    return report
