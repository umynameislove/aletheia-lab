"""Signed bounded polling; every read still checks current database authority."""
from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Literal

from aletheia_lab.hosted_contract.job_page import Job
from aletheia_lab.hosted_contract.job_updates import JobUpdate
from aletheia_lab.project.identity import content_hasher

from .cursors import CursorInvalid, ScopedCursors

_TERMINAL = frozenset(('succeeded', 'failed', 'cancelled', 'expired'))


def reconnect_delay_ms(attempt: int, random_value: float, *, retry_after_ms: int = 0) -> int:
    """Client handoff policy: pause privately cached UI when its 4s lease expires."""
    if (type(attempt) is not int or not 0 <= attempt <= 30
            or type(retry_after_ms) is not int or not 0 <= retry_after_ms <= 300000
            or type(random_value) not in (int, float) or not math.isfinite(random_value) or not 0 <= random_value < 1):
        raise ValueError('A bounded retry attempt and jitter value are required.')
    ceiling: int = min(30000, 1000 << min(attempt, 5))
    return max(retry_after_ms, ceiling // 2) + int(random_value * ceiling / 2)


class SnapshotPolling:
    def __init__(self, cursors: ScopedCursors, *, random_source: Callable[[], float] = random.random) -> None:
        self.cursors, self._random = cursors, random_source

    def response(self, *, job: Job, revision: int, binding: dict[str, object],
                 authority_revision: int, cursor: str | None) -> JobUpdate:
        if type(revision) is not int or not 1 <= revision <= 9223372036854775807 or type(authority_revision) is not int or authority_revision < 0:
            raise ValueError('A verified job and authority revision are required.')
        # retryable is a current control decision which can change when a successor
        # is inserted; it deliberately is not part of this status-only protocol.
        job = Job.model_validate(job.model_dump()).model_copy(update={'retryable': None})
        snapshot = content_hasher(job.model_dump_json().encode()).hexdigest()
        prior = None if cursor is None else self.cursors.read(purpose='job-updates', binding=binding, cursor=cursor)
        stable = 0
        disposition: Literal['snapshot', 'unchanged', 'resync'] = 'snapshot'
        if prior is not None:
            if (set(prior) != {'revision', 'snapshot', 'stable', 'authority_revision'}
                    or type(prior['revision']) is not int or not 1 <= prior['revision'] <= revision
                    or type(prior['stable']) is not int or not 0 <= prior['stable'] <= 3
                    or type(prior['authority_revision']) is not int or prior['authority_revision'] < 0
                    or not isinstance(prior['snapshot'], str) or len(prior['snapshot']) != 64
                    or any(character not in '0123456789abcdef' for character in prior['snapshot'])):
                raise CursorInvalid()
            if prior['revision'] == revision and prior['snapshot'] != snapshot:
                raise ValueError('Job status changed without its durable revision.')
            if prior['authority_revision'] != authority_revision or revision > prior['revision'] + 1:
                disposition = 'resync'
            elif prior['revision'] == revision:
                disposition = 'unchanged'
                stable = min(3, prior['stable'] + 1)
        terminal = job.status in _TERMINAL
        random_value = self._random()
        if type(random_value) not in (float, int) or not math.isfinite(random_value) or not 0 <= random_value < 1:
            raise ValueError('A valid jitter source is required.')
        after = None if terminal else 2000 + stable * 400 + int(random_value * 300)
        token = self.cursors.issue(purpose='job-updates', binding=binding,
            value={'revision': revision, 'snapshot': snapshot, 'stable': stable, 'authority_revision': authority_revision})
        return JobUpdate(schema_version='product-api/v1-draft', workspace_id=job.workspace_id,
            job_id=job.job_id, revision=revision, disposition=disposition,
            job=None if disposition == 'unchanged' else job, cursor=token,
            poll_after_ms=after)
