"""Actual native producer, isolated source capture, paid wire and immutable replay."""

from __future__ import annotations

import json
import runpy
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import native_cache_experiment as study
from aletheia_lab.evaluation import native_cache_source as source
from aletheia_lab.evaluation import warrant_development_live
from aletheia_lab.evaluation.native_cache_extraction import parser_facts
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-source") / "study"
    report = study.prepare(root=ROOT, directory=directory)
    return directory, report


@pytest.fixture
def copy_source(tmp_path, prepared):
    directory = tmp_path / "study"
    shutil.copytree(prepared[0], directory)
    return directory, deepcopy(prepared[1])


class Caller:
    def __init__(self, mode="valid"):
        self.mode = mode
        self.calls = []

    def invoke(self, *, prompt, payload, schema):
        self.calls.append((prompt, deepcopy(payload), schema))
        if self.mode == "raise":
            raise RuntimeError("private provider exception must not escape")
        facts = parser_facts(payload["documents"])
        if self.mode == "overcommit" and len(facts) == 1:
            facts.append({**facts[0], "kind": "loaded_endpoint"})
        return DevelopmentCall(
            status="completed",
            payload_json="{}"
            if self.mode == "invalid"
            else json.dumps({"schema_version": "source-evidence-proposal/v1", "facts": facts}),
            input_tokens=100,
            output_tokens=10,
            estimated_cost_usd=0.00028,
            latency_seconds=0.1,
        )


def test_actual_native_census_and_equal_logs_have_different_consumer_bytes(prepared):
    directory, report = prepared
    native = json.loads((directory / "source.json").read_text())
    first, swapped, corrected, legitimate, reverse = native["cases"]
    assert first["documents"][:3] == swapped["documents"][:3] == corrected["documents"][:3]
    assert (
        first["reference"]["status"]
        == corrected["reference"]["status"]
        == legitimate["reference"]["status"]
        == "no_binding_fault"
    )
    assert swapped["reference"]["status"] == reverse["reference"]["status"] == "binding_fault"
    assert (
        first["consumer_observation"]["consumed_sha256"]
        != swapped["consumer_observation"]["consumed_sha256"]
    )
    assert (
        first["consumer_observation"]["returned"]
        == swapped["consumer_observation"]["returned"][::-1]
    )
    assert report["native_load_count"] == 5 and report["case_view_count"] == 10
    assert report["source_cluster_count"] == 1 and report["parser_exact_reference_count"] == 10
    assert report["wire_audit"]["sdk_capture_count"] == 10
    assert report["wire_audit"]["provider_calls_executed"] == 0
    assert report["maximum_reserved_cost_usd_at_frozen_rates"] == 0.24576
    assert report["cost_ceiling_usd"] == 0.25 and not report["llm_efficacy_measured"]
    for request in study.request_frame(directory):
        text = json.dumps(request["payload"])
        assert "reference" not in text and "consumer_observation" not in text
        assert "control" not in request["payload"]
        assert str(directory) not in text and "/Users/" not in text
        if request["view"] == "log_only":
            assert all(doc["kind"] != "consumer-witness" for doc in request["payload"]["documents"])


@pytest.mark.parametrize(
    "mode,exact,unsafe",
    [("valid", 10, 0), ("invalid", 0, 0), ("overcommit", 5, 5), ("raise", 0, 0)],
    ids=["exact", "invalid", "false-authority", "exception"],
)
def test_lifecycle_full_denominator_failure_preservation_and_no_retry(
    copy_source, mode, exact, unsafe
):
    directory, report = copy_source
    caller, progress = Caller(mode), []
    receipt = study.execute(
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=caller,
        progress=progress.append,
    )
    assert len(caller.calls) == receipt["analysis"]["denominator"] == 10
    assert receipt["analysis"]["exact_fact_frame_count"] == exact
    assert receipt["analysis"]["raw_unwarranted_singleton_count"] == unsafe
    assert not receipt["source_mutated"]
    assert progress[-1]["completed_requests"] == 10
    assert study.verify(directory=directory) == receipt
    if mode == "raise":
        assert receipt["analysis"]["cost_usd_at_frozen_rates"] == round(10 * MAX_CALL_USD, 6)
        assert "private provider exception" not in (directory / "results/00.json").read_text()
    with pytest.raises(ValueError, match="attempt exists"):
        study.execute(directory=directory, confirm_sha256=report["plan_sha256"], caller=caller)
    assert len(caller.calls) == 10


