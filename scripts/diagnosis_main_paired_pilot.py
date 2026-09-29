#!/usr/bin/env python3
"""Prepare and later analyze the private P5 family-paired human pilot offline."""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from aletheia_lab.diagnosis.main_execution import MainBatchResult
from aletheia_lab.diagnosis.main_pipeline import load_private_main_packet
from aletheia_lab.diagnosis.main_runtime import load_main_runtime_inputs
from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    load_main_scoring_contract,
    prepare_main_scoring,
)
from aletheia_lab.evaluation.diagnosis_main_paired_pilot import (
    PairedPilotError,
    analyze_pilot,
    blind_adjudication_packet,
    build_pilot,
    json_bytes,
    select_families,
    sha256,
)

REPO = Path(__file__).resolve().parents[1]
GUIDE = """# Claim–evidence rating guide

Judge only the written claim against the evidence visible with that item. Do
not use outside facts, another rater's answer, AI, or the machine's label.
Read every evidence item, including structured JSON in `content`.

Choose exactly one label:

1. `contradicted`: visible evidence directly refutes an important part.
2. `unsupported`: no important part is established or directly refuted.
3. `partially_supported`: some important parts are established, others lack
   evidence, and none is directly refuted.
4. `fully_supported`: every important part is established by visible evidence.

Missing evidence is not contrary evidence. Plausibility, correlation, or
temporal order alone do not prove a causal claim. Check quantities, scope,
negation, `only`/`sole`, confidence, and the exact cited evidence location.
List only evidence IDs actually used. Any label except `unsupported` needs at
least one ID; `unsupported` may use none. Give an item-specific rationale of
at least 20 characters. If an item is unreadable, report its number to the
coordinator instead of guessing. Do not alter claim, evidence, or IDs.
"""


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PairedPilotError("an immutable private input is unavailable")
    return path.read_bytes()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(_read(path))
    if not isinstance(value, dict):
        raise PairedPilotError("private JSON must contain an object")
    return value


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def _source(
    memory: Path,
) -> tuple[
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisReport,
    Any,
    dict[str, str],
]:
    private_packet = memory / "diagnosis-main-p4-census-v1/private-census-packet.json"
    recovery = memory / "diagnosis-main-recovery-scoring-v1"
    analysis_input_bytes = _read(recovery / "analysis-input.json")
    receipt = _read_json(recovery / "receipt.json")
    if (
        receipt.get("status") != "forward_relation_scoring_terminalized"
        or receipt.get("receipt_sha256")
        != "d293e0dfb5fdcc86386d02c5bc17c79b6f8a8ad66393d4caeeaad9fc468e9acb"
        or receipt.get("analysis_input_sha256")
        != "cb5d7e035cb1746575e72886fee703d770b0d956b714409e5715c898e3511af8"
    ):
        raise PairedPilotError("scored recovery receipt is not the locked source")
    plan = DiagnosisMainAnalysisPlan.model_validate_json(
        _read(REPO / "configs/evaluation/diagnosis_main_analysis_plan_v3.json")
    )
    census_raw = _read_json(private_packet)["analysis_census"]
    census = DiagnosisMainAnalysisCensus.model_validate_json(json.dumps(census_raw))
    analysis_input = DiagnosisMainAnalysisInput.model_validate_json(analysis_input_bytes)
    report = DiagnosisMainAnalysisReport.model_validate_json(
        _read(memory / "diagnosis-main-recovery-analysis-v1/analysis-report.json")
    )
    packet = load_private_main_packet(REPO, private_packet)
    runtime, fairness, response = load_main_runtime_inputs(REPO)
    scoring = load_main_scoring_contract(REPO, runtime_contract=runtime, response_contract=response)
    prior_recovery = memory / "diagnosis-main-technical-recovery-v2"
    batch = MainBatchResult.model_validate_json(_read(prior_recovery / "main-batch-result.json"))
    preparation = prepare_main_scoring(
        packet=packet,
        runtime_contract=runtime,
        response_contract=response,
        fairness_freeze=fairness,
        scoring_contract=scoring,
        batch_result=batch,
        store_root=prior_recovery / "main-store",
    )
    sources = {
        "analysis_input_byte_sha256": sha256(analysis_input_bytes),
        "analysis_report_byte_sha256": sha256(
            _read(memory / "diagnosis-main-recovery-analysis-v1/analysis-report.json")
        ),
        "private_census_byte_sha256": sha256(_read(private_packet)),
        "scoring_receipt_byte_sha256": sha256(_read(recovery / "receipt.json")),
    }
    return plan, census, analysis_input, report, preparation, sources


