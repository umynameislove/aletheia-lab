"""Prospective, bounded SQLite transfer; only execute can invoke a paid caller."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as sha256
from aletheia_lab.evaluation.native_cache_experiment import CODE_FILES, _read
from aletheia_lab.evaluation.native_cache_extraction import resolve_facts
from aletheia_lab.evaluation.sqlite_evidence_extraction import (
    PROMPT,
    assess_proposal,
    citation_schema,
    parser_facts,
    provider_payload,
)
from aletheia_lab.evaluation.sqlite_evidence_source import (
    CONTROLS,
    EVALUATION_NAMESPACE,
    VIEWS,
    generate_source,
    producer_identity,
    visible_reference,
)
from aletheia_lab.evaluation.warrant_development import DevelopmentCall, DevelopmentCaller
from aletheia_lab.evaluation.warrant_development_io import (
    DESTINATION,
    MAX_CALL_USD,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL,
    _private_dir,
    _write,
)
from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller
from aletheia_lab.model_gateway.openai import _openai_response_format

COST_CEILING_USD = 0.25
_NEW_FILES = (
    "evaluation/sqlite_evidence_source.py",
    "evaluation/sqlite_evidence_extraction.py",
    "evaluation/sqlite_evidence_transfer.py",
    "evaluation/execution_contracts.py",
)


def _code_identity() -> dict[str, str]:
    base = Path(__file__).resolve().parents[1]
    return {
        **{name: file_sha256(base / name) for name in (*CODE_FILES, *_NEW_FILES)},
        "scripts/sqlite_evidence_transfer.py": file_sha256(
            base.parents[1] / "scripts/sqlite_evidence_transfer.py"
        ),
    }


def _selection() -> dict[str, Any]:
    """Outcome-sensitive decisions are fixed before generating evaluation bytes."""
    return {
        "code_sha256": _code_identity(),
        "prompt_sha256": sha256(PROMPT),
        "producer": producer_identity(),
        "controls": [list(control) for control in CONTROLS],
        "views": list(VIEWS),
        "evaluation_namespace": EVALUATION_NAMESPACE,
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "primary_estimand": "paired exact visible cited-frame rate: LLM minus strong parser across the fixed ten slots",
        "secondary_estimands": [
            "content fidelity",
            "citation-only rejection",
            "grounding",
            "unsupported authority/role",
            "eligible omissions",
            "raw visible-resolution violation",
            "strict guarded resolution",
            "cost and latency",
        ],
        "units": "one additional controlled producer, five control loads, two dependent visibility views per load",
        "precision": "complete finite census with exact counts; no population interval or independent-call replication",
        "failure_policy": "one attempt per slot, zero retries; technical/invalid responses retained in all ten slots; no automatic replay",
        "adaptation": "new documented nested grammar calibrated only on separate fixture bytes; not zero-shot unknown-schema transfer",
        "scientific_scope": "documentation-informed transfer from exposed Joblib development to a new controlled SQLite producer; not natural incidents, causal admission or guaranteed superiority",
    }


def _checked_source(directory: Path) -> dict[str, Any]:
    saved = _read(directory / "source.json")
    # Independent replay executes native SELECT on the fixed local bytes. Gold
    # never comes from the parser, model, or guard being evaluated.
    if saved != generate_source():
        raise ValueError("native source differs from the complete independent control replay")
    return saved


def request_frame(directory: Path) -> list[dict[str, Any]]:
    source = _checked_source(directory)
    requests = []
    for case in source["cases"]:
        for view in VIEWS:
            documents = [
                doc
                for doc in case["documents"]
                if view == "with_consumer" or doc["kind"] != "sqlite-blob-witness"
            ]
            reference = visible_reference(case, view)
            parsed = parser_facts(documents)
            if (
                sorted(parsed, key=lambda fact: tuple(sorted(fact.items())))
                != sorted(reference["facts"], key=lambda fact: tuple(sorted(fact.items())))
                or resolve_facts(parsed) != reference["resolution"]
            ):
                raise ValueError(
                    "strong parser or resolver differs from independent full cited-frame reference"
                )
            payload = provider_payload(documents)
            schema = citation_schema(documents)
            requests.append(
                {
                    "request_id": sha256([case["case_id"], view, PROMPT, payload, schema]),
                    "case_id": case["case_id"],
                    "view": view,
                    "payload": payload,
                    "schema": schema,
                    "reference": reference,
                }
            )
    if len(requests) != 10 or len({r["request_id"] for r in requests}) != 10:
        raise ValueError("prospective request census differs or request IDs collide")
    return requests


def _audit_wires(requests: list[dict[str, Any]]) -> dict[str, Any]:
    captured: list[dict[str, Any]] = []

    class Completions:
        def create(self, **kwargs: Any) -> object:
            captured.append(kwargs)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            refusal=None,
                            content='{"schema_version":"source-evidence-proposal/v1","facts":[]}',
                        ),
                    )
                ],
            )

    caller = OpenAIDevelopmentCaller(
        maximum_calls=len(requests),
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
    )
    for index, request in enumerate(requests):
        call = caller.invoke(prompt=PROMPT, payload=request["payload"], schema=request["schema"])
        expected = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(request["payload"], ensure_ascii=False, sort_keys=True),
                },
            ],
            "response_format": _openai_response_format(json.dumps(request["schema"])),
            "temperature": 0.0,
            "seed": 731,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        if not call.provider_attempted or len(captured) != index + 1 or captured[-1] != expected:
            raise ValueError("SDK wire differs or exceeds the frozen token budget")
    return {
        "sdk_capture_count": len(captured),
        "distinct_visible_wire_count": len({sha256(wire) for wire in captured}),
        "wire_sha256": sha256(captured),
        "provider_calls_executed": 0,
    }


def _expected_plan(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    requests = request_frame(directory)
    selection = _selection()
    reservation = round(len(requests) * MAX_CALL_USD, 6)
    plan = {
        "schema_version": "sqlite-evidence-transfer-plan/v1",
        "selection": selection,
        "selection_sha256": sha256(selection),
        "source_file_sha256": file_sha256(directory / "source.json"),
        "request_frame_sha256": sha256(requests),
        "source_cluster_count": 1,
        "case_count": len(CONTROLS),
        "request_count": len(requests),
        "maximum_provider_calls": len(requests),
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "maximum_reserved_cost_usd_at_frozen_rates": reservation,
        "cost_ceiling_usd": COST_CEILING_USD,
        "sdk_retries": 0,
        "store": False,
        "provider_fields": [
            "native SELECT trace",
            "row declarations and lookup key",
            "nested caller intent digest",
            "nested consumed digest only in full view",
            "document hashes and independently bound producer/authority/scope semantics",
        ],
        "withheld_fields": [
            "control names/schedule",
            "private case/view reference",
            "raw BLOB bytes",
            "returned values",
            "evaluation namespace",
            "private paths",
            "historical outcomes or human labels",
        ],
        "wire_audit": _audit_wires(requests),
    }
    if reservation > COST_CEILING_USD:
        raise ValueError("planned reservation exceeds operator ceiling")
    return plan, requests


def prepare(*, root: Path, directory: Path) -> dict[str, Any]:
    if root.resolve() / "src/aletheia_lab" != Path(__file__).resolve().parents[1]:
        raise ValueError("root differs from the executing checkout")
    selection = _selection()
    _private_dir(directory, new=True)
    _write(directory / "selection.json", {**selection, "selection_sha256": sha256(selection)})
    _write(directory / "source.json", generate_source())
    plan, _ = _expected_plan(directory)
    _write(directory / "plan.json", {**plan, "plan_sha256": sha256(plan)})
    return preflight(directory=directory)


def checked_plan(
    *, directory: Path, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _private_dir(directory)
    expected, requests = _expected_plan(directory)
    selection = expected["selection"]
    if _read(directory / "selection.json") != {**selection, "selection_sha256": sha256(selection)}:
        raise ValueError("pre-generation selection changed")
    saved = _read(directory / "plan.json")
    if saved != {**expected, "plan_sha256": sha256(expected)} or (
        confirm_sha256 is not None and confirm_sha256 != sha256(expected)
    ):
        raise ValueError("source, code, runtime, plan or confirmation changed")
    return saved, requests


def preflight(*, directory: Path) -> dict[str, Any]:
    plan, requests = checked_plan(directory=directory)
    return {
        "status": "offline_sqlite_evidence_transfer_preflight_pass",
        "plan_sha256": plan["plan_sha256"],
        "case_count": len(CONTROLS),
        "request_count": len(requests),
        "source_cluster_count": 1,
        "parser_exact_reference_count": len(requests),
        "reference_states": {
            view: [
                request["reference"]["resolution"]["state"]
                for request in requests
                if request["view"] == view
            ]
            for view in VIEWS
        },
        "maximum_provider_calls": plan["maximum_provider_calls"],
        "maximum_reserved_cost_usd_at_frozen_rates": plan[
            "maximum_reserved_cost_usd_at_frozen_rates"
        ],
        "cost_ceiling_usd": COST_CEILING_USD,
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "wire_audit": plan["wire_audit"],
        "llm_transfer_measured": False,
        "execution_authorized": False,
    }


def _row(request: dict[str, Any], call: DevelopmentCall) -> dict[str, Any]:
    if (
        call.estimated_cost_usd > MAX_CALL_USD
        or call.input_tokens > MAX_INPUT_TOKENS
        or call.output_tokens > MAX_OUTPUT_TOKENS
    ):
        raise ValueError("caller exceeded frozen resources")
    assessment = (
        assess_proposal(request["payload"]["documents"], (call.payload_json or "").encode())
        if call.status == "completed"
        else {
            "status": "technical_failure",
            "exact_fact_frame": False,
            "raw_resolution": None,
            "guarded_resolution": None,
            "unwarranted_singleton": False,
            "wrong_visible_status": False,
        }
    )
    if (
        call.status == "completed"
        and assessment["baseline_resolution"] != request["reference"]["resolution"]
    ):
        raise ValueError("assessment baseline differs from independent reference")
    return {
        "request_id": request["request_id"],
        "case_id": request["case_id"],
        "view": request["view"],
        "call": call.model_dump(mode="json"),
        "assessment": assessment,
    }


def _analysis(rows: list[dict[str, Any]], requests: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        status = row["assessment"]["status"]
        counts[status] = counts.get(status, 0) + 1
    exact = sum(row["assessment"]["exact_fact_frame"] for row in rows)
    return {
        "denominator": len(requests),
        "source_cluster_count": 1,
        "native_control_load_count": len(CONTROLS),
        "distinct_visible_wire_count": len({sha256([r["payload"], r["schema"]]) for r in requests}),
        "status_counts": counts,
        "exact_fact_frame_count": exact,
        "parser_exact_reference_count": len(requests),
        "paired_exact_rate_difference_llm_minus_parser": exact / len(requests) - 1,
        "content_frame_equal_count": sum(
            row["assessment"].get("content_frame_equal", False) for row in rows
        ),
        "citation_only_rejection_count": sum(
            row["assessment"].get("citation_only_rejection", False) for row in rows
        ),
        "unwarranted_singleton_count": sum(
            row["assessment"]["unwarranted_singleton"] for row in rows
        ),
        "visible_resolution_violation_count": sum(
            row["assessment"]["wrong_visible_status"] for row in rows
        ),
        "error_counts": {
            key: sum(row["assessment"].get(key, 0) for row in rows)
            for key in (
                "grounding_error_count",
                "unsupported_fact_count",
                "omitted_eligible_fact_count",
            )
        },
        "guarded_resolution_states": {
            state: sum(
                row["assessment"]["guarded_resolution"] is not None
                and row["assessment"]["guarded_resolution"]["state"] == state
                for row in rows
            )
            for state in ("ambiguous", "identified", "conflict")
        },
        "provider_attempt_count": sum(row["call"]["provider_attempted"] for row in rows),
        "cost_usd_at_frozen_rates": round(
            sum(row["call"]["estimated_cost_usd"] for row in rows), 6
        ),
        "provider_latency_seconds": sum(row["call"]["latency_seconds"] for row in rows),
        "parser_provider_calls": 0,
        "views": {
            view: {
                "denominator": sum(row["view"] == view for row in rows),
                "exact_count": sum(
                    row["view"] == view and row["assessment"]["exact_fact_frame"] for row in rows
                ),
            }
            for view in VIEWS
        },
        "paired_control_results": [
            {
                "case_id": case_id,
                "exact_in_both_views": all(
                    row["assessment"]["exact_fact_frame"]
                    for row in rows
                    if row["case_id"] == case_id
                ),
            }
            for case_id in sorted({row["case_id"] for row in rows})
        ],
        "limitations": "one new controlled producer with documentation-informed schema adaptation; dependent views, exact finite census not population inference; strict guard contribution is not unaided model superiority or natural-incident/zero-shot validation",
    }


def _receipt(
    plan: dict[str, Any], rows: list[dict[str, Any]], requests: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "status": "sqlite_evidence_transfer_complete",
        "plan_sha256": plan["plan_sha256"],
        "results_sha256": sha256(rows),
        "analysis": _analysis(rows, requests),
        "source_mutated": False,
    }


def execute(
    *,
    directory: Path,
    confirm_sha256: str,
    caller: DevelopmentCaller,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    plan, requests = checked_plan(directory=directory, confirm_sha256=confirm_sha256)
    if (directory / "lease.json").exists() or (directory / "results").exists():
        raise ValueError("attempt exists; do not replay provider calls")
    _write(
        directory / "lease.json",
        {"plan_sha256": plan["plan_sha256"], "maximum_provider_calls": len(requests)},
    )
    (directory / "results").mkdir(mode=0o700)
    rows = []
    for index, request in enumerate(requests):
        try:
            call = caller.invoke(
                prompt=PROMPT, payload=request["payload"], schema=request["schema"]
            )
        except Exception:
            call = DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=MAX_INPUT_TOKENS,
                output_tokens=MAX_OUTPUT_TOKENS,
                estimated_cost_usd=MAX_CALL_USD,
                latency_seconds=0.0,
                usage_observed=False,
            )
        row = _row(request, call)
        _write(directory / "results" / f"{index:02d}.json", row)
        rows.append(row)
        if progress is not None:
            progress(
                {
                    "status": "sqlite_evidence_transfer_progress",
                    "completed_requests": len(rows),
                    "maximum_requests": len(requests),
                }
            )
    checked_plan(directory=directory)
    _write(directory / "receipt.json", _receipt(plan, rows, requests))
    return verify(directory=directory)


def verify(*, directory: Path) -> dict[str, Any]:
    plan, requests = checked_plan(directory=directory)
    if _read(directory / "lease.json") != {
        "plan_sha256": plan["plan_sha256"],
        "maximum_provider_calls": len(requests),
    }:
        raise ValueError("execution lease differs")
    results = directory / "results"
    if results.is_symlink() or {path.name for path in results.iterdir()} != {
        f"{index:02d}.json" for index in range(len(requests))
    }:
        raise ValueError("result census is incomplete or has extra slots")
    rows = []
    for index, request in enumerate(requests):
        saved = _read(results / f"{index:02d}.json")
        expected = _row(request, DevelopmentCall.model_validate(saved["call"]))
        if saved != expected:
            raise ValueError("response, assessment or request binding changed")
        rows.append(expected)
    receipt = _receipt(plan, rows, requests)
    if _read(directory / "receipt.json") != receipt:
        raise ValueError("receipt does not reproduce from retained responses")
    return receipt
