"""Deletion checkpoint trust boundary and truthful public purge states."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted.deletion_checkpoint import DeletionCheckpointPage, DeletionFence
from aletheia_lab.hosted_contract.deletions import DeletionStatus
from aletheia_lab.product.lifecycle import build_product_lifecycle_record
from aletheia_lab.project.persistence import _json_bytes


def _fence(*, project="a" * 64, offset=0):
    record = build_product_lifecycle_record(
        record_kind="deletion",
        project_id="p3-project-" + project,
        payload={
            "schema_version": "p6-project-deletion/v1",
            "status": "pending",
            "deleted_count": 0,
            "retained_shared_count": 0,
            "purge_object_sha256s": [],
        },
    )
    requested = datetime(2026, 1, 1, tzinfo=UTC).astimezone(
        __import__("datetime").timezone(timedelta(hours=offset))
    )
    return DeletionFence(
        workspace_id="a",
        project_id=record.project_id,
        tombstone_id=record.record_id,
        revision=3,
        deleted_by="owner_a",
        requested_at=requested,
        eligible_at=requested + timedelta(days=30),
        project_days=30,
        time_basis="delete_commit",
        record_json=_json_bytes(record.model_dump(mode="json")).decode(),
    )


def test_same_deletion_checkpoint_has_one_identity_across_timezone_and_json_round_trip():
    first, offset = _fence(), _fence(offset=7)
    assert first.identity() == offset.identity()
    page = DeletionCheckpointPage(after=None, items=(first,), next_cursor=None)
    assert DeletionCheckpointPage.model_validate_json(page.model_dump_json()) == page
    assert first.requested_at.tzinfo == UTC


@pytest.mark.parametrize("change", ["private_payload", "different_scope", "noncanonical"])
def test_untrusted_checkpoint_does_not_accept_private_or_rebound_content(change):
    fence = _fence().model_dump(mode="python")
    if change == "private_payload":
        data = json.loads(fence["record_json"])
        data["payload_json"] = '{"question":"PRIVATE_SENTINEL"}'
        fence["record_json"] = json.dumps(data)
    elif change == "different_scope":
        fence["project_id"] = "p3-project-" + "b" * 64
    else:
        fence["record_json"] = " " + fence["record_json"]
    with pytest.raises(ValidationError) as error:
        DeletionFence.model_validate(fence)
    assert "PRIVATE_SENTINEL" not in str(error.value)


def test_checkpoint_order_and_resume_must_match_exact_last_scope():
    a, b = _fence(), _fence(project="b" * 64)
    for items, after, next_cursor in [
        ((b, a), None, None),
        ((a, a), None, None),
        ((a,), ("a", a.project_id), None),
        ((a,), None, ("a", b.project_id)),
    ]:
        with pytest.raises(ValidationError):
            DeletionCheckpointPage(after=after, items=items, next_cursor=next_cursor)


def test_public_purged_state_requires_actual_completion_time():
    f = _fence()
    value = dict(
        schema_version="product-api/v1-draft",
        workspace_id=f.workspace_id,
        project_id=f.project_id,
        tombstone_id=f.tombstone_id,
        revision=f.revision,
        status="retained",
        requested_at=f.requested_at,
        eligible_at=f.eligible_at,
        project_retention_days=30,
        time_basis="delete_commit",
        purged_at=None,
        records_removed=0,
        pending_objects=1,
        shared_objects_retained=0,
    )
    assert DeletionStatus.model_validate(value).status == "retained"
    value["status"] = "purged"
    with pytest.raises(ValidationError):
        DeletionStatus.model_validate(value)
