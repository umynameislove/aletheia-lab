"""Reference and pinned source guards; ordinary CI never runs reserved frames."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from aletheia_lab.evaluation import serving_protocol_native as native
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.serving_protocol_reference import (
    bento_batch_member_reference,
    bento_operand_reference,
    torch_conformance,
    torch_core_reference,
)
from aletheia_lab.evaluation.serving_protocol_transfer import (
    check_condition_census,
    check_transfer_result,
    mapping_specs,
    response_specs,
    summarize_transfer,
)
from aletheia_lab.project.identity import content_sha256


def test_named_operand_reference_does_not_mirror_native_mapper() -> None:
    def method(self: Any, x: Any, *extras: Any, mask: Any, **options: Any) -> None:
        pass

    result = bento_operand_reference(
        method, [1, 2, 3], {"mask": 4, "option": 5}, {"x": 0, "extras": 1, "mask": -1, "options": 0}
    )
    assert result["axes"] == {0: 0, 1: 1, 2: 1, "mask": -1, "option": 0}
    assert result["request_operand_census"] == 5
    with pytest.raises(TypeError):
        bento_operand_reference(method, [], {"mask": 1}, {"mask": 0})


def test_independent_batch_reference_checks_members_shapes_and_dtype() -> None:
    arrays = [
        np.arange(2, dtype=np.int32).reshape(2, 1),
        np.arange(6, dtype=np.float32).reshape(2, 3),
    ]
    expected = bento_batch_member_reference(arrays, -1)
    assert expected["indices"] == [0, 1, 4]
    assert expected["batch"].dtype == np.result_type(*arrays)
    assert np.array_equal(expected["batch"][:, 0:1], arrays[0])
    assert np.array_equal(expected["batch"][:, 1:4], arrays[1])
    with pytest.raises(ValueError, match="shape"):
        bento_batch_member_reference([np.ones((2, 1)), np.ones((3, 2))], 1)


def test_response_reference_checks_wire_width_and_every_output_field() -> None:
    payload = {
        "id": "i",
        "model_name": "m",
        "outputs": [{"name": "a", "shape": [2], "datatype": "FP32", "data": [0.1, -2.4]}],
    }
    expected = torch_core_reference(payload)
    assert expected["outputs"][0]["data"] != payload["outputs"][0]["data"]
    contents = SimpleNamespace(fp32_contents=expected["outputs"][0]["data"])
    value = SimpleNamespace(
        DESCRIPTOR=SimpleNamespace(full_name="inference.ModelInferResponse"),
        id="i",
        model_name="m",
        outputs=[SimpleNamespace(name="a", shape=[2], datatype="FP32", contents=contents)],
    )
    assert torch_conformance(value, json.dumps(payload).encode(), "inference.ModelInferResponse")[
        "conforms"
    ]
    value.outputs[0].name = "other"
    assert not torch_conformance(value, payload, "inference.ModelInferResponse")["conforms"]
    assert not torch_conformance(payload, payload, "inference.ModelInferResponse")["conforms"]


def test_reference_rejects_shape_domain_before_conformance() -> None:
    payload = {
        "model_name": "m",
        "outputs": [{"name": "a", "shape": [3], "datatype": "FP32", "data": [1, 2]}],
    }
    with pytest.raises(ValueError, match="shape"):
        torch_core_reference(payload)


def test_source_hash_guard_and_unchanged_synthetic_ast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = b"class Fixture:\n    def method(self, operand):\n        return operand + 7\n"
    path = tmp_path / "fixture.py"
    path.write_bytes(source)
    monkeypatch.setitem(native.PINS, "fixture.py", content_sha256(source))
    roots = native.SourceRoots(tmp_path, tmp_path)
    selected = native._exact_class(roots, "fixture.py", "Fixture", {})
    assert selected().method(1) == 8
    assert list(inspect.signature(selected.method).parameters) == ["self", "operand"]
    path.write_bytes(source + b"# changed\n")
    with pytest.raises(ValueError, match="pin"):
        native._exact_class(roots, "fixture.py", "Fixture", {})
    with pytest.raises(ValueError, match="filename"):
        native.checked_source(roots, "../fixture.py")


def test_condition_census_and_unidentified_repair_scope() -> None:
    assert len(mapping_specs()) == 8 and len(response_specs()) == 6
    assert 8 * 2 + 2 * 2 + 6 * 2 * 4 * 2 == 116
    summary = summarize_transfer([])
    assert summary["F1_new_enrollment"] == "outside eligible domain"
    assert summary["F3_component_repair"].startswith("unidentified")


def _synthetic_census() -> list[dict[str, Any]]:
    rows = []
    for side in ("affected", "fixed"):
        for spec in mapping_specs():
            rows.append({"family": "BentoML#2469", "side": side, "condition": spec["id"]})
        for route in ("sync", "async"):
            rows.append(
                {"family": "BentoML#2469", "side": side, "condition": f"native-local-{route}"}
            )
        for spec in response_specs():
            for request in ("infer", "protobuf"):
                for route in ("direct", "grpc", "json", "no-header"):
                    rows.append(
                        {
                            "family": "TorchServe#2566",
                            "side": side,
                            "condition": spec["case_id"],
                            "request_kind": request,
                            "route": route,
                        }
                    )
    for row in rows:
        row.update(native_conforms=True, native_error=None, baseline_verdict="conforms")
    return rows


def test_retained_condition_census_rejects_duplicates_and_missing_rows() -> None:
    rows = _synthetic_census()
    check_condition_census(rows)
    with pytest.raises(ValueError, match="census"):
        check_condition_census(rows[:-1])
    rows[-1] = rows[0]
    with pytest.raises(ValueError, match="census"):
        check_condition_census(rows)


def test_rehashed_analysis_cannot_move_result_to_another_plan() -> None:
    rows = _synthetic_census()
    plan = {"plan_sha256": "prospective-synthetic-plan"}
    result = {
        "plan_sha256": plan["plan_sha256"],
        "rows": rows,
        "analysis": summarize_transfer(rows),
    }
    result["results_sha256"] = content_sha256(encode(result).encode())
    check_transfer_result(result, plan)
    with pytest.raises(ValueError, match="binding"):
        check_transfer_result(result, {"plan_sha256": "different-plan"})
    result["analysis"]["conditions"] = 115
    body = {key: value for key, value in result.items() if key != "results_sha256"}
    result["results_sha256"] = content_sha256(encode(body).encode())
    with pytest.raises(ValueError, match="analysis"):
        check_transfer_result(result, plan)
