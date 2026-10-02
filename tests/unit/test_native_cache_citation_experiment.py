"""Separate canonical-locator calls never overwrite or relabel the old experiment."""

from __future__ import annotations

import json
import runpy
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation import native_cache_citation_experiment as study
from aletheia_lab.evaluation import native_cache_experiment as original
from aletheia_lab.evaluation import warrant_development_live
from aletheia_lab.evaluation.native_cache_citation import PROMPT, citation_schema
from aletheia_lab.evaluation.native_cache_extraction import parser_facts
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD

ROOT = Path(__file__).resolve().parents[2]


class Caller:
    def __init__(self, mode="valid"):
        self.mode, self.calls = mode, []

    def invoke(self, *, prompt, payload, schema):
        self.calls.append((prompt, deepcopy(payload), deepcopy(schema)))
        if self.mode == "raise":
            raise RuntimeError("private exception must not escape")
        facts = parser_facts(payload["documents"])
        if self.mode == "aliases":
            for fact in facts:
                fact["pointer"] = fact["pointer"].rsplit("/", 1)[0] + "/text"
        elif self.mode == "wrong-role":
            facts[0]["kind"] = "loaded_endpoint"
        return DevelopmentCall(
            status="completed",
            input_tokens=100,
            output_tokens=10,
            payload_json="{}"
            if self.mode == "invalid"
            else json.dumps({"schema_version": "source-evidence-proposal/v1", "facts": facts}),
            estimated_cost_usd=0.00028,
            latency_seconds=0.1,
        )


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    parent = tmp_path_factory.mktemp("citation-study")
    predecessor, directory = parent / "original", parent / "corrected"
    report = original.prepare(root=ROOT, directory=predecessor)
    original.execute(
        directory=predecessor, confirm_sha256=report["plan_sha256"], caller=Caller("aliases")
    )
    corrected = study.prepare(predecessor=predecessor, directory=directory)
    return predecessor, directory, corrected


@pytest.fixture
def copy_study(tmp_path, prepared):
    predecessor, directory, report = prepared
    old, new = tmp_path / "original", tmp_path / "corrected"
    shutil.copytree(predecessor, old)
    shutil.copytree(directory, new)
    return old, new, deepcopy(report)


def tree(directory):
    return {
        p.relative_to(directory).as_posix(): file_sha256(p)
        for p in directory.rglob("*")
        if p.is_file()
    }


def test_cached_replay_preserves_all_bytes_and_separates_primary_from_diagnostics(prepared):
    predecessor, _, _ = prepared
    before = tree(predecessor)
    report = study.replay(predecessor=predecessor)
    assert report["provider_calls_executed"] == 0
    assert report["original_analysis"]["status_counts"] == {"rejected": 10}
    assert report["original_analysis"]["exact_fact_frame_count"] == 0
    assert report["role_digest_frame_equal_count"] == 10
    assert report["role_digest_document_frame_equal_count"] == 10
    assert report["raw_resolution_matches_reference_count"] == 10
    assert report["citation_only_rejection_count"] == 10
    assert report["normalized_exact_fact_frame_count"] == 10
    assert report["document_text_expansion_count"] == 15
    assert report["explicit_field_locator_substitution_count"] == 0
    assert report["citation_repaired_fact_count"] == 15
    assert tree(predecessor) == before
    assert original.verify(directory=predecessor)["analysis"]["exact_fact_frame_count"] == 0


def test_new_plan_reuses_identical_inputs_and_changes_only_generation_contract(prepared):
    predecessor, directory, report = prepared
    plan, requests = study.checked_plan(predecessor=predecessor, directory=directory)
    prior = original.request_frame(predecessor)
    assert report["case_view_count"] == report["maximum_provider_calls"] == 10
    assert report["wire_audit"]["sdk_capture_count"] == 10
    assert report["wire_audit"]["provider_calls_executed"] == 0
    assert report["maximum_reserved_cost_usd_at_frozen_rates"] == 0.24576
    assert report["cost_ceiling_usd"] == 0.25
    assert not report["new_live_efficacy_measured"]
    assert plan["model_snapshot"] == "gpt-4.1-2025-04-14"
    for new, old in zip(requests, prior, strict=True):
        assert new["payload"] == old["payload"]
        assert new["request_id"] != old["request_id"]
        assert new["predecessor_request_id"] == old["request_id"]
        assert new["schema"] == citation_schema(old["payload"]["documents"])
        assert len(
            new["schema"]["properties"]["facts"]["items"]["properties"]["pointer"]["enum"]
        ) == 2 * len(old["payload"]["documents"])


