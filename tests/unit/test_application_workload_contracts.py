"""Fake application boundary with actual obligation and SQLite service checks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from aletheia_lab.evaluation import application_audit_workload as workload
from aletheia_lab.project.identity import content_sha256


class SourceBoundary:
    def __init__(self, stack, directory):
        directory.mkdir(parents=True)
        self.stack, self.directory = stack, directory
        self.payloads = {"A": b"inert-owned-A", "B": b"inert-owned-B"}
        self.domain = sorted(content_sha256(raw) for raw in self.payloads.values())
        self.fingerprints = {"A": "owned-object-A", "B": "owned-object-B"}
        self.capture = SimpleNamespace(receipts=[], reference=[])
        self.selected, self.paths = "A", dict(self.payloads)
        self.start_attempts, self.http_requests = 0, 0
        self.apps = []
        self.models = {
            name: SimpleNamespace(predict=lambda values: np.asarray([0, 0, 1, 1]))
            for name in self.payloads
        }

    def select(self, name):
        self.selected = name

    def replace(self, selected, actual):
        self.paths[selected] = self.payloads[actual] if actual else b"invalid-owned-artifact"

    async def start(self, scope):
        self.start_attempts += 1
        raw = self.paths[self.selected]
        self.capture.receipts.append({"scope": scope, "descriptor_sha256": content_sha256(raw)})
        self.capture.reference.append({"scope": scope, "raw_hex": raw.hex()})
        if raw not in self.payloads.values():
            raise ValueError("synthetic loader refused owned invalid bytes")
        actual = next(name for name, payload in self.payloads.items() if raw == payload)

        async def request():
            self.http_requests += 1
            return {"object_fingerprint": self.fingerprints[actual], "predictions": [0, 0, 1, 1]}

        app = SimpleNamespace(request=request, close=AsyncMock())
        self.apps.append(app)
        return app

    def environment(self):
        return {"stack": self.stack, "classification": "synthetic-sdk-boundary"}


@pytest.mark.parametrize(
    "step,gold,actual",
    [
        ("initial", "compliant", "A"),
        ("lawful_change", "compliant", "B"),
        ("wrong_b", "violation", "A"),
        ("failed_b", "unknown", None),
        ("restore_b", "compliant", "B"),
        ("wrong_a", "violation", "B"),
        ("restore_a", "compliant", "A"),
    ],
)
def test_attempt_keeps_raw_reference_separate_from_receipts_and_closes_app(
    tmp_path, step, gold, actual
):
    source = SourceBoundary("fixture", tmp_path / "source")
    row = asyncio.run(workload.attempt(source, "offered", step, 2))
    assert row["gold"] == gold and row["object_model"] == actual
    assert row["frame"]["scope"] == "offered" and row["frame"]["step"] == 2
    assert row["frame"]["closed"] is True and row["frame"]["generation"] is None
    assert row["frame"]["count"] == len(row["raw_reference"]) == 1
    assert (row["startup_error"] == "ValueError") is (actual is None)
    assert row["load_ns"] >= 0 and row["inference_ns"] >= 0
    if source.apps:
        source.apps[-1].close.assert_awaited_once()


def test_request_failure_still_closes_the_application(tmp_path):
    source = SourceBoundary("fixture", tmp_path / "source")
    original = source.start

    async def fails(scope):
        app = await original(scope)
        app.request = AsyncMock(side_effect=OSError("synthetic inference failure"))
        return app

    source.start = fails
    with pytest.raises(OSError, match="inference failure"):
        asyncio.run(workload.attempt(source, "offered", "initial", 0))
    source.apps[0].close.assert_awaited_once()


def test_retained_application_control_does_not_confuse_path_with_resident_state(tmp_path):
    source = SourceBoundary("fixture", tmp_path / "source")
    rows = asyncio.run(workload.cache_control(source))
    assert [row["case"] for row in rows] == [
        "retained_application_selection_move",
        "fresh_application_selection",
        "path_replacement_not_resident",
        "failed_new_app_prior_app_survives",
    ]
    assert rows[0]["before"] == rows[0]["after"]
    assert rows[1]["before"]["object_fingerprint"] != rows[1]["after"]["object_fingerprint"]
    assert rows[2]["before"] == rows[2]["after"] == rows[3]["after"]
    assert rows[3]["startup_error"] == "ValueError"
    assert source.paths["B"] == source.payloads["B"]
    assert len(source.apps) == 2 and all(app.close.await_count == 1 for app in source.apps)


def test_census_signing_boundary_receives_actual_expected_and_observed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "ApplicationSource", SourceBoundary)
    signatures = []

    def signer(expected, observed, path):
        signatures.append((expected, observed))
        return "pass" if expected == observed else "fail"

    monkeypatch.setattr(workload, "verify_chain", signer)
    result = asyncio.run(workload.census("fixture", tmp_path / "census"))
    assert [row["gold"] for row in result["rows"]] == [
        "compliant",
        "compliant",
        "violation",
        "unknown",
        "compliant",
        "violation",
        "compliant",
    ]
    assert [row["signed_provenance"] for row in result["rows"]] == [
        row["gold"] for row in result["rows"]
    ]
    assert len(signatures) == 6
    assert result["reference_capture_count"] == result["start_attempts"] == 10
    assert set(result["releases"]) == {"A", "B"}
    assert result["releases"]["A"]["sha256"] == content_sha256(b"inert-owned-A")


@pytest.mark.parametrize("budget", [1024, 8192])
@pytest.mark.parametrize("policy", workload.POLICIES)
def test_actual_admission_refusal_and_delayed_query_are_counted(
    tmp_path, monkeypatch, budget, policy
):
    monkeypatch.setattr(workload, "ApplicationSource", SourceBoundary)
    result = asyncio.run(workload.service_arm("fixture", tmp_path / "arm", policy, budget))
    assert len(result["rows"]) == 7 and result["budget"] == budget
    assert result["peak_charged_bytes"] <= budget and result["overruns"] == 0
    if budget == 1024 and policy.startswith("reserve"):
        assert result["start_attempts"] == result["http_requests"] == 0
        assert all(row["status"] == "refused_before_dispatch" for row in result["rows"])
        assert result["queries"] == []
    else:
        assert result["start_attempts"] == 7 and result["http_requests"] == 6
        assert result["rows"][3]["status"] == "native_startup_failed"
        assert result["rows"][3]["audit_accepted"] is False
        hard = [query for query in result["queries"] if query["hard"]]
        assert len(hard) == sum(row.get("audit_accepted", False) for row in result["rows"])
        assert all(query["decision"]["verdict"] == query["gold"] for query in hard)
        if budget == 8192:
            assert len(hard) == 6
        assert all(query["query_ns"] >= 0 for query in result["queries"])
        assert result["physical_database_bytes"] > 0
