from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import aletheia_lab.evaluation.source_evidence_headroom as audit
from aletheia_lab.content_hashing import file_sha256


def publish(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def synthetic_stores(tmp_path: Path) -> tuple[Path, Path]:
    root, memory = tmp_path / "repo", tmp_path / "memory"
    root.mkdir()
    memory.mkdir()
    source = {"dataset_id": "fixture", "archive_sha256": "a" * 64}
    publish(
        root / "configs/benchmark/p2_label_noise_shift_v3_dataset_bindings.json",
        {
            "datasets": [
                {
                    "dataset_id": "fixture",
                    "license": "fixture-only",
                    "archive": {"sha256": "a" * 64, "source_uri": "https://example.invalid"},
                }
            ],
        },
    )
    code = {}
    for name in (
        "model_artifact_binding_development.py",
        "model_artifact_binding_forward.py",
        "model_artifact_binding_dose.py",
    ):
        path = root / audit._CORE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic producer fixture; never import")
        code[audit._CORE + name] = file_sha256(path)
    directories = [memory / name for name in audit.STORES]
    for directory in directories:
        directory.mkdir()
        (directory / "artifact_A.joblib").write_bytes(b"non-executable fixture A")
        (directory / "artifact_B.joblib").write_bytes(b"non-executable fixture B")
    legacy, forward, dose = directories
    digests = {name: file_sha256(legacy / f"artifact_{name}.joblib") for name in ("A", "B")}
    manifest = {
        "source": source,
        **{f"artifact_{name}_sha256": digest for name, digest in digests.items()},
    }
    publish(legacy / "manifest.json", manifest)
    legacy_paths = {
        case: {
            "declared_artifact_sha256": digests[requested],
            "loaded_artifact_sha256": digests[loaded],
            "raw_log_loss": 1.0,
        }
        for case, requested, loaded in audit._LEGACY
    }
    publish(
        legacy / "receipt.json",
        {
            "schema_version": "model-artifact-binding-development-receipt/v1",
            "manifest": manifest,
            "manifest_sha256": file_sha256(legacy / "manifest.json"),
            "paths": legacy_paths,
            "provider_calls": 0,
            "sealed_predictions_or_metrics_computed": False,
        },
    )
    publish(
        forward / "plan.json",
        {"source": source, "code_sha256": code, "predecessor_sha256": audit._tree(legacy)},
    )
    events = [
        {
            "case": case,
            "declared_artifact_sha256": digests[requested],
            "actual_loaded_sha256": digests[loaded],
            "reported_manifest_sha256": digests[requested],
        }
        for case, requested, loaded in audit._CASES
    ]
    publish(forward / "load-trace.json", {"events": events})
    publish(
        forward / "receipt.json",
        {
            "schema_version": "model-artifact-binding-forward-receipt/v1",
            "plan_sha256": file_sha256(forward / "plan.json"),
            "provider_calls": 0,
            "protected_predictions_or_metrics_computed": False,
            "retained_sha256": {"load-trace.json": file_sha256(forward / "load-trace.json")},
        },
    )
    for value in audit._DOSES:
        (dose / f"artifact_B{value}.joblib").write_bytes(b"non-executable fixture B")
    publish(
        dose / "plan.json",
        {
            "source": source,
            "code_sha256": code,
            "predecessor_receipt_sha256": file_sha256(forward / "receipt.json"),
        },
    )
    publish(
        dose / "scores.json", {"doses": {str(value): {"events": events} for value in audit._DOSES}}
    )
    publish(
        dose / "receipt.json",
        {
            "schema_version": "model-artifact-binding-dose-receipt/v1",
            "plan_sha256": file_sha256(dose / "plan.json"),
            "provider_calls": 0,
            "protected_predictions_or_metrics_computed": False,
            "retained_sha256": {"scores.json": file_sha256(dose / "scores.json")},
        },
    )
    # The allowlist must not traverse even an adjacent protected decoy.
    (memory / "m4-new-sources-v1").mkdir()
    (memory / "m4-new-sources-v1" / "predictions.json").write_bytes(b"not valid JSON; do not open")
    return root, memory


def test_complete_census_and_source_bound_gold_without_deserialization(tmp_path: Path) -> None:
    root, memory = synthetic_stores(tmp_path)
    report = audit.audit_development_sources(root=root, memory_root=memory)
    assert report["status"] == "no_demonstrated_semantic_headroom"
    assert report["summary"]["retained_loader_record_count"] == 41
    assert report["summary"]["endpoint_fact_count"] == 82
    assert report["native_runtime_event_count"] == 36
    assert report["aggregate_path_record_count"] == 5
    assert report["summary"]["exact_fact_count"] == report["summary"]["correct_status_count"] == 41
    assert report["summary"]["reference_status_counts"] == {
        "binding_fault": 7,
        "no_binding_fault": 34,
    }
    assert report["producer_family_count"] == report["source_cluster_count"] == 1
    assert report["source_sha256_before"] == report["source_sha256_after"]
    assert report["models_fitted_or_loaded"] is False
    assert report["semantic_llm_trial_ready"] is False
    assert all(
        "not established" in item["native_trace_public_license"] for item in report["inventory"]
    )


def test_gold_is_not_computed_from_parser_and_wrong_facts_do_not_pass_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, memory = synthetic_stores(tmp_path)
    original = audit.resolve_documents

    def swap_facts(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(*args, **kwargs)
        if result["compatible"] == ["binding_fault"]:
            result["facts"][0]["digest"], result["facts"][1]["digest"] = (
                result["facts"][1]["digest"],
                result["facts"][0]["digest"],
            )
        return result

    monkeypatch.setattr(audit, "resolve_documents", swap_facts)
    report = audit.audit_development_sources(root=root, memory_root=memory)
    assert report["summary"]["correct_status_count"] == 41  # Symmetric equality alone hides it.
    assert report["summary"]["exact_fact_count"] == 34
    assert report["status"] == "development_source_boundary_gap"


@pytest.mark.parametrize("target", ["trace", "artifact", "code", "plan", "census", "schema"])
def test_changed_sources_fail_closed(tmp_path: Path, target: str) -> None:
    root, memory = synthetic_stores(tmp_path)
    forward = memory / audit.STORES[1]
    if target == "trace":
        (forward / "load-trace.json").write_bytes(b"{}")
    elif target == "artifact":
        (memory / audit.STORES[0] / "artifact_A.joblib").write_bytes(b"changed")
    elif target == "code":
        (root / audit._CORE / "model_artifact_binding_forward.py").write_bytes(b"changed")
    elif target == "plan":
        (forward / "plan.json").write_bytes(b"{}")
    elif target == "census":
        receipt = json.loads((memory / audit.STORES[0] / "receipt.json").read_text())
        receipt["paths"].pop("faulty")
        publish(memory / audit.STORES[0] / "receipt.json", receipt)
    else:
        receipt = json.loads((forward / "receipt.json").read_text())
        receipt["schema_version"] = "unknown-producer/v1"
        publish(forward / "receipt.json", receipt)
    with pytest.raises((ValueError, KeyError)):
        audit.audit_development_sources(root=root, memory_root=memory)


def test_blinded_projection_excludes_native_control_and_scientific_outcomes() -> None:
    event = {
        "case": "faulty",
        "declared_artifact_sha256": "a" * 64,
        "actual_loaded_sha256": "b" * 64,
        "raw_log_loss": 100,
        "actual_iterations": 1,
    }
    first = audit._row(
        event=event,
        schema="loader-event/v1",
        pointer="/0",
        scope="slot",
        expected_requested="a" * 64,
        expected_loaded="b" * 64,
    )
    event.update(case="healthy", raw_log_loss=-100, actual_iterations=999)
    second = audit._row(
        event=event,
        schema="loader-event/v1",
        pointer="/0",
        scope="slot",
        expected_requested="a" * 64,
        expected_loaded="b" * 64,
    )
    assert first["input_sha256"] == second["input_sha256"]
    assert first["compatible"] == second["compatible"] == ["binding_fault"]


def test_symlink_source_and_private_inside_repo_rejected(tmp_path: Path) -> None:
    root, memory = synthetic_stores(tmp_path)
    legacy = memory / audit.STORES[0]
    linked = legacy / "extra.json"
    try:
        linked.symlink_to(legacy / "receipt.json")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError):
        audit.audit_development_sources(root=root, memory_root=memory)
    with pytest.raises(ValueError):
        audit.audit_development_sources(root=root, memory_root=root)


def test_modules_have_no_provider_fit_or_pickle_dependency() -> None:
    directory = Path(audit.__file__).parent
    for name in ("source_evidence_admission.py", "source_evidence_headroom.py"):
        tree = ast.parse((directory / name).read_text(encoding="utf-8"))
        modules = {
            node.module if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any(
            part in module
            for module in modules
            if module
            for part in ("openai", "model_gateway", "joblib", "pickle", "sklearn")
        )


def test_cli_is_byte_stable_and_writes_only_new_private_report(tmp_path: Path) -> None:
    root, memory = synthetic_stores(tmp_path)
    script = Path(__file__).resolve().parents[2] / "scripts/audit_source_evidence_headroom.py"
    output = memory / "headroom.json"
    outputs = []
    for seed in ("1", "104729"):
        environment = {
            **os.environ,
            "PYTHONHASHSEED": seed,
            "PYTHONPATH": str(script.parent.parent / "src"),
        }
        arguments = [sys.executable, str(script), "--root", str(root), "--memory-root", str(memory)]
        if seed == "1":
            arguments.extend(("--output", str(output)))
        result = subprocess.run(
            arguments, capture_output=True, text=True, env=environment, timeout=30
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
    assert json.loads(output.read_text())["sources_unchanged"] is True
    unchanged = output.read_bytes()
    result = subprocess.run(
        arguments + ["--output", str(output)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 1
    assert output.read_bytes() == unchanged


def test_cli_cannot_write_inside_an_adjacent_study(tmp_path: Path) -> None:
    root, memory = synthetic_stores(tmp_path)
    script = Path(__file__).resolve().parents[2] / "scripts/audit_source_evidence_headroom.py"
    output = memory / "m4-new-sources-v1" / "audit.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(root),
            "--memory-root",
            str(memory),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(script.parent.parent / "src")},
        timeout=30,
    )
    assert result.returncode == 1
    assert not output.exists()
