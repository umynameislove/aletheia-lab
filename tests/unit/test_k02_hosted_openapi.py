"""An offline export must never accept a mixture of producer source snapshots."""
from __future__ import annotations

import copy
import json
import runpy
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def test_changed_source_aborts_before_writing_a_review_artifact(tmp_path, monkeypatch):
    namespace = runpy.run_path(str(ROOT / "scripts/export_hosted_openapi.py"))
    main = namespace["main"]
    state = main.__globals__
    snapshots = iter(({"src/producer.py": "a" * 64}, {"src/producer.py": "b" * 64}))
    monkeypatch.setitem(state, "sources", lambda repo: next(snapshots))
    monkeypatch.setitem(state, "document", lambda repo: ({}, {}, [], []))
    monkeypatch.setitem(state, "disposition", lambda spec, inventory: [])
    monkeypatch.setitem(state, "self_check", lambda *args: {})
    output = tmp_path / "review"
    monkeypatch.setattr(sys, "argv", ["export_hosted_openapi.py", "--repo", str(ROOT),
        "--inventory", str(tmp_path / "inventory.csv"), "--output", str(output)])
    with pytest.raises(RuntimeError, match="source changed"):
        main()
    assert not output.exists()


@pytest.fixture(scope="module")
def exported_contract():
    namespace = runpy.run_path(str(ROOT / "scripts/export_hosted_openapi.py"))
    return namespace, namespace["document"](ROOT)


@pytest.mark.parametrize("operation,missing", [("refresh", "previous_snapshot_id"),
    ("refresh", "revision"), ("follow_up", "parent_result_id"), ("follow_up", "snapshot_id")])
def test_job_output_schema_keeps_the_operation_specific_result(exported_contract, operation, missing):
    _, (spec, _, examples, _) = exported_contract
    value = copy.deepcopy(next(v for _, v in examples if v.get("operation") == operation))
    shape = spec["paths"]["/v1/workspaces/{workspace_id}/jobs/{job_id}/output"]["get"][
        "responses"]["200"]["content"]["application/json"]["schema"]
    validator = Draft202012Validator({**shape, "components": spec["components"]})
    assert validator.is_valid(value)
    del value["result"][missing]
    assert not validator.is_valid(value)


def test_job_output_schema_checks_both_analysis_versions_and_legacy_contract(exported_contract):
    _, (spec, _, _, _) = exported_contract
    shape = spec["paths"]["/v1/workspaces/{workspace_id}/jobs/{job_id}/output"]["get"][
        "responses"]["200"]["content"]["application/json"]["schema"]
    validator = Draft202012Validator({**shape, "components": spec["components"]})
    for fixture in (ROOT / "fixtures/k04/producer-20261111-shared-contract/job-output.json",
                    ROOT / "fixtures/metric-dimensions-hosted-v2/analyze-output.json"):
        value = json.loads(fixture.read_text(encoding="utf-8"))
        assert validator.is_valid(value), list(validator.iter_errors(value))
        invalid = copy.deepcopy(value)
        del invalid["result"]["schema_version"]
        assert not validator.is_valid(invalid)
        invalid = copy.deepcopy(value)
        invalid["result"]["demo_only"] = True
        assert not validator.is_valid(invalid)
    # The generic component remains the existing shared K04 boundary.
    sys.path.insert(0, str(ROOT / "scripts"))
    import export_k04_contract
    assert spec["components"]["schemas"]["JobOutput"] == export_k04_contract.document()["components"]["schemas"]["JobOutput"]


def test_exporter_itself_is_in_the_stable_source_boundary(exported_contract):
    namespace, _ = exported_contract
    inputs = namespace["sources"](ROOT)
    assert {"scripts/export_hosted_openapi.py", "src/aletheia_lab/project/mapping_identifiers.json",
            "src/aletheia_lab/product/synthetic_p6_view.json", "fixtures/k04/canonical-seed-v1.json",
            "fixtures/k04/native-seed-v1.json", 'pyproject.toml',
            'src/aletheia_lab/product/fonts/NotoSansMono-Regular.ttf',
            'src/aletheia_lab/product/fonts/OFL.txt',
            'src/aletheia_lab/product/fonts/provenance.json'} <= inputs.keys()
