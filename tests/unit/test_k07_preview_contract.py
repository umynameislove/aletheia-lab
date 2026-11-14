from datetime import datetime

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted_contract.imports import (
    ConfirmImport,
    MappingFailure,
    MappingIssue,
    PreviewImport,
    PreviewRequest,
    preview_fixture,
)


def test_preview_wire_fixture_roundtrip_and_boundaries():
    fixture = preview_fixture()
    assert PreviewImport.model_validate_json(fixture.model_dump_json()) == fixture
    assert fixture.excluded_metrics[0].metric_name == "độ chính xác"
    assert fixture.excluded_metrics[0].reason == "unsupported_metric_identifier"
    for patch in ({"expires_at": datetime(2030, 1, 1)}, {"source_sha256": "invalid"},
                  {"preview_id": "other"}, {"excluded_metrics_total": 0},
                  {"mapping_candidates": {"payload": "a" * (1024 * 1024)}},
                  {"mapping_candidates": {"number": float("nan")}}):
        with pytest.raises((ValidationError, ValueError)):
            PreviewImport.model_validate({**fixture.model_dump(mode="python"), **patch})


def test_confirm_binding_is_server_owned_and_no_filesystem_input():
    assert ConfirmImport(schema_version="product-api/v1-draft", mapping={"runs": []}).mapping == {"runs": []}
    with pytest.raises(ValidationError):
        ConfirmImport(schema_version="product-api/v1-draft", mapping={"_preview_id": "attacker"})
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate({"schema_version": "product-api/v1-draft", "root": "C:/private"})


def test_preview_lifetime_is_bounded():
    from aletheia_lab.hosted.preview_leases import PreviewLeases

    for lifetime in (0, -1, True, 3601, float("inf")):
        with pytest.raises(ValueError):
            PreviewLeases(None, ttl_seconds=lifetime)


def test_mapping_failure_has_bounded_safe_locations():
    issue = MappingIssue(code="metric_missing", field="metric_sources[0].metric_name", record_index=2)
    failure = MappingFailure(schema_version="hosted-upload-error/v1", code="mapping_invalid", issues=[issue])
    assert MappingFailure.model_validate_json(failure.model_dump_json()) == failure
    for patch in ({"field": "C:/private/secret"}, {"code": "Unsafe error"}, {"record_index": -1}):
        with pytest.raises(ValidationError):
            MappingIssue.model_validate({**issue.model_dump(), **patch})
