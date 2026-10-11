"""Offline contract tests; synthetic owned traces are not scientific outcomes."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import cache_lifecycle_analysis as cache_analysis
from aletheia_lab.evaluation import cache_lifecycle_study as cache_study
from aletheia_lab.evaluation import joint_inference_study as joint_study
from aletheia_lab.evaluation import litserve_evidence_study as lit_study
from aletheia_lab.evaluation import request_model_study as request_study
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def cache_source(config=None, stale=False):
    """Owned JSON linear models; explicitly ordered request and producer witnesses."""
    config = config or {"arm": "isolated", "order": "old_first", "evidence": "none"}
    artifacts, loads, events, rows = {}, {}, [], []

    def event(kind, **values):
        item = {"kind": kind, "sequence": len(events), "time_ns": len(events) * 10, **values}
        events.append(item)
        return item

    for generation, coefficient, intercept in (("A", 2.0, 1.0), ("B", 3.0, -1.0)):
        raw = encode({"coefficient": coefficient, "intercept": intercept}).encode()
        artifacts[generation] = {"raw_hex": raw.hex()}
        loads[generation] = event(
            "load_return",
            generation=generation,
            artifact=generation,
            digest=content_sha256(raw),
            coefficient=coefficient,
            intercept=intercept,
            object_id=11 if generation == "A" else 22,
        )
    tokens = [
        "warm0",
        "repeat0",
        "old",
        "after_reload0",
        "new1",
        "repeat1",
        "after_failed2",
        "repeat2",
    ] + [f"steady-{i}" for i in range(64)]
    published = "A"
    for index, token in enumerate(tokens):
        if index == 3:
            event("publish", previous_generation="A", generation="B")
            published = "B"
            rows.append({"route": "/reload", "status": 200})
        if index == 6:
            event("load_failure", preserved_generation="B")
            rows.append({"route": "/reload", "status": 400})
        event("select", token=token, generation=published, object_id=loads[published]["object_id"])
        generation = "A" if stale and index == 3 else published
        load = loads[generation]
        x = float(index)
        entry = event(
            "compute_enter",
            token=token,
            generation=generation,
            object_id=load["object_id"],
            digest=load["digest"],
            x=x,
        )
        value = {
            "cid": f"compute-{entry['sequence']}",
            "generation": generation,
            "digest": load["digest"],
            "x": x,
            "y": x * load["coefficient"] + load["intercept"],
        }
        event("compute_return", **value, object_id=load["object_id"])
        event("wrapper_return", token=token, selected_generation=published, returned=value)
        terminal = event("handler_terminal", token=token, returned_producer=value)
        rows.append(
            {
                "route": "/infer",
                "token": token,
                "offered_index": index,
                "status": 200,
                "body": {"x": x},
                "raw_response": encode({"y": value["y"]}),
                "completion_ns": terminal["time_ns"] + 1,
                "phase": "lifecycle" if index < 8 else "steady",
                "elapsed_ns": 100 + index,
            }
        )
    return {
        "config": config,
        "artifacts": artifacts,
        "events": events,
        "rows": rows,
        "terminal": "complete",
        "signed_status": "not_executed",
        "signed_digest": None,
        "audit_query": {
            "records": [],
            "physical_bytes": 0,
            "logical_bytes": 0,
            "commit_batches": 0,
        },
        "measurements": {
            key: 1
            for key in (
                "capture_ns",
                "persist_ns",
                "query_ns",
                "hash_ns",
                "process_cpu_ns",
                "sign_verify_ns",
            )
        },
    }


def infer(source, index=0):
    return [row for row in source["rows"] if row["route"] == "/infer"][index]


def witness(source, kind, token=None):
    return next(
        event
        for event in source["events"]
        if event["kind"] == kind and (token is None or event.get("token") == token)
    )


def test_cache_independent_reference_and_no_evidence_denominator(tmp_path):
    source = cache_source(stale=True)
    raw = cache_analysis.reference(source)
    assert raw["truth"]["after_reload0"] == "violation"
    assert sum(value == "compliant" for value in raw["truth"].values()) == 71
    assert raw["compute_count"] == 72 and raw["loads"] == 2
    protocol = json.loads((ROOT / cache_study.PROTOCOL).read_bytes())
    finding = cache_analysis.analyze(source, tmp_path, protocol)
    assert finding["complete"] is True
    assert finding["offered_inferences"] == 72 and finding["offered_reloads"] == 2
    assert finding["service"] == {
        "correct": 0,
        "false": 0,
        "unknown_or_unserved": 72,
        "records": 0,
        "conditional_native_correct": 71,
        "conditional_native_unknown": 0,
    }
    assert finding["violated_tokens"] == ["after_reload0"]
    assert finding["steady_latency_median_ns"] == 139.5
    assert finding["forecasts"]["violation_count"] is False
    assert finding["forecasts"]["failed_reload_preserves_generation"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        "raw_digest",
        "parameters",
        "duplicate_load",
        "changed_operand",
        "wrong_object",
        "arithmetic",
        "publish_previous",
        "failed_generation",
        "selected_object",
        "producer_value",
        "response_bytes",
        "selected_generation",
        "terminal_value",
        "terminal_late",
        "wrapper_order",
        "sequence",
        "chronology",
        "request_order",
        "offer_index",
        "duplicate_select",
        "duplicate_producer",
        "response_operand",
        "oversized_events",
    ],
)
def test_cache_reference_rejects_changed_owned_witness(mutation):
    source = cache_source()
    load = witness(source, "load_return")
    entered = witness(source, "compute_enter", "warm0")
    returned = witness(source, "compute_return")
    wrapper = witness(source, "wrapper_return", "warm0")
    terminal = witness(source, "handler_terminal", "warm0")
    if mutation == "raw_digest":
        source["artifacts"]["A"]["raw_hex"] = b"foreign release".hex()
    elif mutation == "parameters":
        load["coefficient"] = 99
    elif mutation == "duplicate_load":
        load2 = witness(source, "load_return", None)
        source["events"][1]["generation"] = load2["generation"]
    elif mutation == "changed_operand":
        entered["x"] = 99
    elif mutation == "wrong_object":
        returned["object_id"] = entered["object_id"] = 999
    elif mutation == "arithmetic":
        returned["y"] = 999
    elif mutation == "publish_previous":
        witness(source, "publish")["previous_generation"] = "B"
    elif mutation == "failed_generation":
        witness(source, "load_failure")["preserved_generation"] = "A"
    elif mutation == "selected_object":
        witness(source, "select", "warm0")["object_id"] = 999
    elif mutation == "producer_value":
        wrapper["returned"] = {**wrapper["returned"], "y": 999}
    elif mutation == "response_bytes":
        infer(source)["raw_response"] = '{"y":999}'
    elif mutation == "selected_generation":
        wrapper["selected_generation"] = "B"
    elif mutation == "terminal_value":
        terminal["returned_producer"] = {**terminal["returned_producer"], "y": 999}
    elif mutation == "terminal_late":
        infer(source)["completion_ns"] = terminal["time_ns"] - 1
    elif mutation == "wrapper_order":
        left, right = source["events"].index(wrapper), source["events"].index(terminal)
        source["events"][left], source["events"][right] = terminal, wrapper
        for sequence, event in enumerate(source["events"]):
            event["sequence"], event["time_ns"] = sequence, sequence * 10
    elif mutation == "sequence":
        source["events"][0]["sequence"] = 999
    elif mutation == "chronology":
        source["events"][1]["time_ns"] = -1
    elif mutation == "request_order":
        infer(source)["token"] = "other"
    elif mutation == "offer_index":
        infer(source)["offered_index"] = 99
    elif mutation in {"duplicate_select", "duplicate_producer"}:
        duplicated = deepcopy(
            witness(source, "select", "warm0") if mutation == "duplicate_select" else returned
        )
        duplicated.update(sequence=len(source["events"]), time_ns=len(source["events"]) * 10)
        source["events"].append(duplicated)
    elif mutation == "response_operand":
        infer(source)["body"]["x"] = 999
    else:
        source["events"] = [deepcopy(event) for _ in range(6) for event in source["events"]]
        for sequence, event in enumerate(source["events"]):
            event["sequence"], event["time_ns"] = sequence, sequence * 10
        assert len(source["events"]) > 2048
    with pytest.raises(ValueError):
        cache_analysis.reference(source)


def test_cache_http_failure_is_unknown_and_unsafe_body_does_not_identify_model(tmp_path):
    source = cache_source({"arm": "input_key", "order": "old_first", "evidence": "none"})
    infer(source)["status"] = 503
    protocol = json.loads((ROOT / cache_study.PROTOCOL).read_bytes())
    finding = cache_analysis.analyze(source, tmp_path, protocol)
    assert finding["truth_counts"] == {"unknown": 1, "compliant": 71}
    assert finding["service"]["conditional_native_correct"] == 0
    assert finding["service"]["conditional_native_unknown"] == 72


@pytest.mark.parametrize("mode", ["full", "sufficient"])
def test_cache_retention_checks_physical_database_and_declared_census(tmp_path, mode):
    source = cache_source()
    source["config"]["evidence"] = mode
    retained_kinds = {
        "load_return",
        "compute_return",
        "wrapper_return",
        "handler_terminal",
        "response",
        "publish",
        "load_failure",
    }
    records = (
        source["events"]
        if mode == "full"
        else [event for event in source["events"] if event["kind"] in retained_kinds]
    )
    source["audit_query"]["records"] = records
    with closing(sqlite3.connect(tmp_path / "audit.sqlite")) as connection, connection:
        connection.execute("CREATE TABLE audit(sequence INTEGER PRIMARY KEY,payload BLOB NOT NULL)")
        connection.executemany(
            "INSERT INTO audit VALUES (?, ?)",
            [(event["sequence"], encode(event).encode()) for event in records],
        )
    assert cache_analysis.retained_records(tmp_path, source) == records
    with closing(sqlite3.connect(tmp_path / "audit.sqlite")) as connection, connection:
        connection.execute("DELETE FROM audit WHERE sequence=?", (records[0]["sequence"],))
    with pytest.raises(ValueError, match="physical retained"):
        cache_analysis.retained_records(tmp_path, source)


@pytest.mark.parametrize("mode", ["full", "sufficient"])
def test_cache_physical_service_answers_from_retained_records_and_checks_signed_digest(
    tmp_path, monkeypatch, mode
):
    from aletheia_lab.evaluation import litserve_evidence_provenance as provenance

    source = cache_source(stale=True)
    source["config"]["evidence"] = mode
    retained_kinds = {
        "load_return",
        "compute_return",
        "wrapper_return",
        "handler_terminal",
        "response",
        "publish",
        "load_failure",
    }
    records = (
        source["events"]
        if mode == "full"
        else [event for event in source["events"] if event["kind"] in retained_kinds]
    )
    source["audit_query"]["records"] = records
    database = tmp_path / "audit.sqlite"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE audit(sequence INTEGER PRIMARY KEY,payload BLOB NOT NULL)")
        connection.executemany(
            "INSERT INTO audit VALUES (?, ?)",
            [(event["sequence"], encode(event).encode()) for event in records],
        )
    source["signed_status"] = "pass"
    source["signed_digest"] = content_sha256(database.read_bytes())
    checked = []
    monkeypatch.setattr(provenance, "verify_bundle", lambda path: checked.append(path))
    protocol = json.loads((ROOT / cache_study.PROTOCOL).read_bytes())
    finding = cache_analysis.analyze(source, tmp_path, protocol)
    assert checked == [tmp_path / "provenance"]
    assert finding["service"]["correct"] == 72
    assert finding["service"]["false"] == finding["service"]["unknown_or_unserved"] == 0
    assert finding["physical_bytes_closed"] == database.stat().st_size
    assert finding["forecasts"]["materialized_join"] is True
    source["signed_digest"] = "0" * 64
    with pytest.raises(ValueError, match="signed database digest"):
        cache_analysis.analyze(source, tmp_path, protocol)


def test_cache_none_mode_rejects_a_materialized_service(tmp_path):
    source = cache_source()
    write_new_file(tmp_path / "audit.sqlite", b"unexpected materialized service")
    with pytest.raises(ValueError, match="none mode"):
        cache_analysis.retained_records(tmp_path, source)


def test_cache_retention_rejects_a_symlinked_database(tmp_path):
    source = cache_source()
    write_new_file(tmp_path / "audit.sqlite", b"unexpected materialized service")
    source["config"]["evidence"] = "full"
    alias = tmp_path / "alias"
    alias.mkdir()
    try:
        (alias / "audit.sqlite").symlink_to(tmp_path / "audit.sqlite")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(ValueError, match="symlink"):
        cache_analysis.retained_records(alias, source)


def cache_identity():
    return {"packages": {"aiohttp": "3.14.3", "cachetools": "6.2.6", "in-toto": "3.0.0"}}


def test_cache_prepare_run_verify_preserves_missing_invalid_and_successful_cells(
    tmp_path, monkeypatch
):
    metadata = cache_identity()
    observed = []

    def fake_native(command, **kwargs):
        if "-c" in command:
            assert len(command) == 3 and command[1] == "-c"
            assert "m.distribution(n).locate_file(f)" in command[2]
            return subprocess.CompletedProcess(command, 0, encode(metadata).encode(), b"")
        assert Path(command[1]) == ROOT / "scripts/cache_lifecycle_validation.py"
        assert command[2] == "worker"
        config = json.loads(Path(command[command.index("--config") + 1]).read_bytes())
        target = Path(command[command.index("--study-dir") + 1])
        observed.append(config)
        if len(observed) == 1:
            write_new_file(target / "source.json", b"{}")
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if config["evidence"] == "none" and config["replicate"] == 0:
            write_new_file(target / "source.json", encode(cache_source(config)).encode())
            return subprocess.CompletedProcess(command, 0, b"synthetic worker output", b"")
        raise subprocess.TimeoutExpired(
            command, kwargs["timeout"], output=b"partial", stderr=b"timeout"
        )

    monkeypatch.setattr(cache_study.subprocess, "run", fake_native)
    directory = tmp_path / "study"
    prepared = cache_study.prepare(ROOT, directory, Path("fake-python"), tmp_path)
    assert prepared["cell_count"] == 48 and prepared["runtime_outcomes_observed"] is False
    result = cache_study.run(ROOT, directory, Path("fake-python"), tmp_path)
    report = cache_study.read_sealed(directory / "results.json")
    assert len(observed) == len(report["executions"]) == len(report["findings"]) == 48
    assert report["findings"][0] == {"verification": "fail", "error_type": "KeyError"}
    assert any(
        finding is not None and finding["verification"] == "pass" for finding in report["findings"]
    )
    assert result["analysis"]["planned_inferences"] == 3456
    assert (
        sum(value["unknown_or_unserved"] for value in result["analysis"]["by_evidence"].values())
        == 3456
    )
    assert cache_study.verify(ROOT, directory)["analysis"] == result["analysis"]
    with pytest.raises(ValueError, match="never silently resume"):
        cache_study.run(ROOT, directory, Path("fake-python"), tmp_path)
    missing = next(row for row in report["executions"] if row["source_sha256"] is None)
    write_new_file(directory / f"cell-{missing['index']:03}" / "source.json", b"{}")
    with pytest.raises(ValueError, match="inserted after execution"):
        cache_study.verify(ROOT, directory)


def test_request_missing_native_workers_preserve_all_planned_requests(tmp_path, monkeypatch):
    seen = []
    provenance_versions = {"in-toto": "3.0.0", "securesystemslib": "1.5.1"}
    monkeypatch.setattr(request_study, "version", provenance_versions.__getitem__)

    def fake_native(command, **kwargs):
        if "-c" in command:
            assert command[1:] == [
                "-c",
                "from importlib.metadata import version; print(version('ray'))",
            ]
            version = request_study.VERSIONS[Path(command[0]).name]
            return subprocess.CompletedProcess(command, 0, (version + "\n").encode(), b"")
        assert Path(command[1]) == ROOT / "scripts/request_model_validation.py"
        assert command[2] == "worker"
        seen.append(command)
        raise subprocess.TimeoutExpired(
            command, kwargs["timeout"], output=b"partial", stderr=b"timeout"
        )

    monkeypatch.setattr(request_study.subprocess, "run", fake_native)
    directory = tmp_path / "study"
    result = request_study.run(ROOT, directory, Path("before"), Path("fixed"))
    assert len(seen) == 8 and result["analysis"]["retention_arm_count"] == 0
    assert result["analysis"]["native"]["before/original"]["reference_counts"] == {"unknown": 24}
    assert result["analysis"]["native"]["fixed/owned_ml"]["reference_counts"] == {"unknown": 38}
    assert all(group["terminal_workers"] == 0 for group in result["analysis"]["native"].values())
    report_path = directory / "results.json"
    report = json.loads(report_path.read_bytes())
    report["workers"].pop()
    report["results_sha256"] = request_study.digest(
        {key: value for key, value in report.items() if key != "results_sha256"}
    )
    report_path.write_text(encode(report))
    with pytest.raises(ValueError, match="worker census"):
        request_study.verify(ROOT, directory)


def test_litserve_failed_launch_retains_census_and_verifies_read_only(tmp_path, monkeypatch):
    from aletheia_lab.evaluation import litserve_evidence_provenance as provenance

    metadata = {"packages": {"litserve": "0.2.19"}}

    def metadata_only(command, **kwargs):
        assert len(command) == 3 and command[1] == "-c"
        assert "m.distribution('litserve')" in command[2]
        return subprocess.CompletedProcess(command, 0, encode(metadata).encode(), b"")

    monkeypatch.setattr(lit_study.subprocess, "run", metadata_only)

    def launch_failure(*args):
        raise PermissionError("synthetic native boundary refusal")

    def signing_unavailable(*args):
        raise ImportError("synthetic signing boundary unavailable")

    monkeypatch.setattr(lit_study, "execute_cell", launch_failure)
    monkeypatch.setattr(provenance, "sign_bundle", signing_unavailable)
    directory = tmp_path / "study"
    result = lit_study.run(ROOT, directory, Path("fake-python"), tmp_path)
    assert result["planned_requests"] == 88 and result["offered_requests"] == 0
    assert result["complete"] is False
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    replay = lit_study.verify(ROOT, directory)
    assert replay["verification"] == "pass" and replay["planned_requests"] == 88
    assert before == {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert {
        cell["failure"] for cell in json.loads((directory / "results.json").read_bytes())["cells"]
    } == {"PermissionError"}


@pytest.mark.parametrize("failure", ["timeout", "terminal_failure"])
def test_joint_native_failure_preserves_logs_and_has_no_published_result(
    tmp_path, monkeypatch, failure
):
    def fake_native(command, **kwargs):
        assert Path(command[1]) == ROOT / "scripts/joint_inference_development.py"
        assert command[2] == "worker"
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                command, kwargs["timeout"], output=b"partial", stderr=b"native timeout"
            )
        return subprocess.CompletedProcess(command, 2, b"partial", b"native failure")

    monkeypatch.setattr(joint_study.subprocess, "run", fake_native)
    directory = tmp_path / "study"
    with pytest.raises(RuntimeError, match="private logs retained"):
        joint_study.run(ROOT, directory, [tmp_path])
    assert (directory / "plan.json").is_file()
    assert (directory / "native-0-stdout.log").read_bytes() == b"partial"
    assert not (directory / "results.json").exists()


def test_joint_fake_native_runs_actual_durable_replays_and_verifier(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "joint_existing_fixture", ROOT / "tests/unit/test_joint_inference_study.py"
    )
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)
    value = {
        **fixture_module.source(),
        "startups": 7,
        "http_requests": 24,
        "descriptor_observations": [{}] * 13,
    }
    known = [
        "compliant",
        "compliant",
        "violation",
        "compliant",
        "compliant",
        "unknown",
        "violation",
        "compliant",
        "violation",
        "compliant",
        "unknown",
        "compliant",
    ]
    for row, verdict in zip(value["rows"], known, strict=True):
        row["signed"] = verdict
    seen = []

    def fake_native(command, **kwargs):
        assert Path(command[1]) == ROOT / "scripts/joint_inference_development.py"
        assert command[2] == "worker"
        target = Path(command[command.index("--study-dir") + 1])
        seen.append(target.name)
        write_new_file(target / "source.json", encode(value).encode())
        return subprocess.CompletedProcess(command, 0, b"synthetic worker", b"")

    monkeypatch.setattr(joint_study.subprocess, "run", fake_native)
    result = joint_study.run(ROOT, tmp_path / "study", [tmp_path])
    assert seen == ["native-0", "native-1"]
    assert result["verification"] == "pass"
    assert result["analysis"]["replay_arms"] == 36
    assert result["analysis"]["native"]["compliant"] == 14
    assert result["analysis"]["native"]["violation"] == 6
    assert result["analysis"]["native"]["unknown"] == 4
    assert all(
        comparison["optional_offered"] == 36 for comparison in result["analysis"]["comparisons"]
    )
    assert all(
        comparison.get("accepted_unserved", 0) == 0
        for comparison in result["analysis"]["comparisons"]
    )