@pytest.mark.parametrize(
    "mode,exact,content",
    [
        ("valid", 10, 10),
        ("aliases", 0, 10),
        ("invalid", 0, 0),
        ("wrong-role", 0, 0),
        ("raise", 0, 0),
    ],
    ids=["canonical", "strict-reject-alias", "invalid", "semantic-error", "provider-failure"],
)
def test_complete_lifecycle_keeps_strict_scorer_and_separate_content_counts(
    copy_study, mode, exact, content
):
    predecessor, directory, report = copy_study
    before = tree(predecessor)
    caller, progress = Caller(mode), []
    receipt = study.execute(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=caller,
        progress=progress.append,
    )
    assert len(caller.calls) == receipt["analysis"]["denominator"] == 10
    assert receipt["analysis"]["exact_fact_frame_count"] == exact
    assert receipt["content_diagnostics"]["role_digest_frame_equal_count"] == content
    assert receipt["content_diagnostics"]["role_digest_document_frame_equal_count"] == content
    assert not receipt["live_citation_normalization_applied"]
    assert not receipt["predecessor_mutated"] and tree(predecessor) == before
    assert progress[-1]["completed_requests"] == 10
    assert study.verify(predecessor=predecessor, directory=directory) == receipt
    assert all(prompt == PROMPT for prompt, _, _ in caller.calls)
    if mode == "raise":
        assert receipt["analysis"]["cost_usd_at_frozen_rates"] == round(10 * MAX_CALL_USD, 6)
        assert "private exception" not in (directory / "results/00.json").read_text()
    with pytest.raises(ValueError, match="attempt exists"):
        study.execute(
            predecessor=predecessor,
            directory=directory,
            confirm_sha256=report["plan_sha256"],
            caller=caller,
        )
    assert len(caller.calls) == 10


@pytest.mark.parametrize(
    "which",
    ["confirmation", "plan", "predecessor", "code"],
    ids=["bad-confirmation", "changed-plan", "changed-source", "changed-code"],
)
def test_changed_identity_blocks_before_provider(copy_study, which, monkeypatch):
    predecessor, directory, report = copy_study
    digest = report["plan_sha256"]
    if which == "confirmation":
        digest = "a" * 64
    elif which == "plan":
        plan = json.loads((directory / "plan.json").read_text())
        plan["cost_ceiling_usd"] = 1
        (directory / "plan.json").write_text(json.dumps(plan))
    elif which == "predecessor":
        (predecessor / "frozen-a.pkl").write_bytes(b"tampered")
    else:
        monkeypatch.setattr(study, "PROMPT", study.PROMPT + " changed")
    caller = Caller()
    with pytest.raises(ValueError):
        study.execute(
            predecessor=predecessor, directory=directory, confirm_sha256=digest, caller=caller
        )
    assert not caller.calls and not (directory / "lease.json").exists()


@pytest.mark.parametrize(
    "change",
    ["assessment", "binding", "diagnostics", "call", "receipt", "extra"],
    ids=["assessment", "binding", "diagnostics", "response", "receipt", "extra-slot"],
)
def test_verify_rejects_result_or_receipt_changes(copy_study, change):
    predecessor, directory, report = copy_study
    study.execute(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=Caller(),
    )
    path = directory / "results/00.json"
    row = json.loads(path.read_text())
    if change == "assessment":
        row["assessment"]["exact_fact_frame"] = False
    elif change == "binding":
        row["predecessor_request_id"] = "other"
    elif change == "diagnostics":
        row["content_diagnostics"]["role_digest_frame_equal"] = False
    elif change == "call":
        row["call"]["payload_json"] = "{}"
    elif change == "receipt":
        path = directory / "receipt.json"
        row = json.loads(path.read_text())
        row["analysis"]["denominator"] = 9
    else:
        path = directory / "results/extra.json"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        study.verify(predecessor=predecessor, directory=directory)


@pytest.mark.parametrize(
    "mode",
    ["original-dir", "bad-confirmation", "existing-attempt"],
    ids=["original-dir", "bad-confirmation", "existing-attempt"],
)
def test_cli_rejects_bad_gate_without_constructing_live_client(
    copy_study, mode, monkeypatch, capsys
):
    predecessor, directory, report = copy_study
    if mode == "existing-attempt":
        study.execute(
            predecessor=predecessor,
            directory=directory,
            confirm_sha256=report["plan_sha256"],
            caller=Caller(),
        )

    def forbidden(**kwargs):
        pytest.fail("live client must not be initialized")

    monkeypatch.setattr(warrant_development_live, "OpenAIDevelopmentCaller", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "execute",
            "--predecessor-dir",
            str(predecessor),
            "--study-dir",
            str(predecessor if mode == "original-dir" else directory),
            "--confirm-plan-sha256",
            "wrong" if mode == "bad-confirmation" else report["plan_sha256"],
        ],
    )
    main = runpy.run_path(str(ROOT / "scripts/native_cache_citation.py"))["main"]
    assert main() == 1
    assert "failed_closed" in capsys.readouterr().out


def test_private_boundary_and_same_directory_are_rejected(prepared):
    predecessor, _, _ = prepared
    with pytest.raises(ValueError):
        study.prepare(predecessor=predecessor, directory=predecessor)
    with pytest.raises(ValueError):
        study.prepare(predecessor=predecessor, directory=ROOT / "private-study")


def test_cli_replay_and_preflight_are_offline(prepared, monkeypatch, capsys):
    predecessor, directory, _ = prepared

    def forbidden(**kwargs):
        pytest.fail("no live client in offline CLI")

    monkeypatch.setattr(warrant_development_live, "OpenAIDevelopmentCaller", forbidden)
    main = runpy.run_path(str(ROOT / "scripts/native_cache_citation.py"))["main"]
    for command in ("replay", "preflight"):
        monkeypatch.setattr(
            sys,
            "argv",
            ["cli", command, "--predecessor-dir", str(predecessor), "--study-dir", str(directory)],
        )
        assert main() == 0
        assert json.loads(capsys.readouterr().out)["status"].startswith("offline_")
