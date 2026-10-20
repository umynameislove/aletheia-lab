"""Synthetic receipt, cached-frame and private-output integration checks."""

from __future__ import annotations

import copy
import importlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.diagnosis_main_measurement_impact import ClaimUnit, ImpactFrame
from aletheia_lab.evaluation.diagnosis_main_paired_pilot import (
    _blind_files,
    analyze_pilot,
    select_families,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("diagnosis_comparison_robustness")


@pytest.fixture
def source():
    families = tuple(f"f{index:02d}" for index in range(32))
    selected = select_families(families, b"s" * 32)
    frame = ImpactFrame(family_ids=families)
    outputs, rows, claims = [], [], []
    categories = {}
    for family in families:
        for condition in ("full", "missing_key", "noisy"):
            for arm in ("B1", "A3"):
                request = f"{family}:{condition}:{arm}"
                claim = request + ":claim"
                cell = family, condition, arm
                frame.units[claim] = ClaimUnit(
                    claim, request, family, arm, condition, "fully_supported", 1, True
                )
                frame.output_claims[request] = {claim}
                frame.requests_by_cell[cell] = request
                frame.output_losses[cell] = {0.0: 0.0, 0.5: 0.0}
                categories[request] = "claim_scored"
                if family not in selected:
                    continue
                outputs.append(
                    {
                        "family_id": family,
                        "condition": condition,
                        "arm": arm,
                        "request_id": request,
                        "category": "claim_scored",
                        "claim_ids": [claim],
                        "automatic_primary_loss": 0.0,
                        "automatic_partial_half_loss": 0.0,
                    }
                )
                rows.append(
                    {
                        "claim_id": claim,
                        "claim_text": "SYNTHETIC_PRIVATE_TEXT",
                        "visible_evidence": [
                            {"evidence_id": "e1", "kind": "log", "title": "Trace", "content": "ok"}
                        ],
                    }
                )
                claims.append(
                    {"claim_id": claim, "automatic_label": "fully_supported", "request_id": request}
                )
    files_a, map_a = _blind_files(slot="rater_1", rows=rows, key=b"k" * 32, guide="synthetic")
    files_b, map_b = _blind_files(slot="rater_2", rows=rows, key=b"k" * 32, guide="synthetic")
    first, second = (json.loads(files["submission-template.json"]) for files in (files_a, files_b))
    for submission in (first, second):
        submission["attestation"] = dict.fromkeys(submission["attestation"], True)
        for decision in submission["decisions"]:
            decision.update(
                support_label="fully_supported",
                evidence_ids_used=["e1"],
                rationale="SYNTHETIC_PRIVATE_RATIONALE supports this claim.",
            )
    coordinator = {
        "schema_version": "p5-impact-family-paired-pilot/v1",
        "packet_ids": {"rater_1": first["packet_id"], "rater_2": second["packet_id"]},
        "mapping": map_a + map_b,
        "selected_family_ids": list(selected),
        "outputs": outputs,
        "claims": claims,
        "population_family_ids": list(families),
        "sampling_seed_hex_coordinator_only": (b"s" * 32).hex(),
        "family_selection": "equal_probability_srswor_without_replacement",
        "frozen_primary_B1_minus_A3": 0.0,
        "frozen_partial_half_B1_minus_A3": 0.0,
    }
    adjudication = {
        "schema_version": "p5-impact-blind-adjudication/v1",
        "decisions": [],
        "attestation": None,
    }
    final = analyze_pilot(coordinator, first, second, adjudication)
    data = {
        "coordinator": coordinator,
        "first_submission": first,
        "second_submission": second,
        "adjudication": adjudication,
        "final_summary": final,
        "final_labels": {
            "rows": [
                {"source_claim_id": row["claim_id"], "human_final_label": "fully_supported"}
                for row in claims
            ]
        },
    }
    return frame, categories, data


def test_synthetic_paired_analysis_is_aggregate_only_and_deterministic(cli, source) -> None:
    frame, categories, data = source
    before = copy.deepcopy(data)
    first = cli._analysis(frame, {}, data, categories)
    assert first == cli._analysis(frame, {}, data, categories)
    assert data == before
    encoded = json.dumps(first)
    for private in (
        "SYNTHETIC_PRIVATE",
        "source_claim_id",
        "blind_claim_id",
        "sampling_seed_hex",
        "packet_ids",
    ):
        assert private not in encoded
    assert first["provider_calls"] == 0
    assert (
        first["pilot_references"]["locked_final"]["primary"]["pilot_estimated_human_relabel_effect"]
        == 0
    )
    assert first["conditional_regions"]["0.0"]["reference_union"]["scored_core_claims"] == 192
    assert (
        first["conditional_regions"]["0.0"]["reference_union"]["reference_observed_core_claims"]
        == 48
    )


@pytest.mark.parametrize(
    "change", ("baseline", "selected_loss", "automatic_label", "category", "duplicate", "selection")
)
def test_rehashed_coordinator_cannot_hide_changed_frozen_source(cli, source, change: str) -> None:
    frame, categories, data = source
    coord = data["coordinator"]
    if change == "baseline":
        coord["frozen_primary_B1_minus_A3"] = 0.1
    elif change == "selected_loss":
        coord["outputs"][0]["automatic_primary_loss"] = 1.0
    elif change == "automatic_label":
        coord["claims"][0]["automatic_label"] = "unsupported"
    elif change == "category":
        coord["outputs"][0]["category"] = "actual_abstention"
        coord["outputs"][0]["claim_ids"] = []
    elif change == "duplicate":
        coord["outputs"].append(copy.deepcopy(coord["outputs"][0]))
    else:
        coord["selected_family_ids"] = coord["selected_family_ids"][1:] + ["foreign"]
    with pytest.raises(ValueError):
        cli._analysis(frame, {}, data, categories)


def test_final_labels_must_match_returned_human_decisions_not_ai_repair(cli, source) -> None:
    frame, categories, data = source
    data["final_labels"]["rows"][0]["human_final_label"] = "unsupported"
    with pytest.raises(ValueError, match="returned agreement"):
        cli._analysis(frame, {}, data, categories)


def _locked_hashes(cli) -> tuple[dict[str, bytes], dict[str, Any]]:
    raw = {
        name: name.encode()
        for name in (
            "coordinator",
            "adjudication_packet",
            "final_labels",
            "adjudication",
            "first_submission",
            "second_submission",
            "recovery_input",
            "recovery_report",
            "census",
            "scoring_receipt",
        )
    }
    coord_hash, packet_hash = cli._sha(raw["coordinator"]), cli._sha(raw["adjudication_packet"])
    source = {
        key: cli._sha(raw[name])
        for key, name in (
            ("analysis_input_byte_sha256", "recovery_input"),
            ("analysis_report_byte_sha256", "recovery_report"),
            ("private_census_byte_sha256", "census"),
            ("scoring_receipt_byte_sha256", "scoring_receipt"),
        )
    }
    final = {
        "input_byte_sha256": {
            name: cli._sha(raw[name])
            for name in ("coordinator", "first_submission", "second_submission")
        },
        "adjudication_packet_sha256": packet_hash,
        "human_final_labels_byte_sha256": cli._sha(raw["final_labels"]),
        "source_byte_sha256": source,
    }
    final["input_byte_sha256"]["blind_adjudication"] = cli._sha(raw["adjudication"])
    data = {
        "coordinator": {"source_byte_sha256": source},
        "pilot_receipt": {"coordinator_sha256": coord_hash},
        "final_summary": final,
        "preliminary": {
            "coordinator_byte_sha256": coord_hash,
            "adjudication_packet_sha256": packet_hash,
            "source_byte_sha256": source,
            "raw_submission_byte_sha256": {
                "rater_1": cli._sha(raw["first_submission"]),
                "rater_2": cli._sha(raw["second_submission"]),
            },
        },
    }
    return raw, data


@pytest.mark.parametrize(
    "input_name", ("first_submission", "final_labels", "recovery_input", "adjudication_packet")
)
def test_changed_private_bytes_are_rejected_against_receipts(cli, input_name: str) -> None:
    raw, data = _locked_hashes(cli)
    cli._verify_pilot_bytes(raw, data)
    raw[input_name] += b"tampered"
    with pytest.raises(ValueError):
        cli._verify_pilot_bytes(raw, data)


def test_duplicate_json_fields_are_not_silently_resolved(cli) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        cli._json(b'{"support_label":"fully_supported","support_label":"unsupported"}')


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e999"])
def test_nonfinite_json_cannot_enter_the_cached_measurement_frame(cli, value) -> None:
    with pytest.raises(ValueError, match="nonfinite"):
        cli._json(('{"nested":{"untrusted_number":' + value + "}}").encode())


def test_private_output_is_exclusive_and_cannot_enter_repository(cli, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    summary = {"status": "secondary_offline_analysis_complete", "provider_calls": 0}
    output = tmp_path / "summary.json"
    digest = cli.write_private_summary(repo, output, summary)
    original = output.read_bytes()
    assert digest == cli._sha(original)
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        cli.write_private_summary(repo, output, {"changed": True})
    assert output.read_bytes() == original
    with pytest.raises(ValueError):
        cli.write_private_summary(repo, repo / "private.json", summary)


def test_missing_input_subprocess_fails_without_private_path_or_traceback(tmp_path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    private = memory / "SYNTHETIC_PRIVATE_PATH.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/diagnosis_comparison_robustness.py"),
            "--memory-root",
            str(memory),
            "--first-submission",
            str(private),
            "--second-submission",
            str(private),
            "--output",
            str(tmp_path / "summary.json"),
        ],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["status"] == "failed_closed"
    assert "SYNTHETIC_PRIVATE" not in completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    assert not (tmp_path / "summary.json").exists()
