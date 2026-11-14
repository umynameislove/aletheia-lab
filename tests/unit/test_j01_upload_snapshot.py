"""Upload envelope hashes and scope use real P3 mapping validation facts."""
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted.upload_snapshot import UploadSnapshot, build_upload_snapshot
from aletheia_lab.project.mapping import validate_project_mapping


def test_upload_snapshot_is_sealed_and_does_not_invent_git_history(tmp_path):
    from test_project_mapping import _case, _configuration

    case = _case(tmp_path)
    mapping = _configuration(case)
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, mapping)
    values = dict(workspace_id="ws1", source_id="src1", source_sha256="a" * 64,
                  mapping=mapping, mapping_result=result)
    now = datetime.now(UTC)
    first = build_upload_snapshot(**values, captured_at=now)
    later = build_upload_snapshot(**values, captured_at=now + timedelta(seconds=1))
    assert first.git_provenance == "not_supplied"
    assert first.snapshot_id == later.snapshot_id and first.record_sha256 != later.record_sha256
    assert UploadSnapshot.model_validate_json(first.model_dump_json()) == first
    for change in ({"workspace_id": "other"}, {"source_sha256": "b" * 64}, {"snapshot_id": "upload-snapshot-" + "0" * 64}):
        with pytest.raises(ValidationError):
            UploadSnapshot.model_validate({**first.model_dump(mode="python"), **change})
