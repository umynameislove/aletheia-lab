"""Offline census of three already-exposed M4 development stores.

Read only an explicit allowlist. Never fit or deserialize a model, traverse a
protected study, or call a provider. Native control labels remain on the gold
branch, not the matched-input parser branch. This is a structured-source audit.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.source_evidence_admission import (
    PRODUCER,
    SourceDocument,
    checked_json,
    resolve_documents,
)
from aletheia_lab.project.identity import content_sha256

STORES = (
    "m4-artifact-binding-development-v1",
    "m4-artifact-binding-development-v2",
    "m4-artifact-binding-dose-v1",
)
_CORE = "src/aletheia_lab/benchmark/p2/"
_CASES = (
    ("healthy", "A", "A"),
    ("faulty", "A", "B"),
    ("sham", "A", "A"),
    ("corrected", "A", "A"),
    ("legitimate_B", "B", "B"),
    ("manifest_text_only", "A", "A"),
)
_LEGACY = (*_CASES[:4], ("promoted_B", "B", "B"))
_DOSES = (1, 5, 10, 25, 50)


def _file(path: Path) -> Path:
    if any(item.is_symlink() for item in (path, *path.parents)) or not path.is_file():
        raise ValueError("source file is missing or traverses a symlink")
    return path


def _read(path: Path) -> dict[str, Any]:
    return checked_json(_file(path).read_bytes())


def _tree(directory: Path) -> dict[str, str]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("allowlisted development store is unavailable")
    return {path.name: file_sha256(_file(path)) for path in sorted(directory.iterdir())}


def _bound(directory: Path, bindings: dict[str, Any]) -> None:
    for name, digest in bindings.items():
        if Path(name).name != name or file_sha256(_file(directory / name)) != digest:
            raise ValueError("retained development binding changed")


def _code(root: Path, plan: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        relative = _CORE + name
        if file_sha256(_file(root / relative)) != plan["code_sha256"][relative]:
            raise ValueError("historical loader producer code changed")


def _row(
    *,
    event: dict[str, Any],
    schema: str,
    pointer: str,
    scope: str,
    expected_requested: str,
    expected_loaded: str,
) -> dict[str, Any]:
    loaded_field = (
        "loaded_artifact_sha256" if schema == "development-path/v1" else "actual_loaded_sha256"
    )
    # This is an explicit blinded JSON field projection, not native raw prose.
    # Metrics, iteration counts, control names, recipes, and result labels are
    # deliberately NOT provided to either matched-input parser or future LLM.
    fields = ("declared_artifact_sha256", loaded_field, "reported_manifest_sha256")
    visible = {field: event[field] for field in fields if field in event}
    raw = json.dumps(visible, sort_keys=True, separators=(",", ":")).encode()
    document = SourceDocument(raw, schema, PRODUCER, scope, "/record")
    result = resolve_documents([document], scope=scope)
    # Gold endpoints come from the fixed control schedule + actual retained
    # artifact bytes, not result.facts or the parser's endpoint comparison.
    expected = "no_binding_fault" if expected_requested == expected_loaded else "binding_fault"
    gold = {"requested_endpoint": expected_requested, "loaded_endpoint": expected_loaded}
    observed = {fact["kind"]: fact["digest"] for fact in result["facts"]}
    return {
        "input_sha256": content_sha256(raw),
        "field_pointer": pointer,
        "reference_status": expected,
        "parsed_state": result["state"],
        "compatible": result["compatible"],
        "exact_facts": observed == gold,
        "status_correct": result["state"] == "identified" and result["compatible"] == [expected],
        "resolver_agrees": result["resolver_agrees"],
        "fact_bindings": [
            {**fact, "pointer": pointer + fact["pointer"].removeprefix("/record")}
            for fact in result["facts"]
        ],
        "native_request_attempt_time_available": False,
        "scope_kind": "receipt-local-control-slot; not a native request/attempt/time",
    }


def _legacy_rows(directory: Path, receipt: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = _read(directory / "manifest.json")
    if (
        receipt["schema_version"] != "model-artifact-binding-development-receipt/v1"
        or receipt["manifest_sha256"] != file_sha256(directory / "manifest.json")
        or receipt["manifest"] != manifest
        or set(receipt["paths"]) != {case for case, _, _ in _LEGACY}
    ):
        raise ValueError("legacy development census or manifest changed")
    artifacts = {
        name: file_sha256(_file(directory / f"artifact_{name}.joblib")) for name in ("A", "B")
    }
    if any(manifest[f"artifact_{name}_sha256"] != digest for name, digest in artifacts.items()):
        raise ValueError("legacy artifact bytes changed")
    return [
        _row(
            event=receipt["paths"][case],
            schema="development-path/v1",
            pointer=f"/paths/{case}",
            scope=f"legacy-slot-{index}",
            expected_requested=artifacts[requested],
            expected_loaded=artifacts[loaded],
        )
        for index, (case, requested, loaded) in enumerate(_LEGACY)
    ]


def _event_rows(
    directory: Path,
    events: list[dict[str, Any]],
    *,
    alternate: str,
    prefix: str,
) -> list[dict[str, Any]]:
    if len(events) != len(_CASES) or [event["case"] for event in events] != [
        case for case, _, _ in _CASES
    ]:
        raise ValueError("native loader census or event order changed")
    artifacts = {
        "A": file_sha256(_file(directory / "artifact_A.joblib")),
        "B": file_sha256(_file(directory / alternate)),
    }
    return [
        _row(
            event=event,
            schema="loader-event/v1",
            pointer=f"{prefix}/{index}",
            scope=f"{prefix}-slot-{index}",
            expected_requested=artifacts[requested],
            expected_loaded=artifacts[loaded],
        )
        for index, (event, (_, requested, loaded)) in enumerate(zip(events, _CASES, strict=True))
    ]


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "retained_loader_record_count": len(rows),
        "unique_blinded_input_count": len({row["input_sha256"] for row in rows}),
        "endpoint_fact_count": sum(len(row["fact_bindings"]) for row in rows),
        "exact_fact_count": sum(row["exact_facts"] for row in rows),
        "correct_status_count": sum(row["status_correct"] for row in rows),
        "reference_status_counts": dict(
            sorted(Counter(row["reference_status"] for row in rows).items())
        ),
        "state_counts": dict(sorted(Counter(row["parsed_state"] for row in rows).items())),
        "admission_error_count": sum(not row["exact_facts"] for row in rows),
        "parser_resolution_gap_count": sum(not row["status_correct"] for row in rows),
    }


def _verify_stores(
    root: Path, directories: list[Path]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Check historical receipt and producer chains without executing them."""
    legacy, forward, dose = directories
    receipts = [_read(directory / "receipt.json") for directory in directories]
    plan = _read(forward / "plan.json")
    dose_plan = _read(dose / "plan.json")
    _bound(legacy, plan["predecessor_sha256"])
    for directory, receipt, bound_plan, schema in (
        (forward, receipts[1], plan, "model-artifact-binding-forward-receipt/v1"),
        (dose, receipts[2], dose_plan, "model-artifact-binding-dose-receipt/v1"),
    ):
        if receipt["schema_version"] != schema:
            raise ValueError("unsupported development receipt schema")
        _bound(directory, receipt["retained_sha256"])
        if receipt["plan_sha256"] != file_sha256(directory / "plan.json"):
            raise ValueError("development plan identity changed")
        if (
            receipt["provider_calls"] != 0
            or receipt["protected_predictions_or_metrics_computed"] is not False
        ):
            raise ValueError("only exposed offline development is eligible")
        _code(
            root,
            bound_plan,
            ("model_artifact_binding_development.py", "model_artifact_binding_forward.py"),
        )
    _code(root, dose_plan, ("model_artifact_binding_dose.py",))
    if dose_plan["predecessor_receipt_sha256"] != file_sha256(forward / "receipt.json"):
        raise ValueError("dose predecessor identity changed")
    if (
        receipts[0]["provider_calls"] != 0
        or receipts[0]["sealed_predictions_or_metrics_computed"] is not False
    ):
        raise ValueError("legacy source is not exposed development")
    return receipts, plan, dose_plan


