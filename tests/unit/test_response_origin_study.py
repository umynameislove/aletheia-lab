"""Raw source/census/repair/replay regression checks; no optional SDK execution."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import socket
import sys
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import response_origin_source as source_module
from aletheia_lab.evaluation import response_origin_study as study
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.evaluation.response_origin_reference import (
    bentoml_closure,
    check_source,
    planned_tokens,
    rebuild,
)
from aletheia_lab.evaluation.response_origin_source import ARMS, OriginProbe
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def source_fixture() -> dict[str, Any]:
    raw = b"inert bytes; never deserialized"
    parameters = {"coef": [1.0], "intercept": 0.0}
    actual = {
        "generation": "g0",
        "name": "model",
        "version": "1",
        "runtime_id": 10,
        "object_id": 11,
        "parameters": parameters,
        "pid": 1,
    }
    response = {
        "model_name": "model",
        "model_version": "1",
        "outputs": [{"shape": [1], "data": [1.0]}],
    }
    payload = {"inputs": [{"name": "x", "datatype": "FP64", "shape": [1, 1], "data": [1.0]}]}
    key = encode(
        {
            **payload,
            "parameters": {
                "headers": dict.fromkeys(
                    (
                        "host",
                        "accept",
                        "accept-encoding",
                        "connection",
                        "user-agent",
                        "content-length",
                        "content-type",
                    ),
                    "fixture",
                )
            },
        }
    )
    source: dict[str, Any] = {
        "arm": "native",
        "workflow": "version_routes",
        "terminal": "complete",
        "provider_calls": 0,
        "artifacts": {
            "A": {"raw_hex": raw.hex(), "sha256": content_sha256(raw), "parameters": parameters}
        },
        "loads": {"g0": {**actual, "artifact": content_sha256(raw)}},
        "events": [],
        "rows": [],
        "environment": {"packages": {"mlserver": "1.7.1"}},
    }

    def event(token, kind, **fields):
        index = len(source["events"])
        source["events"].append(
            {"sequence": index, "time_ns": index, "token": token, "kind": kind, **fields}
        )

    for token in planned_tokens("version_routes"):
        infer = token != "initial-load"
        if infer:
            event(token, "selected", actual=actual)
            event(
                token,
                "lookup",
                key=key,
                effective=key,
                hit=token != "v1-first",
                value=encode(response) if token != "v1-first" else "",
                producer="compute" if token != "v1-first" else None,
            )
            if token == "v1-first":
                event(token, "compute_enter", identifier="compute", actual=actual, payload=payload)
                event(
                    token, "compute_return", identifier="compute", actual=actual, response=response
                )
                event(
                    token,
                    "insert",
                    key=key,
                    effective=key,
                    value=encode(response),
                    producer="compute",
                    accepted=True,
                )
        number = "2" if token.startswith("v2") else "1"
        source["rows"].append(
            {
                "token": token,
                "route": f"/v2/models/model/versions/{number}/infer" if infer else "/load",
                "body": payload if infer else None,
                "status": 200,
                "response": response if infer else {},
                "raw_response": encode(response if infer else {}),
                "response_headers": {"ce-modelid": "model", "ce-modelversion": number},
                "origin": "compute" if infer else None,
                "elapsed_ns": 10,
            }
        )
    return source


def test_independent_raw_cache_rebuild_and_native_baseline():
    frames, truth, _ = rebuild(source_fixture())
    assert truth == ["compliant", "compliant", "violation", "violation", "compliant"]
    assert frames[1]["nodes"][frames[1]["request"]["origin"]]["kind"] == "cache"
    result = study.analyze(source_fixture())
    assert result["summaries"]["origin"]["correct_conclusive"] == 5
    assert result["summaries"]["native_body"]["correct_conclusive"] == 5
    assert result["summaries"]["native_headers"]["false_conclusive"] == 2
    assert result["summaries"]["direct_only"]["missing_conclusive"] == 4


@pytest.mark.parametrize(
    "changed", ["arithmetic", "cache", "object", "artifact", "sequence", "census"]
)
def test_raw_cross_checks_do_not_trust_rehashed_aggregate(changed):
    value = source_fixture()
    if changed == "arithmetic":
        next(event for event in value["events"] if event["kind"] == "compute_return")["response"][
            "outputs"
        ][0]["data"] = [4.0]
    elif changed == "cache":
        next(event for event in value["events"] if event["kind"] == "lookup" and event["hit"])[
            "producer"
        ] = "fabricated"
    elif changed == "object":
        next(event for event in value["events"] if event["kind"] == "compute_return")["actual"][
            "object_id"
        ] = 999
        # Deep shared fixture references are split so this is a one-event change.
        value = copy.deepcopy(source_fixture())
        target = next(event for event in value["events"] if event["kind"] == "compute_return")
        target["actual"] = {**target["actual"], "object_id": 999}
    elif changed == "artifact":
        value["artifacts"]["A"]["raw_hex"] = b"changed".hex()
    elif changed == "sequence":
        value["events"][0]["sequence"] = 5
    else:
        value["rows"].pop()
    with pytest.raises(ValueError):
        check_source(value)


def test_failed_partial_source_preserves_all_planned_inferences_unknown():
    value = source_fixture()
    value.update(terminal="failed", rows=[], events=[], loads={})
    result = study.analyze(value)
    assert result["truth"] == ["unknown"] * 5
    assert result["planned_operations"] == 6
    assert result["attempted_operations"] == 0


@pytest.mark.parametrize(
    "changed",
    [
        "structured-response",
        "original-key",
        "selected-generation",
        "effective-key",
        "producer-label",
        "return-token",
    ],
)
def test_raw_correspondence_falsifiers_fail_closed(changed):
    value = copy.deepcopy(source_fixture())
    if changed == "structured-response":
        row = value["rows"][1]
        row["response"] = {**row["response"], "model_version": "2"}
    elif changed == "original-key":
        for event in value["events"]:
            if event["kind"] in {"lookup", "insert"}:
                event["key"] = "arbitrary text"
    elif changed == "selected-generation":
        selected = value["events"][0]
        selected["actual"] = {**selected["actual"], "generation": "fabricated-old"}
    elif changed == "effective-key":
        value["events"][1]["effective"] = "arbitrary namespace"
    elif changed == "producer-label":
        target = next(event for event in value["events"] if event["kind"] == "compute_return")
        target["response"] = {**target["response"], "model_version": "2"}
    else:
        target = next(event for event in value["events"] if event["kind"] == "compute_return")
        target["token"] = "different-request"
    with pytest.raises(ValueError):
        rebuild(value)


def test_recorded_non_json_response_abstains_without_losing_denominator():
    value = source_fixture()
    value["rows"][1] = {
        **value["rows"][1],
        "raw_response": "not-json",
        "response": None,
        "response_decode_error": True,
    }
    result = study.analyze(value)
    assert len(result["truth"]) == 5
    assert result["truth"][0] == "unknown"
    value["rows"][1]["response_decode_error"] = False
    with pytest.raises(ValueError, match="decoding"):
        rebuild(value)


def test_consistently_changed_raw_and_structured_labels_still_need_producer_match():
    value = source_fixture()
    row = value["rows"][1]
    row["response"] = {**row["response"], "model_version": "2"}
    row["raw_response"] = encode(row["response"])
    with pytest.raises(ValueError, match="served response labels"):
        rebuild(value)


def test_http_error_does_not_close_worker_or_certify_response_origin():
    source = {
        "terminal": "complete",
        "pending_workers_at_finish": [],
        "plan": {"planned_requests": 1},
        "requests": [{"token": "slow", "case": "slow-failure", "status": 504}],
        "events": [
            {"kind": "worker_enter", "token": "slow", "ingress_token": "http", "time_ns": 1},
            {"kind": "http_response_closed", "token": None, "ingress_token": "http", "time_ns": 2},
            {
                "kind": "model_computed",
                "token": "slow",
                "ingress_token": "http",
                "time_ns": 3,
                "input": 3,
                "output": 7,
                "model": {"coef": 2, "intercept": 1},
            },
            {"kind": "worker_raise", "token": "slow", "ingress_token": "http", "time_ns": 4},
            {"kind": "worker_closed", "token": "slow", "ingress_token": "http", "time_ns": 5},
        ],
    }
    report = bentoml_closure(source)
    assert report["offered"] == 1
    assert report["cases"] == [
        {
            "case": "slow-failure",
            "status": 504,
            "http_before_worker_exit": True,
            "late_failure_observed": True,
            "response_verdict": "unknown",
        }
    ]
    source["events"][2]["output"] = 8
    with pytest.raises(ValueError, match="numerical"):
        bentoml_closure(source)
    source["pending_workers_at_finish"] = ["slow"]
    with pytest.raises(ValueError, match="full census"):
        bentoml_closure(source)


@pytest.mark.parametrize("arm", ARMS)
def test_key_and_fence_keep_selected_old_object_at_insert(arm):
    probe = object.__new__(OriginProbe)
    probe.arm = arm
    probe.selected = ContextVar("selected", default="g0")
    probe.entry_epoch = ContextVar("entry", default=1)
    probe.producer = ContextVar("producer", default="old-compute")
    probe.token = ContextVar("token", default="old-inflight")
    probe.loads = {"g0": {"name": "model", "version": "1"}}
    probe.epoch, probe.events, probe.insert_origins = 2, [], {}
    inserted = []

    async def insert(key, value):
        inserted.append((key, value))

    async def lookup(key):
        return ""

    probe.cache = SimpleNamespace(insert=insert, lookup=lookup)
    probe._cache_hooks()
    asyncio.run(probe.cache.insert("native-key", "value"))
    assert len(inserted) == (0 if arm == "fenced" else 1)
    if arm == "generation_key":
        assert "g0" in inserted[0][0]
    assert probe.events[0]["producer"] == "old-compute"


def test_fresh_fixed_design_has_complete_scope_and_strong_baselines():
    plan = study.design(ROOT)
    assert len(plan["cells"]) == 36
    assert plan["planned_operation_count"] == 264
    assert plan["planned_inference_count"] == 180
    assert plan["provider_calls"] == plan["protected_runs"] == 0
    assert set(plan["ordinary_repairs"]) == set(ARMS)


def test_worker_bootstraps_loop_before_network_guard_and_guards_finalizers(tmp_path, monkeypatch):
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    order = []
    environment = {"MLSERVER_OWNED": "x", "KEEP_OWNED": "keep"}

    class Probe:
        def __init__(self, *args):
            pass

        async def run(self):
            for connect in (
                socket.socket.connect,
                socket.socket.connect_ex,
                socket.create_connection,
            ):
                with pytest.raises(RuntimeError, match="cannot use external sockets"):
                    connect(None, ("127.0.0.1", 1))
            return {"terminal": "complete"}

    class Runner:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert order == ["bootstrap", "guarded-close"]

        def get_loop(self):
            assert socket.socket.connect is originals[0]
            order.append("bootstrap")

        def run(self, coroutine):
            with pytest.raises(StopIteration) as finished:
                coroutine.send(None)
            return finished.value.value

        def close(self):
            with pytest.raises(RuntimeError, match="cannot use external sockets"):
                socket.create_connection(("127.0.0.1", 1))
            order.append("guarded-close")

    # The fake runner models Windows self-pipe creation without native SDK imports.
    monkeypatch.setattr(source_module, "OriginProbe", Probe)
    monkeypatch.setattr(source_module.os, "environ", environment)
    monkeypatch.setattr(source_module.asyncio, "Runner", Runner)
    result = source_module.worker("native", "version_routes", tmp_path / "owned")
    assert result == {"terminal": "complete", "provider_calls": 0}
    assert environment == {"KEEP_OWNED": "keep", "OTEL_SDK_DISABLED": "true"}
    assert (socket.socket.connect, socket.socket.connect_ex, socket.create_connection) == originals


def test_cli_preserves_explicit_venv_path_without_resolving_symlink(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("response_origin_cli", ROOT / study.FILES[0])
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    seen = []
    monkeypatch.setattr(
        study, "run", lambda root, directory, executable: seen.append(executable) or {"ok": True}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "response_origin_validation",
            "run",
            "--native-python",
            sys.executable,
            "--study-dir",
            str(tmp_path / "study"),
        ],
    )
    assert cli.main() == 0
    assert seen == [Path(sys.executable).absolute()]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_missing_workers_stay_in_denominator_and_replay_rejects_forged_result(
    tmp_path, monkeypatch
):
    def execute(root, executable, directory, cell):
        directory.mkdir()
        return {**cell, "returncode": 1, "failure": "RuntimeError", "source_sha256": None}

    monkeypatch.setattr(study, "_execute", execute)
    target = tmp_path / "study"
    result = study.run(ROOT, target, Path("unused"))
    assert result["analysis"]["offered_inferences"] == 180
    assert result["analysis"]["failed_or_missing_cells"] == 36
    assert study.verify(ROOT, target)["native_execution_repeated"] is False
    path = target / "results.json"
    stored = json.loads(path.read_bytes())
    stored["analysis"]["offered_inferences"] = 0
    stored.pop("results_sha256")
    stored["results_sha256"] = digest(stored)
    path.write_text(encode(stored), encoding="utf-8")
    with pytest.raises(ValueError, match="replay"):
        study.verify(ROOT, target)
