"""Private development preparation, checkpointing, and exact-text reference export."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import (
    CONTENT_RETENTION_CRITERION,
    WARRANT_JUDGE_PROMPT,
    WRITER_PROMPT,
    DevelopmentCaller,
    WarrantCase,
    WarrantCaseResult,
    WarrantClaim,
    WarrantEvidence,
    WarrantReference,
    claim_sha256,
    output_sha256,
    run_warrant_case,
)
from aletheia_lab.evaluation.warrant_development_analysis import analyze_warrant_results
from aletheia_lab.filesystem import publish_immutable_file

MODEL = "gpt-4.1-2025-04-14"
DESTINATION = "https://api.openai.com/v1/chat/completions"
MAX_INPUT_TOKENS = 8192
MAX_OUTPUT_TOKENS = 1024
MAX_CALL_USD = (MAX_INPUT_TOKENS * 2 + MAX_OUTPUT_TOKENS * 8) / 1_000_000
CONCURRENCY = 4
SMOKE_CALL_CEILING = 9


def _read(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_000_000:
        raise ValueError("input must be a bounded regular private file")
    return json.loads(path.read_bytes())


def _write(path: Path, value: object) -> None:
    payload = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    publish_immutable_file(path, payload)


def _private_dir(directory: Path, *, new: bool = False) -> Path:
    repo = Path(__file__).resolve().parents[3]
    resolved = directory.resolve()
    if (
        directory.is_symlink()
        or resolved.is_relative_to(repo)
        or any((parent / ".git").exists() for parent in (resolved, *resolved.parents))
    ):
        raise ValueError("development data must remain outside the code checkout")
    if any(parent.is_symlink() for parent in directory.parents):
        raise ValueError("private path cannot use symlinked ancestors")
    if new:
        directory.mkdir(mode=0o700)
    if not directory.is_dir():
        raise ValueError("private development directory is absent")
    return directory


def _code_identity() -> dict[str, str]:
    directory = Path(__file__).parent
    return {
        name: file_sha256(directory / name)
        for name in (
            "warrant_development.py",
            "warrant_development_analysis.py",
            "warrant_development_io.py",
            "warrant_development_live.py",
            "claim_support_instrument.py",
            "claim_evidence_semantics.py",
            "claim_corpus_contracts.py",
            "../model_gateway/openai.py",
        )
    }


def _packet(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 16_000_000:
        raise ValueError("packet must be a bounded regular archive")
    with zipfile.ZipFile(path) as archive:
        item = archive.getinfo("main.json")
        if item.file_size > 4_000_000:
            raise ValueError("main packet exceeds the development input limit")
        value: dict[str, Any] = json.loads(archive.read(item))
    if value["phase"] != "main":
        raise ValueError("pilot cannot enter this development sample")
    return value


def prepare_warrant_development(*, memory_root: Path, output: Path) -> dict[str, Any]:
    """Use all 160 probability and 40 enriched cases; never select by improvement."""

    audit = memory_root / "p5-domain-human-audit-v1"
    sample_path = audit / "output/coordinator-only/sample-map.json"
    reference_path = audit / "adjudication-v1/analysis/private-item-level.json"
    sample = _read(sample_path)
    references = _read(reference_path)
    if (
        file_sha256(sample_path)
        != "cb0d3a7a0e49aebbc4c305ef52d4a3128ffe55a6213938100ce40a8a98e7cf7f"
    ):
        raise ValueError("sampling map differs from the completed human audit")
    if (
        file_sha256(reference_path)
        != "db769c91660b48c94b55091e248e11ba6c6738e877dd0e21a366e9771f7f6f45"
    ):
        raise ValueError("human-final development labels differ from the locked audit")
    packets = sorted((audit / "output/delivery").glob("*-220.zip"))
    matched = [(path, _packet(path)) for path in packets]
    packet_path, packet = next(pair for pair in matched if pair[1]["rater_slot"] == "rater_1")
    if (
        file_sha256(packet_path)
        != "857dfcdc19b6efc45e1795f801f116930f6644e74220f25010119dcc4a86dc01"
    ):
        raise ValueError("claim/evidence packet differs from the exact human-rated delivery")
    cases, seeds = _case_frame(packet, sample["mapping"], references["rows"])
    case_data = [case.model_dump(mode="json") for case in cases]
    plan = {
        "schema_version": "warrant-development-plan/v1",
        "experiment": "cached_claim_repair_by_judge_factorial",
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "case_count": len(cases),
        "components": {
            name: sum(c.component == name for c in cases) for name in ("probability", "enriched")
        },
        "case_frame_sha256": canonical_execution_sha256(case_data),
        "seed_reference_sha256": canonical_execution_sha256(seeds),
        "source_hashes": {
            "sampling_map": file_sha256(sample_path),
            "human_final": file_sha256(reference_path),
            "blind_packet": file_sha256(packet_path),
        },
        "code_sha256": _code_identity(),
        "writer_prompt_sha256": canonical_execution_sha256(WRITER_PROMPT),
        "judge_prompt_sha256": canonical_execution_sha256(WARRANT_JUDGE_PROMPT),
        "content_retention_criterion": CONTENT_RETENTION_CRITERION,
        "coverage_unit": "binary review of all evidence-supported material content in each sampled assertion; not full diagnosis-output recall",
        "temperature": 0.0,
        "seed": 731,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "maximum_provider_calls": 9 * len(cases) + SMOKE_CALL_CEILING,
        "worst_case_cost_usd_at_frozen_rates": round(
            (9 * len(cases) + SMOKE_CALL_CEILING) * MAX_CALL_USD, 6
        ),
        "concurrency": CONCURRENCY,
        "sdk_retries": 0,
        "timeout_seconds": 90.0,
        "store": False,
        "provider_fields": [
            "source_claim or claim_text and claim_type",
            "visible_evidence: evidence_id, kind, title, content",
        ],
        "excluded_provider_fields": [
            "human labels",
            "family/arm/condition IDs",
            "rater identities and rationale",
            "paths",
            "sampling component",
            "coverage units",
        ],
        "failure_policy": "synthetic contract smoke (up to nine calls) then bounded cohort; retain technical/invalid/empty outputs; no automatic rerun of paid calls",
        "reference_policy": "exact-text unchanged human label only; changed prose needs new development review; coverage unknown until reviewed",
        "scientific_use": "development selection only, not final validation or main-study rerun",
    }
    _private_dir(output, new=True)
    _write(output / "cases.json", case_data)
    _write(output / "seed-references.json", seeds)
    _write(output / "plan.json", plan)
    return {
        "status": "prepared_offline",
        "plan_sha256": canonical_execution_sha256(plan),
        **{
            key: plan[key]
            for key in (
                "case_count",
                "components",
                "maximum_provider_calls",
                "worst_case_cost_usd_at_frozen_rates",
                "destination",
                "model_snapshot",
            )
        },
        "provider_calls_executed": False,
    }


def _case_frame(
    packet: dict[str, Any], mapping: list[dict[str, Any]], references: list[dict[str, Any]]
) -> tuple[tuple[WarrantCase, ...], list[dict[str, Any]]]:
    slots = [
        row
        for row in mapping
        if row["phase"] == "main" and row["rater_slot"] == packet["rater_slot"]
    ]
    by_blind = {row["blind_claim_id"]: row for row in slots}
    by_source = {row["source_claim_id"]: row for row in references}
    if len(slots) != 200 or len(by_blind) != 200 or len(by_source) != 200 or len(references) != 200:
        raise ValueError("development frame must bind the exact 200-item audit")
    cases: list[WarrantCase] = []
    seeds: list[dict[str, Any]] = []
    for item in packet["items"]:
        row = by_blind[item["blind_claim_id"]]
        ref = by_source[row["source_claim_id"]]
        if any(
            row[key] != ref[key] for key in ("request_id", "family_id", "component", "claim_type")
        ):
            raise ValueError("packet and locked human source join differ")
        case = WarrantCase(
            case_id="dev-" + canonical_execution_sha256(row["source_claim_id"])[:24],
            family_id=row["family_id"],
            source_output_id=row["request_id"],
            component=row["component"],
            source_claim=WarrantClaim(claim_text=item["claim_text"], claim_type=row["claim_type"]),
            visible_evidence=tuple(
                WarrantEvidence.model_validate(e) for e in item["visible_evidence"]
            ),
            required_unit_ids=("bounded_source_material_content",),
        )
        cases.append(case)
        seeds.append(
            {
                "case_id": case.case_id,
                "claim_sha256": claim_sha256(case.source_claim),
                "evidence_sha256": case.evidence_sha256(),
                "label": ref["human_final_label"],
            }
        )
    if (
        len(cases) != 200
        or len({case.case_id for case in cases}) != 200
        or {name: sum(c.component == name for c in cases) for name in ("probability", "enriched")}
        != {"probability": 160, "enriched": 40}
    ):
        raise ValueError("sample census or component split differs")
    return tuple(cases), seeds


def checked_plan(
    directory: Path, *, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], tuple[WarrantCase, ...]]:
    _private_dir(directory)
    plan = _read(directory / "plan.json")
    case_data = _read(directory / "cases.json")
    seeds = _read(directory / "seed-references.json")
    if (
        (confirm_sha256 is not None and canonical_execution_sha256(plan) != confirm_sha256)
        or plan["code_sha256"] != _code_identity()
        or plan["case_frame_sha256"] != canonical_execution_sha256(case_data)
        or plan["seed_reference_sha256"] != canonical_execution_sha256(seeds)
    ):
        raise ValueError("approved plan, source cases, reference seed, or execution code changed")
    cases = tuple(WarrantCase.model_validate_json(json.dumps(item)) for item in case_data)
    if len(cases) != plan["case_count"] or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case census differs from approved plan")
    fixed = {
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "concurrency": CONCURRENCY,
        "temperature": 0.0,
        "seed": 731,
        "sdk_retries": 0,
        "store": False,
        "timeout_seconds": 90.0,
    }
    if (
        any(plan[key] != value for key, value in fixed.items())
        or plan["maximum_provider_calls"] != 9 * len(cases) + SMOKE_CALL_CEILING
        or not 1 <= len(cases) <= 200
    ):
        raise ValueError("plan execution settings differ from the implemented caller contract")
    return plan, cases


def execute_warrant_development(
    *,
    directory: Path,
    confirm_sha256: str,
    caller: DevelopmentCaller,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, Any]:
    plan, cases = checked_plan(directory, confirm_sha256=confirm_sha256)
    if (directory / "lease.json").exists() or (directory / "results").exists():
        raise FileExistsError("preserve prior paid attempt; automatic replay is forbidden")
    (directory / "results").mkdir(mode=0o700)
    _write(directory / "lease.json", {"plan_sha256": confirm_sha256, "automatic_replay": False})
    smoke = run_warrant_case(_synthetic_smoke_case(), caller)
    _write(directory / "synthetic-smoke.json", smoke.model_dump(mode="json"))
    smoke_calls = [
        call
        for writer in smoke.writers
        for call in ([writer.call] if writer.call else [])
        + [judgment.call for values in writer.judgments.values() for judgment in values]
    ]
    smoke_count = sum(call.provider_attempted for call in smoke_calls)
    if (
        smoke.writers[1].call is None
        or smoke.writers[1].call.status != "completed"
        or smoke.writers[1].output.status != "completed"
        or any(
            judgment.label is None
            for writer in smoke.writers
            for values in writer.judgments.values()
            for judgment in values
        )
    ):
        _write(
            directory / "smoke-stop.json",
            {
                "status": "smoke_failed_closed",
                "plan_sha256": confirm_sha256,
                "provider_invocation_count": smoke_count,
            },
        )
        return {"status": "smoke_failed_closed", "cohort_cases_executed": 0}
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        remaining = iter(cases)
        pending: set[Future[WarrantCaseResult]] = set()
        for _ in range(min(CONCURRENCY, len(cases))):
            pending.add(pool.submit(run_warrant_case, next(remaining), caller))
        count = 0
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                result = future.result()
                _write(
                    directory / "results" / (result.case.case_id + ".json"),
                    result.model_dump(mode="json"),
                )
                count += 1
                if progress is not None:
                    progress({"completed_cases": count, "total_cases": len(cases)})
                next_case = next(remaining, None)
                if next_case is not None:
                    pending.add(pool.submit(run_warrant_case, next_case, caller))
    initial = analyze_warrant_results(_results(directory))
    _write(
        directory / "execution-receipt.json",
        {
            "status": "development_execution_complete",
            "plan_sha256": canonical_execution_sha256(plan),
            "case_result_sha256": {
                case.case_id: file_sha256(directory / "results" / (case.case_id + ".json"))
                for case in cases
            },
            "references_complete": False,
            "provider_invocation_count": smoke_count + initial["provider_invocation_count"],
        },
    )
    _write(directory / "reference-template.json", reference_template(directory))
    _write(directory / "reference-review-packet.json", reference_review_packet(directory))
    return {**analyze_private_development(directory=directory), "synthetic_smoke_passed": True}


def _synthetic_smoke_case() -> WarrantCase:
    return WarrantCase(
        case_id="synthetic-contract-smoke",
        family_id="synthetic",
        source_output_id="synthetic",
        component="synthetic",
        source_claim=WarrantClaim(
            claim_text="The observed sample count was 2.", claim_type="evidence_statement"
        ),
        visible_evidence=(
            WarrantEvidence(
                evidence_id="smoke-e1",
                kind="metric",
                title="Synthetic count",
                content="The observed sample count was 2.",
            ),
        ),
        required_unit_ids=("sample_count",),
    )


def _results(directory: Path) -> tuple[WarrantCaseResult, ...]:
    plan, cases = checked_plan(directory)
    expected = {case.case_id + ".json" for case in cases}
    if {path.name for path in (directory / "results").iterdir()} != expected:
        raise ValueError("retained result census differs from approved cases")
    receipt_path = directory / "execution-receipt.json"
    if receipt_path.exists():
        receipt = _read(receipt_path)
        current = {
            case.case_id: file_sha256(directory / "results" / (case.case_id + ".json"))
            for case in cases
        }
        if (
            receipt["plan_sha256"] != canonical_execution_sha256(plan)
            or receipt["case_result_sha256"] != current
        ):
            raise ValueError("completed result bytes differ from the execution receipt")
    results = tuple(
        WarrantCaseResult.model_validate_json(
            json.dumps(_read(directory / "results" / (case.case_id + ".json")))
        )
        for case in cases
    )
    if tuple(result.case for result in results) != cases:
        raise ValueError("retained results differ from approved cases or evidence")
    return results


def reference_template(directory: Path) -> list[dict[str, Any]]:
    seeds = {item["case_id"]: item for item in _read(directory / "seed-references.json")}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for result in _results(directory):
        for writer in result.writers:
            key = result.case.case_id, output_sha256(writer.output)
            seed = seeds[result.case.case_id]
            labels = [
                seed["label"]
                if claim_sha256(claim) == seed["claim_sha256"]
                and result.case.evidence_sha256() == seed["evidence_sha256"]
                else None
                for claim in writer.output.claims
            ]
            complete = all(label is not None for label in labels)
            rows[key] = {
                "case_id": key[0],
                "output_sha256": key[1],
                "evidence_sha256": result.case.evidence_sha256(),
                "claim_sha256": [claim_sha256(claim) for claim in writer.output.claims],
                "labels": labels,
                "warranted_unit_ids": [] if not writer.output.claims else None,
                "basis": "locked_human"
                if complete and writer.output.claims
                else "development_review",
            }
    return list(rows.values())


def analyze_private_development(
    *, directory: Path, references_path: Path | None = None
) -> dict[str, Any]:
    if not (directory / "execution-receipt.json").is_file():
        raise ValueError("analysis requires a complete checkpointed development execution")
    results = _results(directory)
    raw = _read(references_path) if references_path is not None else reference_template(directory)
    seeds = {item["case_id"]: item for item in _read(directory / "seed-references.json")}
    for row in raw:
        if row["basis"] == "locked_human":
            seed = seeds.get(row["case_id"])
            if (
                seed is None
                or row["claim_sha256"] != [seed["claim_sha256"]]
                or row["evidence_sha256"] != seed["evidence_sha256"]
                or row["labels"] != [seed["label"]]
            ):
                raise ValueError("locked human label cannot be transferred to rewritten text")
    refs = tuple(
        WarrantReference.model_validate_json(json.dumps(row))
        for row in raw
        if all(label is not None for label in row["labels"])
    )
    return analyze_warrant_results(results, refs)


def reference_review_packet(directory: Path) -> dict[str, Any]:
    """No automatic/human labels, writer names, judge votes or source-family IDs."""

    results = _results(directory)
    items = {}
    for result in results:
        for writer in result.writers:
            key = result.case.case_id + "/" + output_sha256(writer.output)
            items[key] = {
                "case_id": result.case.case_id,
                "output_sha256": output_sha256(writer.output),
                "source_claim": result.case.source_claim.model_dump(mode="json"),
                "claims": [claim.model_dump(mode="json") for claim in writer.output.claims],
                "visible_evidence": [
                    item.model_dump(mode="json") for item in result.case.visible_evidence
                ],
                "required_unit_ids": list(result.case.required_unit_ids),
                "labels": [None for _ in writer.output.claims],
                "warranted_unit_ids": None,
            }
    return {
        "criterion": CONTENT_RETENTION_CRITERION,
        "instructions": "Rate every claim against visible evidence and mark whether all evidence-supported material source content was retained. Do not open reference-template.json, human labels or judge results during review. Partially-supported text can retain supported content; empty outputs retain none. The coordinator will check exact-text bindings after this development review, which is not fresh final human validation.",
        "items": sorted(
            items.values(), key=lambda item: canonical_execution_sha256(item["output_sha256"])
        ),
    }


def compile_review_references(*, directory: Path, reviewed_packet_path: Path) -> dict[str, Any]:
    """Bind completed blind development review to immutable outputs and locked seed labels."""

    original = reference_review_packet(directory)
    reviewed = _read(reviewed_packet_path)
    if not isinstance(reviewed, dict) or set(reviewed) != set(original):
        raise ValueError("review packet envelope differs")
    if any(reviewed[key] != original[key] for key in ("criterion", "instructions")):
        raise ValueError("review instructions or criterion changed")
    items = reviewed["items"]
    if not isinstance(items, list) or len(items) != len(original["items"]):
        raise ValueError("reviewed output census differs")
    templates = {
        (row["case_id"], row["output_sha256"]): row for row in reference_template(directory)
    }
    compiled: list[dict[str, Any]] = []
    for expected, item in zip(original["items"], items, strict=True):
        if not isinstance(item, dict) or set(item) != set(expected):
            raise ValueError("reviewed item fields differ")
        if any(
            item[key] != expected[key]
            for key in expected
            if key not in {"labels", "warranted_unit_ids"}
        ):
            raise ValueError("review changed claim, evidence, unit, or output identity")
        labels = item["labels"]
        units = item["warranted_unit_ids"]
        if (
            not isinstance(labels, list)
            or len(labels) != len(expected["claims"])
            or any(label is None for label in labels)
            or not isinstance(units, list)
            or (not expected["claims"] and bool(units))
        ):
            raise ValueError("development labels or content review are incomplete")
        key = item["case_id"], item["output_sha256"]
        template = templates[key]
        if any(
            prior is not None and prior != label
            for prior, label in zip(template["labels"], labels, strict=True)
        ):
            raise ValueError("blind review conflicts with an exact locked human label")
        row = {**template, "labels": labels, "warranted_unit_ids": units}
        compiled.append(
            WarrantReference.model_validate_json(json.dumps(row)).model_dump(mode="json")
        )
    analysis = analyze_warrant_results(
        _results(directory),
        tuple(WarrantReference.model_validate_json(json.dumps(row)) for row in compiled),
    )
    destination = directory / "reference-reviewed.json"
    _write(destination, compiled)
    return {
        "status": "development_references_compiled",
        "reference_output_count": len(compiled),
        "reference_sha256": file_sha256(destination),
        "analysis": analysis,
    }
