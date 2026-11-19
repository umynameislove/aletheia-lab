"""V2 metric dimensions survive persistence, follow-up and offline reports."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.product import ProductService
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.results import load_product_result, persist_product_result
from aletheia_lab.product.view import (
    ProductMetricChangeV2,
    ProductSnapshotV2,
    ProductView,
    ProductViewV2,
    parse_product_view_json,
)


def _view() -> ProductViewV2:
    payload = json.loads((Path(__file__).parents[1] / "fixtures/synthetic_p6_view.json").read_text())
    payload["schema_version"] = "p6-product-view/v2"
    payload["demo_only"] = False
    project_id, snapshot_id, baseline_id = "p3-project-" + "1" * 64, "p3-snapshot-" + "2" * 64, "p3-snapshot-" + "3" * 64
    result_id = "p6-result-" + "0" * 64
    payload["project"]["id"] = project_id
    payload["snapshot"].update(id=snapshot_id, baseline_id=baseline_id)
    payload["result"]["id"] = result_id
    payload["conversation"]["turns"][0].update(snapshot_id=snapshot_id, result_id=result_id)
    for node in payload["graph"]["nodes"]:
        if node["kind"] == "Snapshot":
            node["source_id"] = snapshot_id
        elif node["kind"] == "Disposition":
            node["source_id"] = result_id
    for definition in payload["snapshot"]["metric_definitions"]:
        definition["unit"] = "%"
    for change in payload["snapshot"]["metric_changes"]:
        for field in ("before", "after"):
            if change[field] is not None:
                change[field].update(snapshot_id=baseline_id if field == "before" else snapshot_id,
                                     class_label="không bệnh", unit="%", unit_origin="source")
    return ProductViewV2.model_validate_json(json.dumps(payload))


def test_v2_requires_explicit_metadata_and_does_not_reinterpret_v1() -> None:
    original = (Path(__file__).parents[1] / "fixtures/synthetic_p6_view.json").read_bytes()
    old = parse_product_view_json(original)
    assert type(old) is ProductView
    view = _view()
    assert type(parse_product_view_json(view.model_dump_json())) is ProductViewV2
    with pytest.raises(ValidationError):
        ProductView.model_validate_json(view.model_dump_json())
    payload = view.model_dump(mode="json")
    del payload["snapshot"]["metric_changes"][0]["before"]["class_label"]
    with pytest.raises(ValidationError):
        ProductViewV2.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field,value", [("class_label", "bệnh"), ("unit", "ratio")])
def test_different_dimensions_never_form_one_numeric_change(field: str, value: str) -> None:
    payload = _view().snapshot.metric_changes[0].model_dump(mode="json")
    payload["after"][field] = value
    with pytest.raises(ValidationError, match="class and unit"):
        ProductMetricChangeV2.model_validate_json(json.dumps(payload))


def test_class_is_part_of_change_identity_and_global_policy_applies_to_each_class() -> None:
    payload = _view().snapshot.model_dump(mode="json")
    other = copy.deepcopy(payload["metric_changes"][0])
    other["evidence_id"] += "-second"
    for field in ("before", "after"):
        other[field]["class_label"] = "bệnh"
    payload["metric_changes"].append(other)
    snapshot = ProductSnapshotV2.model_validate_json(json.dumps(payload))
    assert len(snapshot.metric_changes) == 2
    other["before"]["class_label"] = other["after"]["class_label"] = "không bệnh"
    with pytest.raises(ValidationError, match="identities"):
        ProductSnapshotV2.model_validate_json(json.dumps(payload))
    other["before"]["class_label"] = other["after"]["class_label"] = "bệnh"
    other["adverse_status"] = "not_adverse"
    with pytest.raises(ValidationError, match="declared policy"):
        ProductSnapshotV2.model_validate_json(json.dumps(payload))


def test_unknown_unit_is_explicit_and_cannot_claim_source_or_declared_provenance() -> None:
    payload = _view().model_dump(mode="json")
    for definition in payload["snapshot"]["metric_definitions"]:
        definition["unit"] = None
    for change in payload["snapshot"]["metric_changes"]:
        for field in ("before", "after"):
            if change[field] is not None:
                change[field].update(unit=None, unit_origin="unknown")
    ProductViewV2.model_validate_json(json.dumps(payload))
    payload["snapshot"]["metric_changes"][0]["after"]["unit_origin"] = "source"
    with pytest.raises(ValidationError, match="unknown unit"):
        ProductViewV2.model_validate_json(json.dumps(payload))


def test_v2_history_follow_up_and_reports_keep_exact_class_unit_and_values(tmp_path: Path) -> None:
    store = tmp_path / "store"
    ProductService(store)
    original = _view()
    saved = persist_product_result(store, original)
    selection = resolve_product_follow_up_selection(saved, selection_kind="claim", selection_id=saved.claims[0].id, question="What remains uncertain?")
    child = build_product_follow_up_view(saved, selection)
    saved_child = persist_product_result(store, child, parent_result_id=saved.result.id)
    assert type(saved_child) is ProductViewV2
    assert load_product_result(store, saved.result.id) == saved
    assert load_product_result(store, saved_child.result.id) == saved_child
    assert saved_child.snapshot == original.snapshot
    json_report = json.loads(render_product_report(saved_child, "json"))
    assert json_report["snapshot"] == original.model_dump(mode="json")["snapshot"]
    markdown = render_product_report(saved_child, "markdown").decode()
    assert "không bệnh" in markdown and "unit_origin=`source`" in markdown and "unit=`%`" in markdown


def test_dimension_labels_remain_literal_in_markdown_and_pdf_text() -> None:
    from aletheia_lab.product.reports import _pdf_lines
    label = "`<b>class``</b>`"
    payload = _view().model_dump(mode="json")
    for change in payload["snapshot"]["metric_changes"]:
        for side in ("before", "after"):
            if change[side] is not None:
                change[side]["class_label"] = label
    view = ProductViewV2.model_validate_json(json.dumps(payload))
    markdown = render_product_report(view, "markdown").decode()
    assert "class=``` `" + "<b>class``</b>` ```" in markdown
    assert label in "\n".join(_pdf_lines(markdown, literal_code_spans=True))
