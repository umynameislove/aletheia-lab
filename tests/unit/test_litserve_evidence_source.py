"""SDK-free source fidelity contracts; synthetic branches, not native validation."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aletheia_lab.evaluation import litserve_evidence_source as source
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def api(tmp_path, monkeypatch, arm="native"):
    monkeypatch.setattr(source, "_DIRECTORY", tmp_path)
    monkeypatch.setattr(source, "_WRITER", None)
    value = source.EvidenceAPI(tmp_path, "/a", arm)
    value.model = source._OwnedModel(2.0, 1.0)
    return value


def test_owned_model_json_is_bounded_regular_and_format_checked(tmp_path):
    path = tmp_path / "owned.json"
    write_new_file(
        path, encode({"format": "owned-affine-json/v1", "coefficient": 2, "intercept": 1}).encode()
    )
    value, raw = source._load_model(path)
    assert value.predict(5) == 11
    assert json.loads(raw)["format"] == "owned-affine-json/v1"


@pytest.mark.parametrize("raw", [b"not json", b"{}", b"x" * 4097])
def test_arbitrary_model_files_are_not_deserialized(tmp_path, raw):
    path = tmp_path / "owned.json"
    write_new_file(path, raw)
    with pytest.raises(ValueError):
        source._load_model(path)


def test_native_context_is_not_modified_and_actual_items_keep_slot_identity(tmp_path, monkeypatch):
    value = api(tmp_path, monkeypatch)
    items = [
        {"token": token, "x": x, "delay": 0, "fail": False, "deadline_ns": 0}
        for token, x in (("synthetic-1", 101), ("synthetic-2", 103))
    ]
    contexts = [{}, {}]
    assert value.predict(items, contexts) == [{"output": 203}, {"output": 207}]
    assert contexts == [{}, {}]
    events = source.read_events(tmp_path.glob("producer-*.jsonl"))
    entered = [e for e in events if e["kind"] == "predict_enter"]
    assert [(e["token"], e["slot"]) for e in entered] == [("synthetic-1", 0), ("synthetic-2", 1)]
    assert len({e["object_id"] for e in events if "object_id" in e}) == 1


def test_whole_batch_error_keeps_computed_sibling_and_collateral_census(tmp_path, monkeypatch):
    value = api(tmp_path, monkeypatch)
    items = [
        {"token": "synthetic-good", "x": 101, "delay": 0, "fail": False},
        {"token": "synthetic-bad", "x": 103, "delay": 0, "fail": True},
    ]
    with pytest.raises(ValueError, match="application failure"):
        value.predict(items, [{}, {}])
    events = source.read_events(tmp_path.glob("producer-*.jsonl"))
    terminal = [e for e in events if e["kind"] == "terminal"]
    assert [(e["token"], e["outcome"]) for e in terminal] == [
        ("synthetic-good", "success"),
        ("synthetic-bad", "error"),
    ]
    assert terminal[0]["collateral"] is True


def test_cooperative_refusal_prevents_designated_computation_but_not_a_claim_of_preemption(
    tmp_path, monkeypatch
):
    value = api(tmp_path, monkeypatch, "cooperative")

    class Refusal(Exception):
        def __init__(self, status, detail):
            self.status_code, self.detail = status, detail

    original = source.importlib.import_module
    monkeypatch.setattr(
        source.importlib,
        "import_module",
        lambda n: SimpleNamespace(HTTPException=Refusal) if n == "fastapi" else original(n),
    )
    with pytest.raises(Refusal):
        value.predict(
            [{"token": "synthetic-expired", "x": 101, "delay": 0, "fail": False, "deadline_ns": 0}],
            [{}],
        )
    events = source.read_events(tmp_path.glob("producer-*.jsonl"))
    assert not any(e["kind"] == "computed" for e in events)
    assert next(e for e in events if e["kind"] == "terminal")["outcome"] == "cancelled"


def test_plain_native_exception_bytes_remain_opaque():
    dangerous = b"pickle content must never be decoded"
    assert source._plain(dangerous) == {
        "raw_hex": dangerous.hex(),
        "encoding": "opaque_native_bytes",
    }


def test_http_body_and_response_annotations_are_runtime_types_not_strings():
    import inspect

    request = inspect.signature(source.EvidenceAPI.decode_request).parameters["request"].annotation
    response = inspect.signature(source.EvidenceAPI.encode_response).return_annotation
    assert not isinstance(request, str) and not isinstance(response, str)
    assert request == dict[str, source.Any]


def test_capture_cost_and_journal_have_explicit_measurement_boundary(tmp_path):
    writer = source._EventWriter(tmp_path)
    writer.emit("synthetic", "/a", "synthetic", {})
    events = source.read_events(tmp_path.glob("producer-*.jsonl"))
    cost = json.loads((tmp_path / f"capture-cost-{writer.pid}.json").read_bytes())
    assert cost["events"] == len(events) == 1
    assert cost["event_bytes"] == (tmp_path / f"producer-{writer.pid}.jsonl").stat().st_size
    assert cost["one_current_stats_update_not_included"] is True


def test_final_raw_reader_rejects_truncated_tail(tmp_path):
    path = tmp_path / "producer-1.jsonl"
    write_new_file(path, b'{"pid":1}')
    with pytest.raises(ValueError, match="incomplete"):
        source.read_events([path])


def test_guard_blocks_external_connect_but_not_owned_loopback(monkeypatch):
    import socket

    monkeypatch.setattr(source, "_event", lambda *a, **kw: None)
    sock = SimpleNamespace(family=socket.AF_INET)
    source._network_guard("socket.connect", (sock, ("127.0.0.1", 4321)))
    with pytest.raises(PermissionError):
        source._network_guard("socket.connect", (sock, ("203.0.113.1", 443)))
