"""Deletion progress is current-authorized and never claims backup/source erasure."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_k04_catalog_http import BASE, PROJECT, _scenario

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted_contract.deletions import DeletionStatus


class Retention:
    def __init__(self, *, revoke_on_delivery=False, wrong_scope=False):
        self.calls = 0
        self.revoke_on_delivery, self.wrong_scope = revoke_on_delivery, wrong_scope

    async def status(self, *, credential_digest, workspace_id, project_id):
        self.calls += 1
        assert len(credential_digest.get_secret_value()) == 64
        assert (workspace_id, project_id) == ('ws1', PROJECT)
        if self.revoke_on_delivery and self.calls == 2:
            raise AuthorizationDenied()
        at = datetime.now(UTC)
        return DeletionStatus(schema_version='product-api/v1-draft',
            workspace_id='wrong' if self.wrong_scope else workspace_id, project_id=project_id,
            tombstone_id='p6-deletion-' + 'a' * 64, revision=3, status='retained',
            requested_at=at, eligible_at=at + timedelta(days=30), project_retention_days=30,
            time_basis='delete_commit', purged_at=None, records_removed=0,
            pending_objects=2, shared_objects_retained=1)


def test_truthful_progress_rechecks_authority_before_headers(tmp_path):
    retention = Retention()
    messages, *_ = asyncio.run(_scenario(tmp_path, path=BASE + '/projects/' + PROJECT + '/deletion-status',
                                         retention=retention))
    assert messages[0]['status'] == 200 and retention.calls == 2
    body = json.loads(messages[1]['body'])
    assert body['status'] == 'retained' and body['purged_at'] is None
    assert body['raw_source_policy'] == 'workspace_sources_retained'
    assert body['backup_policy'] == 'operator_managed_not_erased_here'
    assert (b'cache-control', b'no-store') in messages[0]['headers']


@pytest.mark.parametrize('change,status', [('revoked', 404), ('scope', 500), ('disabled', 503)])
def test_deletion_status_denies_stale_authority_rebound_scope_and_disabled_adapter(tmp_path, change, status):
    retention = None if change == 'disabled' else Retention(revoke_on_delivery=change == 'revoked', wrong_scope=change == 'scope')
    messages, *_ = asyncio.run(_scenario(tmp_path, path=BASE + '/projects/' + PROJECT + '/deletion-status',
                                         retention=retention))
    assert messages[0]['status'] == status
    assert b'tombstone_id' not in messages[1]['body']