def _load_pilot(pilot_dir: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    receipt = _read_json(pilot_dir / "receipt.json")
    coordinator_bytes = _read(pilot_dir / "coordinator-only.json")
    deliveries = {name: _read(pilot_dir / name) for name in ("rater_1.zip", "rater_2.zip")}
    if receipt.get("coordinator_sha256") != sha256(coordinator_bytes) or receipt.get(
        "zip_sha256"
    ) != {name: sha256(body) for name, body in deliveries.items()}:
        raise PairedPilotError("pilot files differ from their private receipt")
    coordinator = json.loads(coordinator_bytes)
    if coordinator.get("blind_zip_sha256") != receipt["zip_sha256"] or tuple(
        coordinator["selected_family_ids"]
    ) != select_families(
        tuple(sorted(coordinator["population_family_ids"])),
        bytes.fromhex(coordinator["sampling_seed_hex_coordinator_only"]),
    ):
        raise PairedPilotError("sample selection or blind ZIP identity changed")
    return coordinator, deliveries


def _private_target(path: Path) -> None:
    if path.resolve(strict=False).is_relative_to(REPO.resolve()):
        raise PairedPilotError("private human artifacts cannot be written into repository")


def prepare(memory: Path, output_dir: Path) -> dict[str, Any]:
    _private_target(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise PairedPilotError("pilot destination already exists")
    plan, census, recovery_input, recovery_report, preparation, sources = _source(memory)
    seed = secrets.token_bytes(32)
    coordinator, deliveries = build_pilot(
        plan=plan,
        census=census,
        recovery_input=recovery_input,
        recovery_report=recovery_report,
        preparation=preparation,
        seed=seed,
        blind_key=secrets.token_bytes(32),
        guide=GUIDE,
    )
    coordinator["population_family_ids"] = sorted(family.family_id for family in census.families)
    coordinator["source_byte_sha256"] = sources
    coordinator_bytes = json_bytes(coordinator)
    receipt = {
        "schema_version": "p5-impact-family-paired-receipt/v1",
        "status": "blind_pilot_prepared_not_sent",
        "coordinator_sha256": sha256(coordinator_bytes),
        "zip_sha256": {name: sha256(body) for name, body in deliveries.items()},
        "selected_family_count": len(coordinator["selected_family_ids"]),
        "selected_scored_output_count": sum(
            item["category"] == "claim_scored" for item in coordinator["outputs"]
        ),
        "selected_scored_claim_count": len(coordinator["claims"]),
        "frozen_outcomes_mutated": False,
        "provider_calls": 0,
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p5-impact-pilot-", dir=output_dir.parent) as temp:
        temp_dir = Path(temp)
        _write_new(temp_dir / "coordinator-only.json", coordinator_bytes)
        for name, body in deliveries.items():
            _write_new(temp_dir / name, body)
        _write_new(temp_dir / "receipt.json", json_bytes(receipt))
        os.chmod(temp_dir, 0o700)
        if output_dir.exists():
            raise PairedPilotError("pilot destination appeared during preparation")
        os.rename(temp_dir, output_dir)
    return receipt


def verify(memory: Path, pilot_dir: Path) -> dict[str, Any]:
    coordinator, _ = _load_pilot(pilot_dir)
    for name, digest in coordinator["source_byte_sha256"].items():
        paths = {
            "analysis_input_byte_sha256": memory
            / "diagnosis-main-recovery-scoring-v1/analysis-input.json",
            "analysis_report_byte_sha256": memory
            / "diagnosis-main-recovery-analysis-v1/analysis-report.json",
            "private_census_byte_sha256": memory
            / "diagnosis-main-p4-census-v1/private-census-packet.json",
            "scoring_receipt_byte_sha256": memory
            / "diagnosis-main-recovery-scoring-v1/receipt.json",
        }
        if name not in paths or sha256(_read(paths[name])) != digest:
            raise PairedPilotError("locked source bytes changed")
    return {
        "status": "blind_pilot_verified_not_sent",
        "selected_family_count": len(coordinator["selected_family_ids"]),
        "selected_scored_claim_count": len(coordinator["claims"]),
        "provider_calls": 0,
    }


def adjudication(
    pilot_dir: Path, first_path: Path, second_path: Path, output: Path
) -> dict[str, Any]:
    _private_target(output)
    coordinator, deliveries = _load_pilot(pilot_dir)
    first_bytes, second_bytes = _read(first_path), _read(second_path)
    first, second = json.loads(first_bytes), json.loads(second_bytes)
    if not isinstance(first, dict) or not isinstance(second, dict):
        raise PairedPilotError("human submissions must contain JSON objects")
    files = blind_adjudication_packet(coordinator, deliveries["rater_1.zip"], first, second)
    from aletheia_lab.evaluation.diagnosis_main_paired_pilot import _zip

    files["submission-lock.json"] = json_bytes(
        {
            "schema_version": "p5-impact-submission-lock/v1",
            "coordinator_sha256": sha256(_read(pilot_dir / "coordinator-only.json")),
            "first_submission_byte_sha256": sha256(first_bytes),
            "second_submission_byte_sha256": sha256(second_bytes),
        }
    )
    body = _zip(files)
    _write_new(output, body)
    return {
        "status": "blind_disagreement_packet_prepared_not_sent",
        "disagreement_count": len(json.loads(files["items.json"])["items"]),
        "zip_sha256": sha256(body),
    }


def analyze(
    pilot_dir: Path,
    first_path: Path,
    second_path: Path,
    adjudication_packet: Path,
    adjudication_path: Path,
    output: Path,
) -> dict[str, Any]:
    _private_target(output)
    coordinator, _ = _load_pilot(pilot_dir)
    first_bytes, second_bytes = _read(first_path), _read(second_path)
    with zipfile.ZipFile(io.BytesIO(_read(adjudication_packet))) as archive:
        lock = json.loads(archive.read("submission-lock.json"))
        blind_template = json.loads(archive.read("adjudication-template.json"))
    if lock != {
        "schema_version": "p5-impact-submission-lock/v1",
        "coordinator_sha256": sha256(_read(pilot_dir / "coordinator-only.json")),
        "first_submission_byte_sha256": sha256(first_bytes),
        "second_submission_byte_sha256": sha256(second_bytes),
    }:
        raise PairedPilotError("rated submissions changed after disagreement packet")
    adjudication_bytes = _read(adjudication_path)
    adjudication_json = json.loads(adjudication_bytes)
    if {decision["blind_claim_id"] for decision in blind_template["decisions"]} != {
        decision["blind_claim_id"] for decision in adjudication_json["decisions"]
    }:
        raise PairedPilotError("blind disagreement packet and returned decisions differ")
    result = analyze_pilot(
        coordinator,
        json.loads(first_bytes),
        json.loads(second_bytes),
        adjudication_json,
    )
    result["input_byte_sha256"] = {
        "coordinator": sha256(_read(pilot_dir / "coordinator-only.json")),
        "first_submission": sha256(first_bytes),
        "second_submission": sha256(second_bytes),
        "blind_adjudication": sha256(adjudication_bytes),
    }
    encoded = json_bytes(result)
    _write_new(output, encoded)
    return {
        "status": result["status"],
        "sampled_family_count": result["sampled_family_count"],
        "analysis_sha256": sha256(encoded),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--memory-root", type=Path, required=True)
    prep.add_argument("--output-dir", type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("--memory-root", type=Path, required=True)
    check.add_argument("--pilot-dir", type=Path, required=True)
    adj = sub.add_parser("adjudication")
    adj.add_argument("--pilot-dir", type=Path, required=True)
    adj.add_argument("--first-submission", type=Path, required=True)
    adj.add_argument("--second-submission", type=Path, required=True)
    adj.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("analyze")
    run.add_argument("--pilot-dir", type=Path, required=True)
    run.add_argument("--first-submission", type=Path, required=True)
    run.add_argument("--second-submission", type=Path, required=True)
    run.add_argument("--adjudication-packet", type=Path, required=True)
    run.add_argument("--blind-adjudication", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.memory_root.resolve(strict=True), args.output_dir)
        elif args.command == "verify":
            result = verify(args.memory_root.resolve(strict=True), args.pilot_dir)
        elif args.command == "adjudication":
            result = adjudication(
                args.pilot_dir,
                args.first_submission,
                args.second_submission,
                args.output,
            )
        else:
            result = analyze(
                args.pilot_dir,
                args.first_submission,
                args.second_submission,
                args.adjudication_packet,
                args.blind_adjudication,
                args.output,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
