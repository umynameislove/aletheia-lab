"""Complete offline lifecycle, SDK wire, immutable identities and failure census."""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation import sqlite_evidence_transfer as study
from aletheia_lab.evaluation import warrant_development_live
from aletheia_lab.evaluation.sqlite_evidence_extraction import parser_facts
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD, MAX_INPUT_TOKENS

ROOT = Path(__file__).resolve().parents[2]


class Caller:
    def __init__(self, mode="valid"):
        self.mode, self.calls = mode, []

    def invoke(self, *, prompt, payload, schema):
        self.calls.append((prompt, deepcopy(payload), deepcopy(schema)))
        if self.mode == "raise":
            raise RuntimeError("private exception text must never escape")
        facts = parser_facts(payload["documents"])
        if self.mode == "aliases":
            facts[0]["pointer"] = "/documents/selection/text"
        return DevelopmentCall(
            status="technical_failure" if self.mode == "technical" else "completed",
            payload_json=None
            if self.mode == "technical"
            else "{}"
            if self.mode == "invalid"
            else json.dumps({"schema_version": "source-evidence-proposal/v1", "facts": facts}),
            input_tokens=MAX_INPUT_TOKENS + 1 if self.mode == "overbudget" else 100,
            output_tokens=10,
            estimated_cost_usd=0.00028,
            latency_seconds=0.1,
            provider_attempted=self.mode != "technical",
        )


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sqlite-study") / "prepared"
    report = study.prepare(root=ROOT, directory=directory)
    return directory, report


@pytest.fixture
def copy_study(tmp_path, prepared):
    original, report = prepared
    directory = tmp_path / "study"
    shutil.copytree(original, directory)
    return directory, deepcopy(report)


def tree(directory):
    return {
        path.relative_to(directory).as_posix(): file_sha256(path)
        for path in directory.rglob("*")
        if path.is_file()
    }