def test_wrong_confirmation_and_code_drift_stop_before_provider(copy_source, monkeypatch):
    directory, _ = copy_source
    caller = Caller()
    with pytest.raises(ValueError):
        study.execute(directory=directory, confirm_sha256="a" * 64, caller=caller)
    monkeypatch.setattr(study, "_code_identity", lambda: {"changed": "a" * 64})
    with pytest.raises(ValueError):
        study.preflight(directory=directory)
    assert not caller.calls and not (directory / "lease.json").exists()


@pytest.mark.parametrize("name", ["source.json", "frozen-a.pkl", "frozen-b.pkl", "plan.json"])
def test_source_or_plan_byte_tamper_blocks_execution(copy_source, name):
    directory, report = copy_source
    path = directory / name
    if name == "plan.json":
        value = json.loads(path.read_text())
        value["cost_ceiling_usd"] = 1.0
        path.write_text(json.dumps(value))
    else:
        path.write_bytes(path.read_bytes() + b" ")
    caller = Caller()
    with pytest.raises(ValueError):
        study.execute(directory=directory, confirm_sha256=report["plan_sha256"], caller=caller)
    assert not caller.calls and not (directory / "lease.json").exists()


@pytest.mark.parametrize("change", ["assessment", "binding", "call", "receipt", "extra"])
def test_replay_rejects_saved_result_and_receipt_tamper(copy_source, change):
    directory, report = copy_source
    study.execute(directory=directory, confirm_sha256=report["plan_sha256"], caller=Caller())
    path = directory / "results/00.json"
    row = json.loads(path.read_text())
    if change == "assessment":
        row["assessment"]["exact_fact_frame"] = False
    elif change == "binding":
        row["case_id"] = "other"
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
        study.verify(directory=directory)


def test_cli_does_not_initialize_live_client_for_bad_confirmation(copy_source, monkeypatch, capsys):
    directory, _ = copy_source

    def forbidden(**kwargs):
        pytest.fail("network client initialized before local gate")

    monkeypatch.setattr(warrant_development_live, "OpenAIDevelopmentCaller", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "execute", "--study-dir", str(directory), "--confirm-plan-sha256", "wrong"],
    )
    main = runpy.run_path(str(ROOT / "scripts/native_cache_extraction.py"))["main"]
    assert main() == 1
    assert "failed_closed" in capsys.readouterr().out


def test_native_capture_restores_hook_and_logging_on_failure(tmp_path):
    import io
    import logging

    original = source.numpy_pickle.load
    level, handlers = logging.getLogger().level, logging.getLogger().handlers[:]
    file = io.BytesIO(b"not a trusted pickle")
    file.name = str(tmp_path / "output.pkl")
    with pytest.raises(ValueError):
        source._one_load(
            lambda: source.numpy_pickle.load(file),
            path=Path(file.name),
            trusted=b"other",
            requested=b"other",
        )
    assert source.numpy_pickle.load is original
    assert logging.getLogger().level == level and logging.getLogger().handlers == handlers


def test_private_path_boundary_and_source_generator_no_overwrite(tmp_path):
    with pytest.raises(ValueError):
        study.prepare(root=ROOT, directory=ROOT / "native-study")
    with pytest.raises(ValueError):
        source.generate_source(tmp_path)


def test_direct_native_generation_preserves_process_state(tmp_path, monkeypatch):
    import logging

    monkeypatch.chdir(tmp_path)
    original = source.numpy_pickle.load
    handlers, level = logging.getLogger().handlers[:], logging.getLogger().level
    result = source.generate_source(tmp_path)
    assert len(result["cases"]) == 5
    assert source.numpy_pickle.load is original
    assert logging.getLogger().handlers == handlers and logging.getLogger().level == level
    assert result["cases"][1]["consumer_observation"]["returned"] == [3, 2, 1]
    with pytest.raises(ValueError, match="fresh private"):
        source.generate_source(tmp_path)