def _licensed_source(
    root: Path, receipts: list[dict[str, Any]], plan: dict[str, Any], dose_plan: dict[str, Any]
) -> dict[str, Any]:
    binding = _read(root / "configs/benchmark/p2_label_noise_shift_v3_dataset_bindings.json")
    sources = [
        item for item in binding["datasets"] if item["dataset_id"] == plan["source"]["dataset_id"]
    ]
    if len(sources) != 1 or sources[0]["archive"]["sha256"] != plan["source"]["archive_sha256"]:
        raise ValueError("source/license identity changed")
    if dose_plan["source"] != plan["source"] or receipts[0]["manifest"]["source"] != plan["source"]:
        raise ValueError("development stores do not share the expected source")
    return dict(sources[0])


def audit_development_sources(*, root: Path, memory_root: Path) -> dict[str, Any]:
    """Recompute a bounded descriptive census; not a new source validation run."""
    root = root.resolve(strict=True)
    if any(item.is_symlink() for item in (memory_root, *memory_root.absolute().parents)):
        raise ValueError("memory root must not traverse a symlink")
    memory_root = memory_root.resolve(strict=True)
    if memory_root.is_relative_to(root):
        raise ValueError("private sources must remain outside the repository")
    directories = [memory_root / name for name in STORES]
    before = {name: _tree(directory) for name, directory in zip(STORES, directories, strict=True)}
    receipts, plan, dose_plan = _verify_stores(root, directories)
    source = _licensed_source(root, receipts, plan, dose_plan)
    legacy, forward, dose = directories
    rows_by_store = [
        _legacy_rows(legacy, receipts[0]),
        _event_rows(
            forward,
            _read(forward / "load-trace.json")["events"],
            alternate="artifact_B.joblib",
            prefix="/events",
        ),
    ]
    doses = _read(dose / "scores.json")["doses"]
    if set(doses) != {str(value) for value in _DOSES}:
        raise ValueError("dose census changed")
    dose_rows = []
    for value in _DOSES:
        dose_rows.extend(
            _event_rows(
                dose,
                doses[str(value)]["events"],
                alternate=f"artifact_B{value}.joblib",
                prefix=f"/doses/{value}/events",
            )
        )
    rows_by_store.append(dose_rows)
    inventory = [
        {
            "store": name,
            "producer_family": PRODUCER,
            "container": "receipt.paths"
            if index == 0
            else "load-trace.events"
            if index == 1
            else "scores.doses.events",
            "native_schema_version": receipts[index]["schema_version"] if index == 0 else None,
            "parser_contract": "development-path/v1" if index == 0 else "loader-event/v1",
            "producer_version_assigned_by_adapter": index != 0,
            "native_request_attempt_timestamp": "absent",
            "scope": "receipt/container/control-slot; no temporal reconstruction",
            "raw_control_label_leakage": True,
            "matched_input": "blinded native JSON field projection; not raw text",
            "trust": "local historical loader code and receipt; not hostile-host attestation",
            "dataset_license": source["license"],
            "native_trace_public_license": "not established by the dataset license",
            "source_uri": source["archive"]["source_uri"],
            "completeness": "fixed retained control census only; not all runtime events",
            **_summary(rows),
            "rows": rows,
        }
        for index, (name, rows) in enumerate(zip(STORES, rows_by_store, strict=True))
    ]
    after = {name: _tree(directory) for name, directory in zip(STORES, directories, strict=True)}
    if before != after:
        raise ValueError("source stores mutated during the audit")
    all_rows = [row for rows in rows_by_store for row in rows]
    summary = _summary(all_rows)
    return {
        "schema_version": "source-evidence-development-headroom/v1",
        "status": "no_demonstrated_semantic_headroom"
        if summary["parser_resolution_gap_count"] == 0 and summary["admission_error_count"] == 0
        else "development_source_boundary_gap",
        "source_cluster_count": 1,
        "producer_family_count": 1,
        "native_runtime_event_count": len(rows_by_store[1]) + len(rows_by_store[2]),
        "aggregate_path_record_count": len(rows_by_store[0]),
        "native_container_layout_count": 3,
        "endpoint_parser_contract_count": 2,
        "inventory_scope": "explicit three-store exposed-development allowlist; not a search of all project evidence",
        "inventory": inventory,
        "summary": summary,
        "source_sha256_before": before,
        "source_sha256_after": after,
        "sources_unchanged": True,
        "gold_reference": "separate fixed control schedule plus retained artifact byte hashes; no parser outputs",
        "gold_reference_limit": "independent replay logic, not independent producer/host attestation",
        "llm_comparison_executed": False,
        "provider_calls": 0,
        "models_fitted_or_loaded": False,
        "protected_sources_opened": False,
        "semantic_llm_trial_ready": False,
        "next_decision": "keep deterministic adapter; seek authorized genuinely semantic source before DEV-02",
    }
