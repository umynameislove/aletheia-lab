"""Synthetic boundaries only; these tests do not start or emulate a Ray cluster."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from aletheia_lab.evaluation import request_model_source as source


def test_original_census_preserves_external_singleton_orders() -> None:
    rows = source.planned_rows("original")
    assert len(rows) == 12
    assert len({row["token"] for row in rows}) == len(rows)
    assert not any(row["concurrent"] for row in rows)
    assert [row["requested_model"] for row in rows if row["scenario"] == "first_bbb"] == [
        "bbb",
        "aaa",
        "aaa",
        "bbb",
    ]
    assert {row["kind"] for row in rows if row["scenario"] == "non_batched"} == {"non_batched"}
    with pytest.raises(ValueError, match="mode"):
        source.planned_rows("mock_native")


def test_request_tokens_do_not_replace_native_selection_or_original_payload() -> None:
    response = SimpleNamespace(
        status_code=200, text='"original untouched"', headers={}, json=lambda: "original untouched"
    )
    client = SimpleNamespace(post=Mock(return_value=response))
    row = source.planned_rows("original")[1]
    result = source.request_row(client, "http://127.0.0.1:1/predict", row, "original")
    parameters = client.post.call_args.kwargs["params"]
    assert parameters == {"model_id": "bbb", "arg": "1", "kind": "non_batched"}
    assert result["output"] == "original untouched"
    assert result["response_text"] == response.text
    source.request_row(client, "http://127.0.0.1:1/predict", row, "owned_ml")
    parameters = client.post.call_args.kwargs["params"]
    assert parameters["model_id"] == "bbb"
    assert parameters["token"] == row["token"]


@pytest.mark.parametrize("status", [422, 500])
def test_native_http_failure_keeps_status_body_and_caller(status: int) -> None:
    response = SimpleNamespace(
        status_code=status, text="native error", headers={}, json=Mock(side_effect=ValueError)
    )
    client = SimpleNamespace(post=Mock(return_value=response))
    planned = source.planned_rows("owned_ml")[-1]
    result = source.request_row(client, "http://127.0.0.1:1/predict", planned, "owned_ml")
    assert result["token"] == planned["token"]
    assert result["http_status"] == status
    assert result["response_text"] == "native error"
    assert result["error"] == f"native_http_{status}"


def test_transport_failure_keeps_planned_census_and_exception() -> None:
    client = SimpleNamespace(post=Mock(side_effect=TimeoutError("synthetic transport")))
    planned = source.planned_rows("original")[0]
    result = source.request_row(client, "http://127.0.0.1:1/predict", planned, "original")
    assert result["http_status"] is None
    assert result["error"] == "TimeoutError: synthetic transport"
    output: dict[str, Any] = {"planned": source.planned_rows("original"), "rows": [result]}
    source.complete_census(output)
    assert [row["token"] for row in output["rows"]] == [row["token"] for row in output["planned"]]
    assert output["rows"][0] == result
    assert all(row["error"] == "unexecuted_after_native_failure" for row in output["rows"][1:])


def test_telemetry_values_restore_after_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAY_USAGE_STATS_ENABLED", "previous")
    monkeypatch.delenv("RAY_USAGE_STATS_REPORT_URL", raising=False)
    with pytest.raises(RuntimeError), source.local_environment():
        assert os.environ["RAY_USAGE_STATS_ENABLED"] == "0"
        assert os.environ["RAY_USAGE_STATS_REPORT_URL"].startswith("http://127.0.0.1:")
        raise RuntimeError("synthetic failure")
    assert os.environ["RAY_USAGE_STATS_ENABLED"] == "previous"
    assert "RAY_USAGE_STATS_REPORT_URL" not in os.environ


def test_refuses_existing_cluster_without_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ray = SimpleNamespace(is_initialized=lambda: True, shutdown=Mock(), init=Mock())
    fake_serve = SimpleNamespace(shutdown=Mock())
    monkeypatch.setattr(
        source.importlib,
        "import_module",
        lambda name: fake_ray if name == "ray" else fake_serve,
    )
    result = source.run_source(tmp_path / "fresh", "original")
    fake_ray.init.assert_not_called()
    fake_ray.shutdown.assert_not_called()
    fake_serve.shutdown.assert_not_called()
    assert len(result["rows"]) == len(result["planned"]) == 12
    assert "existing Ray cluster" in result["failures"][0]["error"]
    with pytest.raises(ValueError, match="fresh"):
        source.run_source(tmp_path / "fresh", "original")


def test_missing_native_dependency_records_every_unexecuted_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(name: str) -> Any:
        raise ImportError(f"unavailable {name}")

    monkeypatch.setattr(source.importlib, "import_module", unavailable)
    result = source.run_source(tmp_path / "fresh", "owned_ml")
    assert result["environment"] is None
    assert len(result["rows"]) == len(result["planned"])
    assert not result["loads"] and not result["batches"] and not result["startups"]
    assert result["failures"][0]["error"].startswith("ImportError:")
