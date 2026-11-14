"""Small checks for the single upload-manifest/progress wire contract."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted_contract.uploads import MAX_PART_BYTES, UploadManifest, UploadStatus


def _data():
    return {"schema_version": "product-api/v1-draft", "source_type": "zip_bundle", "byte_size": 5,
            "sha256": "a" * 64, "parts": [{"part_number": 1, "byte_size": 2, "sha256": "b" * 64},
                                          {"part_number": 2, "byte_size": 3, "sha256": "c" * 64}]}


def test_manifest_round_trip_keeps_seal_and_strict_types():
    value = UploadManifest.model_validate(_data())
    assert UploadManifest.model_validate_json(value.model_dump_json()) == value
    assert value.parts[1].byte_size == 3


@pytest.mark.parametrize("change", ["duplicate", "gap", "length", "oversize", "path", "bool", "format"])
def test_manifest_rejects_ambiguous_or_unbounded_inputs(change):
    value = _data()
    if change == "duplicate":
        value["parts"][1]["part_number"] = 1
    elif change == "gap":
        value["parts"][1]["part_number"] = 3
    elif change == "length":
        value["byte_size"] = 6
    elif change == "oversize":
        value["parts"][0]["byte_size"] = MAX_PART_BYTES + 1
    elif change == "path":
        value["server_path"] = "C:/private"
    elif change == "bool":
        value["parts"][0]["part_number"] = True
    elif change == "format":
        value["source_type"] = "pickle"
    with pytest.raises(ValidationError):
        UploadManifest.model_validate(value)


def test_ready_requires_full_verified_bytes_and_source_reference():
    value = dict(workspace_id="a", upload_id="upl1", state="active", byte_size=5, sha256="a" * 64,
                 expires_at=datetime.now(UTC), received_bytes=2, received_parts=[1], missing_parts=[2])
    assert UploadStatus.model_validate(value).received_bytes == 2
    with pytest.raises(ValidationError):
        UploadStatus.model_validate({**value, "state": "ready", "source_id": "src1"})


@pytest.mark.parametrize("part_number", [-1, 0, 513])
def test_progress_rejects_part_numbers_outside_manifest_bounds(part_number):
    with pytest.raises(ValidationError):
        UploadStatus(workspace_id="a", upload_id="upl1", state="active", byte_size=5, sha256="a" * 64,
            expires_at=datetime.now(UTC), received_bytes=0, received_parts=[], missing_parts=[part_number])


@pytest.mark.parametrize("missing_parts", [[], [1, 3]])
def test_progress_rejects_empty_or_noncontiguous_part_universe(missing_parts):
    with pytest.raises(ValidationError):
        UploadStatus(workspace_id="a", upload_id="upl1", state="active", byte_size=5, sha256="a" * 64,
            expires_at=datetime.now(UTC), received_bytes=0, received_parts=[], missing_parts=missing_parts)
