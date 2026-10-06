from __future__ import annotations

import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest

from aletheia_lab.evaluation import application_audit_study as study
from aletheia_lab.evaluation.application_audit_analysis import check_row
from aletheia_lab.evaluation.application_audit_sources import PickleCapture
from aletheia_lab.evaluation.model_load_serving_workload import decide
from aletheia_lab.project.identity import content_sha256


def row() -> tuple[dict, dict]:
    raw = b"owned-release"
    digest = content_sha256(raw)
    releases = {
        "A": {
            "sha256": digest,
            "raw_hex": raw.hex(),
            "object_fingerprint": "object-a",
            "predictions": [0, 1],
        },
        "B": {
            "sha256": content_sha256(b"partner"),
            "raw_hex": b"partner".hex(),
            "object_fingerprint": "object-b",
            "predictions": [1, 0],
        },
    }
    frame = {
        "scope": "census-00",
        "step": 0,
        "kind": "load",
        "domain": [value["sha256"] for value in releases.values()],
        "expected": digest,
        "observed": [digest],
        "count": 1,
        "closed": True,
        "generation": None,
    }
    return {
        "scope": frame["scope"],
        "step": "initial",
        "frame": frame,
        "raw_reference": [{"scope": frame["scope"], "raw_hex": raw.hex()}],
        "startup_error": None,
        "gold": "compliant",
        "object_model": "A",
        "native_response": {
            "http_status": 200,
            "health_status": 200,
            "object_fingerprint": "object-a",
            "predictions": [0, 1],
        },
        "sufficient": decide(frame),
        "signed_provenance": "compliant",
    }, releases


def test_reference_replay_matches_raw_descriptor_not_stored_gold() -> None:
    value, releases = row()
    assert check_row(value, releases, 0) == "compliant"
    value["gold"] = "violation"
    with pytest.raises(ValueError, match="gold"):
        check_row(value, releases, 0)


@pytest.mark.parametrize(
    "mutation",
    (
        "release",
        "capture",
        "count",
        "selection",
        "object",
        "prediction",
        "health",
        "signed",
        "census",
    ),
)
def test_rehashed_semantic_tampering_is_detected(mutation: str) -> None:
    value, releases = row()
    value, releases = deepcopy(value), deepcopy(releases)
    if mutation == "release":
        releases["A"]["raw_hex"] = b"changed".hex()
    elif mutation == "capture":
        value["frame"]["observed"] = [releases["B"]["sha256"]]
    elif mutation == "count":
        value["frame"]["count"] = 0
    elif mutation == "selection":
        value["frame"]["expected"] = releases["B"]["sha256"]
    elif mutation == "object":
        value["native_response"]["object_fingerprint"] = "foreign"
    elif mutation == "prediction":
        value["native_response"]["predictions"] = [1, 0]
    elif mutation == "health":
        value["native_response"]["health_status"] = 503
    elif mutation == "signed":
        value["signed_provenance"] = "violation"
    else:
        value["step"] = "lawful_change"
    with pytest.raises(ValueError):
        check_row(value, releases, 0)


def test_pickle_capture_preserves_original_descriptor_and_restores_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pickle

    optional_alias = ModuleType("cloudpickle")
    optional_alias.load = pickle.load
    monkeypatch.setitem(sys.modules, "cloudpickle", optional_alias)
    payload = pickle.dumps({"model": "owned"})
    path = tmp_path / "fresh.pkl"
    path.write_bytes(payload)
    original = pickle.load
    capture = PickleCapture(tmp_path)
    capture.scope = "load"
    with capture.installed(), path.open("rb") as stream:
        assert pickle.load(stream) == {"model": "owned"}
    assert pickle.load is original
    assert capture.reference == [{"scope": "load", "raw_hex": payload.hex()}]
    assert capture.receipts == [{"scope": "load", "descriptor_sha256": content_sha256(payload)}]


def test_worker_bootstraps_loop_before_socket_guard_and_guards_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = socket.socket.connect
    phases = []

    async def fake(stack: str, directory: Path) -> dict:
        assert socket.socket.connect is not original
        with pytest.raises(RuntimeError, match="network"):
            socket.create_connection(("127.0.0.1", 1))

        async def cleanup():
            try:
                yield
            finally:
                assert socket.socket.connect is not original
                phases.append("cleanup")

        generator = cleanup()
        await generator.__anext__()
        directory.mkdir()
        return {"stack": stack}

    monkeypatch.setattr(study, "worker", fake)
    study.execute_worker("bentoml", tmp_path / "owned")
    assert socket.socket.connect is original and phases == ["cleanup"]
    assert json.loads((tmp_path / "owned/result.json").read_bytes()) == {"stack": "bentoml"}


def test_existing_output_rejected_before_any_worker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing outcomes"):
        study.run(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, Path(__file__))


def test_child_environment_strips_secrets_and_overrides_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("BENTOCLOUD_API_TOKEN", "not-a-real-secret")
    environment = study.child_environment(tmp_path, [tmp_path / "dependencies"])
    assert "OPENAI_API_KEY" not in environment and "BENTOCLOUD_API_TOKEN" not in environment
    assert environment["BENTOML_DO_NOT_TRACK"] == "true"
    assert environment["PYTHONPATH"].startswith(str(tmp_path / "src"))
