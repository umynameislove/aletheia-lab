from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import onnx_packaging_exposure as study
from aletheia_lab.evaluation.onnx_packaging_review import verify_retained


def test_selection_is_order_invariant_and_snapshot_pinned() -> None:
    rows = [{"id": f"org/model-{i}", "sha": f"{i:040x}", "private": False} for i in range(40)]
    assert study.select_frame(rows) == study.select_frame(list(reversed(rows)))
    assert len(study.select_frame(rows)) == 30
    assert all(len(row["sha"]) == 40 for row in study.select_frame(rows))
    with pytest.raises(ValueError, match="duplicate"):
        study.select_frame(rows + [rows[0]])
    assert study.select_frame([{"id": "org/no-pin"}]) == [{"repo": "org/no-pin", "sha": ""}]
    assert study._allowed_url("https://us.aws.cdn.hf.co/public")
    assert not study._allowed_url("https://evilcdn.hf.co/public")


def test_filename_markers_are_not_graph_truth() -> None:
    info = {
        "sha": "a" * 40,
        "siblings": [
            {"rfilename": "model.onnx", "size": 10},
            {"rfilename": "unrelated.data", "size": 50},
            {"rfilename": "model.sig.json", "size": 30},
        ],
    }
    metadata = study.packaging_metadata(info, "a" * 40)
    assert metadata["filename_candidate"] is True
    assert "valid_external_reference_count" not in metadata
    assert metadata["signature_filename_markers"] == ["model.sig.json"]
    with pytest.raises(ValueError, match="commit"):
        study.packaging_metadata(info, "b" * 40)


@pytest.mark.parametrize("name", ["../weights", "/weights", "x\\weights"])
def test_unsafe_metadata_paths_fail_closed(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        study.packaging_metadata({"sha": "a", "siblings": [{"rfilename": name}]}, "a")


@pytest.mark.parametrize(
    "url",
    ["http://huggingface.co/x", "https://huggingface.co.evil/x", "https://user@huggingface.co/x"],
)
def test_public_host_scope(url: str) -> None:
    assert not study._allowed_url(url)
    with pytest.raises(ValueError, match="outside"):
        study.fetch_public(url, 10)


def test_partial_graph_scan_is_not_absence_and_failures_stay_in_denominator() -> None:
    rows = [
        {
            "repo": "a/a",
            "metadata_status": "complete",
            "graph_count": 2,
            "filename_candidate": False,
            "signature_filename_markers": [],
            "graphs": [
                {
                    "status": "parsed",
                    "external_tensor_count": 0,
                    "valid_external_reference_count": 0,
                }
            ],
        },
        {"repo": "a/b", "metadata_status": "failed", "graphs": []},
        {
            "repo": "a/c",
            "metadata_status": "complete",
            "graph_count": 1,
            "filename_candidate": False,
            "signature_filename_markers": [],
            "graphs": [
                {
                    "status": "parsed",
                    "external_tensor_count": 1,
                    "valid_external_reference_count": 1,
                }
            ],
        },
    ]
    summary = study.summarize(rows)
    assert summary["sampled_repositories"] == 3
    assert summary["graph_confirmed_external_repositories"] == ["a/c"]
    assert summary["all_graphs_parsed_no_external_reference_repositories"] == []
    assert summary["external_reference_unverified_repositories"] == 2
    assert "unsigned models" in summary["does_not_measure"]


def test_prepare_is_exclusive_and_plan_tampering_blocks(tmp_path: Path) -> None:
    output = tmp_path / "new"
    study.prepare(output)
    assert study.check_plan(output)["contract"] == study.CONTRACT
    with pytest.raises(FileExistsError):
        study.prepare(output)
    plan = json.loads((output / "plan.json").read_bytes())
    plan["contract"]["sample_limit"] = 999
    (output / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        study.check_plan(output)


def test_fake_census_keeps_metadata_failure_and_prevents_second_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "new"
    study.prepare(output)

    def fake(url: str, limit: int) -> bytes:
        if url == study.FRAME_URL:
            return study.encode([{"id": "org/model", "sha": "a" * 40}])
        raise OSError("unavailable")

    monkeypatch.setattr(study, "fetch_public", fake)
    result = study.collect(output)
    assert result["analysis"]["sampled_repositories"] == 1
    assert result["analysis"]["metadata_failed"] == 1
    before = (output / "results.json").read_bytes()
    with pytest.raises(FileExistsError):
        study.collect(output)
    assert (output / "results.json").read_bytes() == before


def test_onnx_external_tensors_in_attributes_and_malformed_locations() -> None:
    onnx: Any = pytest.importorskip("onnx")
    tensor = onnx.TensorProto(
        name="w",
        data_type=onnx.TensorProto.FLOAT,
        dims=[1],
        data_location=onnx.TensorProto.EXTERNAL,
    )
    tensor.external_data.add(key="location", value="arbitrary.weights")
    node = onnx.helper.make_node("Constant", [], ["out"], value=tensor)
    graph = onnx.helper.make_graph(
        [node], "g", [], [onnx.helper.make_tensor_value_info("out", 1, [1])]
    )
    model = onnx.helper.make_model(graph)
    result = study.inspect_onnx(model.SerializeToString())
    assert result["valid_external_reference_count"] == 1
    assert result["references"][0]["locations"] == ["arbitrary.weights"]
    node.attribute[0].t.external_data.add(key="location", value="other")
    graph.node[0].CopyFrom(node)
    model.graph.CopyFrom(graph)
    assert study.inspect_onnx(model.SerializeToString())["valid_external_reference_count"] == 0
    with pytest.raises(ValueError, match="model graph"):
        study.inspect_onnx(b"")


def test_offline_reviewer_and_transport_recovery_keep_exact_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    study.prepare(first)
    frame = [{"id": "org/model", "sha": "a" * 40}]
    metadata = {"sha": "a" * 40, "siblings": [{"rfilename": "README.md", "size": 1}]}
    monkeypatch.setattr(
        study,
        "fetch_public",
        lambda url, cap: study.encode(frame if url == study.FRAME_URL else metadata),
    )
    result = study.collect(first)
    assert verify_retained(first)["downloads"] == 0
    second = tmp_path / "second"
    study.prepare(second, first)
    monkeypatch.setattr(
        study, "fetch_public", lambda *args: pytest.fail("frozen metadata must not be refetched")
    )
    assert study.collect(second)["analysis"] == result["analysis"]
    assert verify_retained(second)["verification"] == "pass"
    graph_success = json.loads((first / "results.json").read_bytes())
    graph_success["retained_graph_bytes"] = 1
    (first / "results.json").write_text(json.dumps(graph_success), encoding="utf-8")
    with pytest.raises(ValueError, match="successful"):
        study.prepare(tmp_path / "third", first)
    (second / "row-00.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="row identity"):
        verify_retained(second)