def tamper(path, edit):
    value = json.loads(path.read_bytes())
    edit(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_prepare_binds_reference_selection_and_exact_minimized_wire(prepared):
    directory, report = prepared
    plan, requests = study.checked_plan(directory=directory)
    assert (
        report["parser_exact_reference_count"]
        == report["request_count"]
        == report["maximum_provider_calls"]
        == 10
    )
    assert report["case_count"] == 5 and report["source_cluster_count"] == 1
    assert report["wire_audit"]["sdk_capture_count"] == 10
    assert report["wire_audit"]["distinct_visible_wire_count"] == 6
    assert report["wire_audit"]["provider_calls_executed"] == 0
    assert report["maximum_reserved_cost_usd_at_frozen_rates"] == 0.24576
    assert report["cost_ceiling_usd"] == 0.25
    assert not report["llm_transfer_measured"] and not report["execution_authorized"]
    assert plan["sdk_retries"] == 0 and plan["store"] is False
    assert len({request["request_id"] for request in requests}) == 10
    assert requests[0]["payload"] == requests[2]["payload"] == requests[4]["payload"]
    for request in requests:
        assert parser_facts(request["payload"]["documents"]) == request["reference"]["facts"]
        visible = json.dumps([request["payload"], request["schema"]])
        for withheld in (
            "returned_hex",
            "case-",
            "with_consumer",
            "trace_only",
            "restoration",
            "reference",
            "evaluation-v1",
            "/Users/",
            "frozen_artifacts",
        ):
            assert withheld not in visible
    assert not (directory / "lease.json").exists()


def test_selection_is_saved_before_evaluation_source_generation(tmp_path, monkeypatch):
    directory = tmp_path / "study"
    original = study.generate_source
    observed = []

    def generate():
        observed.append((directory / "selection.json").is_file())
        return original()

    monkeypatch.setattr(study, "generate_source", generate)
    study.prepare(root=ROOT, directory=directory)
    assert observed and all(observed)


def test_independent_reference_detects_parser_locator_bug(prepared, monkeypatch):
    directory, _ = prepared
    original = study.parser_facts

    def wrong_locator(docs):
        facts = original(docs)
        facts[0]["pointer"] = "/documents/selection/text"
        return facts

    monkeypatch.setattr(study, "parser_facts", wrong_locator)
    with pytest.raises(ValueError, match="independent full cited-frame"):
        study.request_frame(directory)


def test_full_fake_execution_and_offline_receipt_replay(copy_study):
    directory, report = copy_study
    before = tree(directory)
    caller, progress = Caller(), []
    receipt = study.execute(
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=caller,
        progress=progress.append,
    )
    analysis = receipt["analysis"]
    assert analysis["status_counts"] == {"admitted": 10}
    assert analysis["exact_fact_frame_count"] == 10
    assert analysis["paired_exact_rate_difference_llm_minus_parser"] == 0
    assert analysis["content_frame_equal_count"] == 10
    assert analysis["guarded_resolution_states"] == {"ambiguous": 5, "identified": 5, "conflict": 0}
    assert (
        analysis["unwarranted_singleton_count"]
        == analysis["visible_resolution_violation_count"]
        == 0
    )
    assert analysis["source_cluster_count"] == 1 and analysis["distinct_visible_wire_count"] == 6
    assert analysis["provider_attempt_count"] == len(caller.calls) == 10
    assert analysis["cost_usd_at_frozen_rates"] == 0.0028
    assert len(progress) == 10
    after = tree(directory)
    assert all(after[name] == digest for name, digest in before.items())
    assert study.verify(directory=directory) == receipt
    replay = Caller()
    with pytest.raises(ValueError, match="attempt exists"):
        study.execute(directory=directory, confirm_sha256=report["plan_sha256"], caller=replay)
    assert replay.calls == []


@pytest.mark.parametrize(
    "mode,expected_status,attempts",
    [
        ("invalid", "invalid_proposal", 10),
        ("aliases", "rejected", 10),
        ("technical", "technical_failure", 0),
        ("raise", "technical_failure", 10),
    ],
    ids=["invalid", "locator", "no-attempt", "exception"],
)
def test_failures_stay_in_complete_denominator(copy_study, mode, expected_status, attempts):
    directory, report = copy_study
    receipt = study.execute(
        directory=directory, confirm_sha256=report["plan_sha256"], caller=Caller(mode)
    )
    analysis = receipt["analysis"]
    assert analysis["denominator"] == 10
    assert analysis["status_counts"] == {expected_status: 10}
    assert analysis["exact_fact_frame_count"] == 0
    assert analysis["paired_exact_rate_difference_llm_minus_parser"] == -1
    assert analysis["provider_attempt_count"] == attempts
    assert study.verify(directory=directory) == receipt
    if mode == "raise":
        assert analysis["cost_usd_at_frozen_rates"] == round(10 * MAX_CALL_USD, 6)
        assert "private exception text" not in (directory / "receipt.json").read_text()
    if mode == "aliases":
        assert analysis["citation_only_rejection_count"] == 10
        assert analysis["visible_resolution_violation_count"] == 0


@pytest.mark.parametrize(
    "target",
    ["source", "selection", "plan", "confirmation", "runtime", "code"],
    ids=lambda value: value,
)
def test_changed_inputs_fail_before_any_call(copy_study, monkeypatch, target):
    directory, report = copy_study
    confirmation = report["plan_sha256"]
    if target == "source":
        tamper(
            directory / "source.json",
            lambda value: value["cases"][0]["reference"].update(status="binding_fault"),
        )
    elif target == "selection":
        tamper(
            directory / "selection.json",
            lambda value: value.update(precision="population inference"),
        )
    elif target == "plan":
        tamper(directory / "plan.json", lambda value: value.update(cost_ceiling_usd=5))
    elif target == "confirmation":
        confirmation = "f" * 64
    elif target == "runtime":
        original = study.producer_identity
        monkeypatch.setattr(
            study, "producer_identity", lambda: {**original(), "sqlite_version": "changed"}
        )
    else:
        monkeypatch.setattr(study, "_code_identity", lambda: {"changed.py": "f" * 64})
    caller = Caller()
    with pytest.raises(ValueError):
        study.execute(directory=directory, confirm_sha256=confirmation, caller=caller)
    assert caller.calls == [] and not (directory / "lease.json").exists()


@pytest.mark.parametrize(
    "target",
    ["call", "assessment", "request", "receipt", "lease", "missing", "extra"],
    ids=lambda value: value,
)
def test_tampered_or_incomplete_results_fail_independent_verify(copy_study, target):
    directory, report = copy_study
    study.execute(directory=directory, confirm_sha256=report["plan_sha256"], caller=Caller())
    if target == "call":
        tamper(directory / "results/00.json", lambda value: value["call"].update(payload_json="{}"))
    elif target == "assessment":
        tamper(
            directory / "results/00.json",
            lambda value: value["assessment"].update(exact_fact_frame=False),
        )
    elif target == "request":
        tamper(directory / "results/00.json", lambda value: value.update(request_id="changed"))
    elif target == "receipt":
        tamper(
            directory / "receipt.json",
            lambda value: value["analysis"].update(exact_fact_frame_count=11),
        )
    elif target == "lease":
        tamper(directory / "lease.json", lambda value: value.update(maximum_provider_calls=20))
    elif target == "missing":
        (directory / "results/00.json").unlink()
    else:
        (directory / "results/extra.json").write_text("{}")
    with pytest.raises(ValueError):
        study.verify(directory=directory)


def test_resource_violation_leaves_lease_without_rerun_authority(copy_study):
    directory, report = copy_study
    with pytest.raises(ValueError, match="resources"):
        study.execute(
            directory=directory, confirm_sha256=report["plan_sha256"], caller=Caller("overbudget")
        )
    assert (directory / "lease.json").exists()
    replay = Caller()
    with pytest.raises(ValueError):
        study.execute(directory=directory, confirm_sha256=report["plan_sha256"], caller=replay)
    assert replay.calls == []


def test_cli_offline_and_execution_boundary(copy_study, monkeypatch, capsys):
    directory, report = copy_study
    namespace = runpy.run_path(str(ROOT / "scripts/sqlite_evidence_transfer.py"))
    constructed = []

    def forbidden(**kwargs):
        constructed.append(kwargs)
        raise AssertionError("network client must not be constructed")

    monkeypatch.setattr(warrant_development_live, "OpenAIDevelopmentCaller", forbidden)
    monkeypatch.setattr(
        sys, "argv", ["sqlite_evidence_transfer.py", "preflight", "--study-dir", str(directory)]
    )
    assert namespace["main"]() == 0
    assert json.loads(capsys.readouterr().out)["wire_audit"]["provider_calls_executed"] == 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sqlite_evidence_transfer.py",
            "execute",
            "--study-dir",
            str(directory),
            "--confirm-plan-sha256",
            "wrong",
        ],
    )
    assert namespace["main"]() == 1 and constructed == []
    capsys.readouterr()
    study.execute(directory=directory, confirm_sha256=report["plan_sha256"], caller=Caller())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sqlite_evidence_transfer.py",
            "execute",
            "--study-dir",
            str(directory),
            "--confirm-plan-sha256",
            report["plan_sha256"],
        ],
    )
    assert namespace["main"]() == 1 and constructed == []
    monkeypatch.setattr(
        sys, "argv", ["sqlite_evidence_transfer.py", "verify", "--study-dir", str(directory)]
    )
    assert namespace["main"]() == 0 and constructed == []


def test_private_destination_freshness_and_root(tmp_path):
    with pytest.raises(ValueError, match="root"):
        study.prepare(root=tmp_path, directory=tmp_path / "wrong-root")
    with pytest.raises(ValueError, match="outside"):
        study.prepare(root=ROOT, directory=ROOT / "not-permitted-study")
    existing = tmp_path / "already-there"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        study.prepare(root=ROOT, directory=existing)


def test_separate_process_hash_seed_stability(tmp_path):
    import os

    plans = []
    for seed in ("1", "104729"):
        directory = tmp_path / f"seed-{seed}"
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONHASHSEED=seed)
        env.pop("OPENAI_API_KEY", None)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/sqlite_evidence_transfer.py"),
                "prepare",
                "--root",
                str(ROOT),
                "--study-dir",
                str(directory),
            ],
            env=env,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        plans.append(json.loads(completed.stdout)["plan_sha256"])
    assert plans[0] == plans[1]
