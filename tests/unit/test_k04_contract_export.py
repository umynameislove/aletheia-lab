"""The owned K-04 fragment matches current routes, producer schemas and bytes."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema import ValidationError as SchemaValidationError

from aletheia_lab.hosted import catalog_http, job_http, workspace_http
from aletheia_lab.hosted_contract.job_page import Job, JobOutput
from aletheia_lab.hosted_contract.workspaces import ExportDescriptor, GraphView
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.results import _view_from_record

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/export_k04_contract.py"
spec = importlib.util.spec_from_file_location("export_k04_contract", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = exporter
spec.loader.exec_module(exporter)


@pytest.fixture
def exported(tmp_path):
    exporter.export(tmp_path)
    document = json.loads((tmp_path / "openapi-workspaces.json").read_text(encoding="utf-8"))
    inventory = json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8"))
    return tmp_path, document, inventory


def test_exported_payloads_and_all_refs_follow_maintained_producer_schemas(exported):
    path, document, inventory = exported
    checker = FormatChecker()
    assert "date-time" in checker.checkers
    assert not checker.conforms("2026-11-11T10:00:00", "date-time")
    for schema in document["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)
    for file, model in inventory["payload_models"].items():
        schema = {"$ref": "#/components/schemas/" + model, "components": document["components"]}
        Draft202012Validator(schema, format_checker=checker).validate(json.loads((path / file).read_text(encoding="utf-8")))

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                prefix = "#/components/schemas/"
                assert value["$ref"].startswith(prefix) and value["$ref"][len(prefix):] in document["components"]["schemas"]
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(document)
    for file, field, model in (("export-json-durable-synthetic.json", "expires_at", "ExportDescriptor"),
                               ("job-cancelled.json", "created_at", "Job")):
        value = json.loads((path / file).read_text(encoding="utf-8"))
        schema = {"$ref": "#/components/schemas/" + model, "components": document["components"]}
        validator = Draft202012Validator(schema, format_checker=checker)
        validator.validate({**value, field: "2026-11-11T10:00:00Z"})
        with pytest.raises(SchemaValidationError):
            validator.validate({**value, field: "2026-11-11T10:00:00"})


def test_every_exported_operation_matches_actual_handler_methods(exported):
    _, document, inventory = exported
    ids = {"workspace_id": "ws_synthetic", "project_id": inventory["canonical_identities"]["project_id"],
        "result_id": inventory["canonical_identities"]["result_id"], "snapshot_id": inventory["canonical_identities"]["snapshot_id"],
        "record_id": inventory["canonical_identities"]["result_id"], "evidence_id": "evidence_synthetic", "annotation_id": "annotation_synthetic",
        "user_id": "user_synthetic", "source_id": "src_synthetic", "invitation_id": "inv_synthetic", "job_id": "job_synthetic",
        "export_id": "ex_json_" + inventory["canonical_identities"]["result_id"]}
    for template, methods in document["paths"].items():
        concrete = template.format(**ids)
        if job_http._ROUTE.fullmatch(concrete):
            allow = job_http.JobApplication._allow(concrete)
        elif any(pattern.fullmatch(concrete) for pattern in
                 (catalog_http._PROJECT, catalog_http._RESULT, catalog_http._EVENTS, catalog_http._DOWNLOAD)):
            allow = catalog_http.CatalogApplication._allow(concrete)
        else:
            assert concrete in ("/v1/workspaces", "/v1/invitations/accept") or any(pattern.fullmatch(concrete) for pattern in
                (workspace_http._MEMBERS, workspace_http._TRANSFER, workspace_http._INVITES,
                 workspace_http._GRANT, workspace_http._WORKSPACE, workspace_http._TARGETS))
            allow = workspace_http.WorkspaceApplication._allow(concrete)
        assert set(methods) == {method.lower() for method in allow.decode("ascii").split(", ")}
        for method, operation in methods.items():
            csrf = "CsrfHeader" in operation["security"][0]
            assert csrf == (method not in ("get", "head")) and "503" in operation["responses"]
            if method == "head":
                assert all("content" not in response for response in operation["responses"].values())


def test_export_201_download_binary_range_and_retained_delete_are_truthful(exported):
    path, document, inventory = exported
    export = document["paths"][exporter.RESULT + "/exports"]["post"]
    assert "201" in export["responses"] and "202" not in export["responses"]
    assert export["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/ExportDescriptor")
    download = document["paths"][exporter.PREFIX + "/exports/{export_id}/download"]
    for method in ("get", "head"):
        assert {"200", "206", "416"}.issubset(download[method]["responses"])
        assert "Content-Range" in download[method]["responses"]["206"]["headers"]
    assert download["get"]["responses"]["206"]["content"]["application/json"]["schema"]["format"] == "binary"
    for report_format in ("json", "markdown", 'pdf'):
        descriptor = json.loads((path / ("export-" + report_format + ".json")).read_text(encoding="utf-8"))
        binary = path / ("download-" + report_format + ".bin")
        assert binary.stat().st_size == descriptor["byte_size"]
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == descriptor["sha256"]
        assert inventory["binary_payloads"][binary.name]["sha256"] == descriptor["sha256"]
        assert descriptor["export_id"] == f"ex_{report_format}_{descriptor['result_id']}"
        assert descriptor["expires_at"] is None
        durable = json.loads((path / f"export-{report_format}-durable-synthetic.json").read_text(encoding="utf-8"))
        assert durable["export_id"] == descriptor["export_id"] + "_" + "0123456789abcdef" * 2
        assert durable["expires_at"] == "2026-11-11T12:00:00Z"
        assert durable["download_url"] == f"/v1/workspaces/{durable['workspace_id']}/exports/{durable['export_id']}/download"
        for key in ("project_id", "result_id", "format", "byte_size", "sha256"):
            assert durable[key] == descriptor[key]
    deleted = json.loads((path / "deleted-project.json").read_text(encoding="utf-8"))
    assert deleted["status"] == "tombstoned" and deleted["retention"] == "retained"
    assert "ETag" in document["paths"][exporter.PROJECT]["delete"]["responses"]["200"]["headers"]
    assert inventory["formats"] == ["json", "markdown", 'pdf'] and inventory["unsupported_formats"] == []
    assert (path / 'download-pdf.bin').read_bytes().startswith(b'%PDF-')
    assert 'application/pdf' in download['get']['responses']['200']['content']
    assert inventory["events_transport"] == "paged-json" and not inventory["sse"] and not inventory["physical_purge"]


def test_canonical_ids_and_invitation_error_and_page_boundaries_remain_explicit(exported):
    path, _, inventory = exported
    seed = json.loads(exporter.SEED.read_text(encoding="utf-8"))
    record = ProductLifecycleRecord.model_validate(seed["result_record"])
    result = json.loads((path / "result-view.json").read_text(encoding="utf-8"))
    assert result["view"] == seed["view"] == _view_from_record(record).model_dump(mode="json")
    assert result["result_id"] == record.record_id == inventory["canonical_identities"]["result_id"]
    for file in ("members", "invitations", "invitation-targets", "projects", "records", "results-workspace", "events-workspace", "annotation-history"):
        value = json.loads((path / (file + ".json")).read_text(encoding="utf-8"))
        assert value["next_cursor"] is None and value["total"] is None and value["total_kind"] == "unknown"
    for name, field in [('claims', 'claims'), ('evidence-page', 'evidence')]:
        value = json.loads((path / (name + '.json')).read_text())
        assert value['total_kind'] == 'exact' and value['total'] == len(seed['view'][field]) and value['next_cursor'] is None
    header = json.loads((path / 'result-header.json').read_text())
    assert header['result'] == seed['view']['result'] and header['project'] == seed['view']['project']
    assert 'snapshot' not in header and 'claims' not in header and 'graph' not in header
    assert json.loads((path / "results-workspace.json").read_text(encoding="utf-8"))["project_id"] is None
    pending = json.loads((path / "invitations.json").read_text(encoding="utf-8"))["items"][0]
    assert not {"token", "token_digest"} & set(pending)
    for code, status in (("invitation_expired", 410), ("invitation_unavailable", 404), ("invitation_wrong_account", 403)):
        problem = json.loads((path / ("problem-" + code.replace("_", "-") + ".json")).read_text(encoding="utf-8"))
        assert problem["code"] == code and problem["status"] == status


def test_repeated_exports_are_deterministic_and_existing_evidence_is_not_overwritten(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    exporter.export(first)
    exporter.export(second)
    original = {file.name: file.read_bytes() for file in first.iterdir()}
    assert original == {file.name: file.read_bytes() for file in second.iterdir()}
    with pytest.raises(ValueError, match="new or empty"):
        exporter.export(first)
    assert original == {file.name: file.read_bytes() for file in first.iterdir()}


def test_checked_in_package_is_exact_current_generator_output(exported):
    path, _, _ = exported
    checked_in = ROOT / "fixtures/k04/producer-20261112-k08-k12"
    assert {file.name: file.read_bytes() for file in path.iterdir()} == {
        file.name: file.read_bytes() for file in checked_in.iterdir()
    }


def test_previous_shared_handoff_stays_a_legacy_fixture_and_validates_current_additive_schema(exported):
    _, current, _ = exported
    legacy = ROOT / 'fixtures/k04/producer-20261111-shared-contract'
    inventory = json.loads((legacy / 'inventory.json').read_text())
    assert inventory['formats'] == ['json', 'markdown'] and inventory['unsupported_formats'] == ['pdf']
    for file, model in inventory['payload_models'].items():
        schema = {'$ref': '#/components/schemas/' + model, 'components': current['components']}
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(json.loads((legacy / file).read_text()))


def test_historical_native_package_still_validates_and_preserves_legacy_fields(exported):
    _, current, _ = exported
    legacy = ROOT / "fixtures/k04/producer-20261110-native"
    historical = json.loads((legacy / "openapi-workspaces.json").read_text(encoding="utf-8"))
    inventory = json.loads((legacy / "inventory.json").read_text(encoding="utf-8"))
    checker = FormatChecker()
    assert "date-time" in checker.checkers
    for file, model in inventory["payload_models"].items():
        payload = json.loads((legacy / file).read_text(encoding="utf-8"))
        for document in (historical, current):
            schema = {"$ref": "#/components/schemas/" + model, "components": document["components"]}
            Draft202012Validator(schema, format_checker=checker).validate(payload)
    for name, model, unknown in (
        ("job-cancelled", Job, ("operation", "created_at", "deadline_at", "terminal_at", "attempt",
                               "max_attempts", "retryable", "output_url")),
        ("graph", GraphView, ("next_cursor",)),
        ("export-json", ExportDescriptor, ("expires_at",)),
        ("export-markdown", ExportDescriptor, ("expires_at",)),
    ):
        payload = json.loads((legacy / f"{name}.json").read_text(encoding="utf-8"))
        value = model.model_validate_json(json.dumps(payload)).model_dump(mode="json")
        assert {key: value[key] for key in payload} == payload
        for key in unknown:
            assert key not in payload and value[key] is None
        if model is Job:
            assert value["progress"] is None
        if model is GraphView:
            assert value["truncated"] is False


def test_common_job_output_component_is_the_maintained_five_operation_envelope(exported):
    path, document, inventory = exported
    model = JobOutput[dict[str, object]]
    schema = document["components"]["schemas"]["JobOutput"]
    assert schema == model.model_json_schema(ref_template="#/components/schemas/{model}")
    assert schema["properties"]["operation"]["enum"] == ["preview", "confirm", "analyze", "follow_up", "refresh"]
    assert inventory["producer_sources"]["job_page.py"] == hashlib.sha256(
        (ROOT / "src/aletheia_lab/hosted_contract/job_page.py").read_bytes()).hexdigest()
    payload = json.loads((path / "job-output.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for operation in schema["properties"]["operation"]["enum"]:
        value = {**payload, "operation": operation}
        validator.validate(value)
        assert model.model_validate_json(json.dumps(value)).model_dump(mode="json") == value
    for change in ({"operation": "unknown"}, {"result": []}, {"unexpected": True}):
        with pytest.raises(SchemaValidationError):
            validator.validate({**payload, **change})
    assert document["x-extension-revision"] == "k04-k08-k12-contract-20261112"
    assert "runtime qualification is recorded separately" in document["info"]["description"]
    assert "no joint acceptance is implied" in document["info"]["description"]


def test_result_route_documents_both_frozen_v1_and_explicit_v2_metadata(exported):
    path, document, inventory = exported
    view = json.loads((ROOT / "fixtures/metric-dimensions-v2/product-view.json").read_text(encoding="utf-8"))
    payload = {"schema_version": "product-api/v1-draft", "workspace_id": "ws_dimensions",
               "project_id": view["project"]["id"], "result_id": view["result"]["id"], "view": view}
    response = document["paths"][exporter.RESULT]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert {entry["$ref"] for entry in response["oneOf"]} == {
        "#/components/schemas/ResultView", "#/components/schemas/ResultViewV2"}
    validator = Draft202012Validator({"components": document["components"], **response})
    validator.validate(payload)
    legacy_name = next(name for name, model in inventory["payload_models"].items() if model == "ResultView")
    validator.validate(json.loads((path / legacy_name).read_text(encoding="utf-8")))
    del payload["view"]["snapshot"]["metric_changes"][0]["before"]["unit_origin"]
    with pytest.raises(SchemaValidationError):
        validator.validate(payload)
