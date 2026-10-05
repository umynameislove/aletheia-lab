"""Shared ProductView schema and reference-integrity contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.product import ProductService
from aletheia_lab.product.view import ProductView

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_p6_view.json"
_PACKAGED_FIXTURE = (
    Path(__file__).parents[2] / "src" / "aletheia_lab" / "product" / "synthetic_p6_view.json"
)
_FIXTURE_SHA256 = "634876aa5033fc62dbf74add537a4733957e1082a718d01b8ef44a6c8464fc3c"


def _payload() -> dict[str, object]:
    value = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validate(payload: dict[str, object]) -> ProductView:
    return ProductView.model_validate_json(json.dumps(payload))


def test_shared_fixture_is_exact_and_round_trips_through_product_view() -> None:
    raw = _FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256
    assert _PACKAGED_FIXTURE.read_bytes() == raw

    view = ProductView.model_validate_json(raw)

    assert view.model_dump(mode="json") == _payload()
    assert ProductView.model_validate_json(view.model_dump_json()) == view
    assert view.runtime.external_call is False
    assert view.conversation.turns[0].result_id == view.result.id
    assert view.conversation.turns[0].runtime == view.runtime
    assert view.result.denominators.independent_families is None


def test_demo_view_returns_a_fresh_exact_fixture_after_restart(tmp_path: Path) -> None:
    expected = _payload()

    first = ProductService(tmp_path / "store").demo_view()
    first["demo_only"] = False
    second = ProductService(tmp_path / "store").demo_view()

    assert second == expected
    assert second["runtime"] == {
        "provider": "deterministic_mock",
        "model": "synthetic-demo-only",
        "external_call": False,
    }


def test_product_view_rejects_missing_extra_and_wrong_typed_fields() -> None:
    missing = _payload()
    missing.pop("runtime")
    with pytest.raises(ValidationError):
        _validate(missing)

    missing_turn_result = _payload()
    conversation = missing_turn_result["conversation"]
    assert isinstance(conversation, dict)
    turns = conversation["turns"]
    assert isinstance(turns, list)
    turns[0].pop("result_id")
    with pytest.raises(ValidationError):
        _validate(missing_turn_result)

    mismatched_turn_runtime = _payload()
    conversation = mismatched_turn_runtime["conversation"]
    assert isinstance(conversation, dict)
    turns = conversation["turns"]
    assert isinstance(turns, list)
    runtime = turns[0]["runtime"]
    assert isinstance(runtime, dict)
    runtime["model"] = "different-runtime"
    with pytest.raises(ValidationError, match="runtime"):
        _validate(mismatched_turn_runtime)

    extra = _payload()
    extra["evaluator_truth"] = "hidden"
    with pytest.raises(ValidationError):
        _validate(extra)

    wrong_type = _payload()
    result = wrong_type["result"]
    assert isinstance(result, dict)
    result["status"] = 1
    with pytest.raises(ValidationError):
        _validate(wrong_type)


def test_product_view_rejects_duplicate_and_dangling_references() -> None:
    duplicate = _payload()
    evidence = duplicate["evidence"]
    assert isinstance(evidence, list)
    evidence.append(dict(evidence[0]))
    with pytest.raises(ValidationError, match="unique"):
        _validate(duplicate)

    dangling = _payload()
    claims = dangling["claims"]
    assert isinstance(claims, list)
    claims[0]["citation_ids"] = ["demo-evidence-missing"]
    with pytest.raises(ValidationError, match="unavailable evidence"):
        _validate(dangling)

    dangling_edge = _payload()
    graph = dangling_edge["graph"]
    assert isinstance(graph, dict)
    graph["edges"][0]["target"] = "demo-node-missing"
    with pytest.raises(ValidationError, match="dangling edge"):
        _validate(dangling_edge)


def test_product_view_rejects_hidden_causal_and_zero_for_unknown_family_count() -> None:
    hidden = _payload()
    evidence = hidden["evidence"]
    assert isinstance(evidence, list)
    evidence[0]["visibility"] = "evaluator"
    with pytest.raises(ValidationError):
        _validate(hidden)

    causal = _payload()
    graph = causal["graph"]
    assert isinstance(graph, dict)
    graph["edges"][0]["kind"] = "CAUSES"
    with pytest.raises(ValidationError, match="causal"):
        _validate(causal)

    fabricated_zero = _payload()
    result = fabricated_zero["result"]
    assert isinstance(result, dict)
    denominators = result["denominators"]
    assert isinstance(denominators, dict)
    denominators["independent_families"] = 0
    with pytest.raises(ValidationError):
        _validate(fabricated_zero)
