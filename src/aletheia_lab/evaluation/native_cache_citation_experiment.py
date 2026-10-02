"""Cached citation diagnostics and a separate, bounded canonical-locator API run."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation import native_cache_experiment as original
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as sha256
from aletheia_lab.evaluation.native_cache_citation import (
    PROMPT,
    citation_schema,
    diagnose_citations,
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

COST_CEILING_USD = original.COST_CEILING_USD


def _predecessor(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    receipt = original.verify(directory=directory)
    manifest = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or (path.is_file() and path.stat().st_size > 1_000_000):
            raise ValueError("predecessor must be a bounded regular artifact tree")
        if path.is_file():
            manifest[path.relative_to(directory).as_posix()] = file_sha256(path)
    return receipt, original.request_frame(directory), manifest


def replay(*, predecessor: Path) -> dict[str, Any]:
    receipt, requests, before = _predecessor(predecessor)
    diagnostics = []
    for index, request in enumerate(requests):
        row = original._read(predecessor / "results" / f"{index:02d}.json")
        call = DevelopmentCall.model_validate(row["call"])
        diagnostic = diagnose_citations(
            request["payload"]["documents"], (call.payload_json or "").encode()
        )
        diagnostics.append({"case_id": request["case_id"], "view": request["view"], **diagnostic})
    if _predecessor(predecessor)[2] != before:
        raise ValueError("predecessor changed during cached replay")
    return {
        "status": "offline_native_cache_citation_replay_complete",
        "scope": "supplementary exposed-development citation equivalence, not original strict success",
        "predecessor_plan_sha256": receipt["plan_sha256"],
        "predecessor_results_sha256": receipt["results_sha256"],
        "predecessor_tree_sha256": sha256(before),
        "predecessor_mutated": False,
        "provider_calls_executed": 0,
        "denominator": len(requests),
        "source_cluster_count": 1,
        "original_analysis": receipt["analysis"],
        "role_digest_frame_equal_count": sum(d["role_digest_frame_equal"] for d in diagnostics),
        "role_digest_document_frame_equal_count": sum(
            d["role_digest_document_frame_equal"] for d in diagnostics
        ),
        "raw_resolution_matches_reference_count": sum(
            d["raw_resolution_matches_reference"] for d in diagnostics
        ),
        "citation_only_rejection_count": sum(d["citation_only_rejection"] for d in diagnostics),
        "citation_repaired_fact_count": sum(d["citation_repaired_fact_count"] for d in diagnostics),
        "document_text_expansion_count": sum(
            d["document_text_expansion_count"] for d in diagnostics
        ),
        "explicit_field_locator_substitution_count": sum(
            d["explicit_field_locator_substitution_count"] for d in diagnostics
        ),
        "normalized_exact_fact_frame_count": sum(
            bool(d["normalized_assessment"] and d["normalized_assessment"]["exact_fact_frame"])
            for d in diagnostics
        ),
    }


def _requests(predecessor: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    receipt, prior, manifest = _predecessor(predecessor)
    requests = []
    for request in prior:
        schema = citation_schema(request["payload"]["documents"])
        requests.append(
            {
                **request,
                "predecessor_request_id": request["request_id"],
                "request_id": sha256([PROMPT, schema, request["request_id"]]),
                "schema": schema,
            }
        )
    return receipt, requests, manifest


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
            raise ValueError("canonical-locator SDK wire differs or input exceeds token ceiling")
    return {
        "sdk_capture_count": len(captured),
        "wire_sha256": sha256(captured),
        "provider_calls_executed": 0,
    }


def _plan(predecessor: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt, requests, manifest = _requests(predecessor)
    base = Path(__file__).resolve().parents[1]
    plan = {
        "schema_version": "native-cache-citation-plan/v1",
        "predecessor_plan_sha256": receipt["plan_sha256"],
        "predecessor_results_sha256": receipt["results_sha256"],
        "predecessor_manifest": manifest,
        "code_sha256": {
            **original._code_identity(),
            **{
                name: file_sha256(base / name)
                for name in (
                    "evaluation/native_cache_citation.py",
                    "evaluation/native_cache_citation_experiment.py",
                    "evaluation/execution_contracts.py",
                )
            },
            "scripts/native_cache_citation.py": file_sha256(
                base.parents[1] / "scripts/native_cache_citation.py"
            ),
        },
        "request_frame_sha256": sha256(requests),
        "prompt_sha256": sha256(PROMPT),
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "maximum_provider_calls": len(requests),
        "maximum_reserved_cost_usd_at_frozen_rates": round(len(requests) * MAX_CALL_USD, 6),
        "cost_ceiling_usd": COST_CEILING_USD,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "sdk_retries": 0,
        "store": False,
        "provider_fields": original._read(predecessor / "plan.json")["provider_fields"],
        "withheld_fields": original._read(predecessor / "plan.json")["withheld_fields"],
        "source_cluster_count": 1,
        "correction": "explicit custom source locator and visible-ID x field syntax enum; strict scorer/resolver unchanged; no live normalization",
        "scientific_use": "new exposed-development interface check, not independent validation or semantic superiority",
        "failure_policy": "one attempt per slot; preserve all 10 slots; no automatic retries or rerun",
        "wire_audit": _audit_wires(requests),
    }
    if len(requests) != 10 or plan["maximum_reserved_cost_usd_at_frozen_rates"] > COST_CEILING_USD:
        raise ValueError("canonical-locator comparison exceeds the fixed census or ceiling")
    return plan, requests


def _disjoint(predecessor: Path, directory: Path) -> None:
    _private_dir(predecessor)
    if directory.resolve().is_relative_to(
        predecessor.resolve()
    ) or predecessor.resolve().is_relative_to(directory.resolve()):
        raise ValueError("new comparison and predecessor must be disjoint")


def prepare(*, predecessor: Path, directory: Path) -> dict[str, Any]:
    _disjoint(predecessor, directory)
    plan, _ = _plan(predecessor)
    _private_dir(directory, new=True)
    _write(directory / "plan.json", {**plan, "plan_sha256": sha256(plan)})
    return preflight(predecessor=predecessor, directory=directory)


def checked_plan(
    *, predecessor: Path, directory: Path, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _disjoint(predecessor, directory)
    _private_dir(directory)
    expected, requests = _plan(predecessor)
    saved = original._read(directory / "plan.json")
    if saved != {**expected, "plan_sha256": sha256(expected)} or (
        confirm_sha256 is not None and confirm_sha256 != sha256(expected)
    ):
        raise ValueError("predecessor, source, code, wire, plan or confirmation changed")
    return saved, requests


def preflight(*, predecessor: Path, directory: Path) -> dict[str, Any]:
    plan, requests = checked_plan(predecessor=predecessor, directory=directory)
    return {
        "status": "offline_native_cache_citation_preflight_pass",
        "plan_sha256": plan["plan_sha256"],
        "case_view_count": len(requests),
        "maximum_provider_calls": plan["maximum_provider_calls"],
        "maximum_reserved_cost_usd_at_frozen_rates": plan[
            "maximum_reserved_cost_usd_at_frozen_rates"
        ],
        "cost_ceiling_usd": COST_CEILING_USD,
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "wire_audit": plan["wire_audit"],
        "predecessor_mutated": False,
        "new_live_efficacy_measured": False,
    }


def _row(request: dict[str, Any], call: DevelopmentCall) -> dict[str, Any]:
    if (
        call.estimated_cost_usd > MAX_CALL_USD
        or call.input_tokens > MAX_INPUT_TOKENS
        or call.output_tokens > MAX_OUTPUT_TOKENS
    ):
        raise ValueError("caller exceeded the frozen resource contract")
    diagnostic = diagnose_citations(
        request["payload"]["documents"],
        (call.payload_json or "").encode() if call.status == "completed" else b"{}",
        normalize=False,
    )
    return {
        **{
            key: request[key] for key in ("request_id", "predecessor_request_id", "case_id", "view")
        },
        "call": call.model_dump(mode="json"),
        "assessment": original._assess(request, call),
        "content_diagnostics": {
            key: diagnostic[key]
            for key in (
                "role_digest_frame_equal",
                "role_digest_document_frame_equal",
                "raw_resolution_matches_reference",
            )
        },
    }


def _receipt(plan: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "native_cache_citation_development_complete",
        "plan_sha256": plan["plan_sha256"],
        "predecessor_results_sha256": plan["predecessor_results_sha256"],
        "results_sha256": sha256(rows),
        "analysis": original._analysis(rows),
        "content_diagnostics": {
            key + "_count": sum(row["content_diagnostics"][key] for row in rows)
            for key in (
                "role_digest_frame_equal",
                "role_digest_document_frame_equal",
                "raw_resolution_matches_reference",
            )
        },
        "predecessor_mutated": False,
        "live_citation_normalization_applied": False,
    }


def execute(
    *,
    predecessor: Path,
    directory: Path,
    confirm_sha256: str,
    caller: DevelopmentCaller,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    plan, requests = checked_plan(
        predecessor=predecessor, directory=directory, confirm_sha256=confirm_sha256
    )
    if (directory / "lease.json").exists() or (directory / "results").exists():
        raise ValueError("attempt exists; verify rather than calling the provider again")
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
        if progress:
            progress(
                {
                    "status": "native_cache_citation_progress",
                    "completed_requests": len(rows),
                    "maximum_requests": len(requests),
                }
            )
    checked_plan(predecessor=predecessor, directory=directory)
    _write(directory / "receipt.json", _receipt(plan, rows))
    return verify(predecessor=predecessor, directory=directory)


def verify(*, predecessor: Path, directory: Path) -> dict[str, Any]:
    plan, requests = checked_plan(predecessor=predecessor, directory=directory)
    if original._read(directory / "lease.json") != {
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
        saved = original._read(results / f"{index:02d}.json")
        expected = _row(request, DevelopmentCall.model_validate(saved["call"]))
        if saved != expected:
            raise ValueError("response, request binding or strict assessment changed")
        rows.append(expected)
    receipt = _receipt(plan, rows)
    if original._read(directory / "receipt.json") != receipt:
        raise ValueError("receipt does not reproduce from the retained response census")
    return receipt
