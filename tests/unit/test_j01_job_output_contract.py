"""Common producer envelope and offline export checks; no runtime acceptance claim."""
from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted_contract.job_page import JobOutput
from aletheia_lab.product.view import ProductView

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = JobOutput[dict[str, object]]
EXAMPLE = {"schema_version": "product-api/v1-draft", "workspace_id": "ws_synthetic",
           "job_id": "job_synthetic", "operation": "preview", "result": {}}


def _main(name: str):
    return runpy.run_path(str(ROOT / "scripts" / name))["main"]


@pytest.mark.parametrize("patch", [
    {"schema_version": "product-api/v2"}, {"operation": "unknown"}, {"result": []},
    {"workspace_id": "other/workspace"}, {"job_id": ""}, {"extra": True},
])
def test_common_output_model_and_exported_schema_reject_invalid_boundary(patch):
    from jsonschema import Draft202012Validator

    body = {**EXAMPLE, **patch}
    with pytest.raises(ValidationError):
        OUTPUT.model_validate_json(json.dumps(body))
    assert not Draft202012Validator(OUTPUT.model_json_schema()).is_valid(body)


def test_common_envelope_requires_every_field_and_retains_operation_result():
    for missing in EXAMPLE:
        with pytest.raises(ValidationError):
            OUTPUT.model_validate_json(json.dumps({k: v for k, v in EXAMPLE.items() if k != missing}))
    for operation in ("preview", "confirm", "analyze", "follow_up"):
        body = {**EXAMPLE, "operation": operation, "result": {"nested": [None, 0, {"ok": True}]}}
        assert OUTPUT.model_validate_json(json.dumps(body)).model_dump(mode="json") == body


@pytest.fixture
def observations(tmp_path):
    """Synthetic test inputs exercise the reader; they are not observed ASGI evidence."""
    root = tmp_path / "observations"
    root.mkdir()
    view = json.loads((ROOT / "src/aletheia_lab/product/synthetic_p6_view.json").read_text(encoding="utf-8"))
    view["demo_only"] = False
    ProductView.model_validate_json(json.dumps(view))
    results = {"preview": {"mapping_candidates": {}}, "confirm": {"snapshot_id": view["snapshot"]["id"]}, "analyze": view}
    report = {"jobs": [], "snapshot_id": view["snapshot"]["id"], "result_id": view["result"]["id"]}
    for operation, result in results.items():
        body = {**EXAMPLE, "job_id": "job_" + operation, "operation": operation, "result": result}
        (root / f"{operation}.json").write_text(json.dumps(body), encoding="utf-8")
        report["jobs"].append({"operation": operation, "job_id": body["job_id"]})
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "source.json").write_text(json.dumps({"workspace_id": EXAMPLE["workspace_id"]}), encoding="utf-8")
    return root


def _check(root):
    from jsonschema import FormatChecker

    if "date-time" not in FormatChecker().checkers:
        pytest.skip("Independent checker requires jsonschema[format] date-time support")
    _main("check_j01_schema.py")([str(root)])


def test_export_observed_outputs_matches_real_models_schema_and_byte_manifest(tmp_path, observations):
    target = tmp_path / "export"
    _main("export_j01_review.py")([str(target), "--observed-output-dir", str(observations)])
    assert json.loads((target / "job-output.schema.json").read_text()) == OUTPUT.model_json_schema()
    for operation in ("preview", "confirm", "analyze"):
        original = OUTPUT.model_validate_json((observations / f"{operation}.json").read_bytes())
        assert json.loads((target / f"job-output-{operation}.json").read_text()) == original.model_dump(mode="json")
    _check(target)
    sums = dict(line.split("  ", 1)[::-1] for line in (target / "SHA256SUMS").read_text().splitlines())
    assert set(sums) == {path.name for path in target.iterdir() if path.name != "SHA256SUMS"}
    assert all(hashlib.sha256((target / name).read_bytes()).hexdigest() == digest for name, digest in sums.items())
    original = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(FileExistsError):
        _main("export_j01_review.py")([str(target), "--observed-output-dir", str(observations)])
    assert original == {path.name: path.read_bytes() for path in target.iterdir()}


@pytest.mark.parametrize("patch", [{"workspace_id": "ws_other"}, {"job_id": "job_other"}, {"operation": "confirm"}])
def test_export_rejects_observed_filename_or_run_scope_mismatch_before_writing(tmp_path, observations, patch):
    path = observations / "preview.json"
    value = {**json.loads(path.read_text()), **patch}
    path.write_text(json.dumps(value), encoding="utf-8")
    target = tmp_path / "rejected"
    with pytest.raises(ValueError, match="scope"):
        _main("export_j01_review.py")([str(target), "--observed-output-dir", str(observations)])
    assert not target.exists()


@pytest.mark.parametrize("patch", [{"demo_only": True}, {"schema_version": "p6-product-view/v2"}])
def test_export_rejects_demo_or_invalid_analysis(tmp_path, observations, patch):
    path = observations / "analyze.json"
    value = json.loads(path.read_text())
    value["result"].update(patch)
    path.write_text(json.dumps(value), encoding="utf-8")
    target = tmp_path / "rejected"
    with pytest.raises(ValueError):
        _main("export_j01_review.py")([str(target), "--observed-output-dir", str(observations)])
    assert not target.exists()


def test_new_schema_without_observations_and_legacy_v7_both_validate(tmp_path):
    target = tmp_path / "export"
    _main("export_j01_review.py")([str(target)])
    assert (target / "job-output.schema.json").is_file()
    assert not list(target.glob("job-output-*.json"))
    _check(target)
    _check(ROOT / "fixtures/j01/producer-review-v7")


@pytest.mark.parametrize("patch", [{"extra": True}, {"schema_version": "product-api/v2"}, {"operation": "unknown"}, {"result": []}])
def test_independent_checker_rejects_corrupt_exported_output(tmp_path, observations, patch):
    from jsonschema.exceptions import ValidationError as SchemaValidationError

    target = tmp_path / "export"
    _main("export_j01_review.py")([str(target), "--observed-output-dir", str(observations)])
    path = target / "job-output-preview.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **patch}), encoding="utf-8")
    with pytest.raises(SchemaValidationError):
        _check(target)
