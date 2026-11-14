"""Deterministic synthetic deletion/polling examples from maintained producer models."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from aletheia_lab.hosted_contract.deletions import DeletionStatus
from aletheia_lab.hosted_contract.job_page import Job
from aletheia_lab.hosted_contract.job_updates import JobUpdate


def export(output: Path) -> None:
    if output.exists():
        raise ValueError('A new output directory is required.')
    at = datetime(2030, 1, 1, tzinfo=UTC)
    common = dict(schema_version='product-api/v1-draft', workspace_id='ws_synthetic')
    deletion = DeletionStatus(**common, project_id='p3-project-' + 'a' * 64,
        tombstone_id='p6-deletion-' + 'b' * 64, revision=3, status='retained',
        requested_at=at, eligible_at=at + timedelta(days=30), project_retention_days=30,
        time_basis='delete_commit', purged_at=None, records_removed=0,
        pending_objects=2, shared_objects_retained=1)
    pending = Job(**common, job_id='job_synthetic', status='running', progress=None,
        operation='analyze', created_at=at, deadline_at=at + timedelta(minutes=2), attempt=1, max_attempts=3)
    terminal = pending.model_copy(update={'status': 'succeeded', 'terminal_at': at + timedelta(seconds=2),
        'output_url': '/v1/workspaces/ws_synthetic/jobs/job_synthetic/output'})
    cursor = 'synthetic-opaque-cursor-not-a-valid-runtime-token'
    examples = {'deletion-retained': deletion,
        'deletion-purging': deletion.model_copy(update={'status': 'purging', 'records_removed': 1}),
        'deletion-purged': deletion.model_copy(update={'status': 'purged', 'purged_at': at + timedelta(days=31),
            'pending_objects': 0, 'records_removed': 7})}
    for name, disposition, job, revision, delay in [
        ('snapshot', 'snapshot', pending, 2, 2700), ('unchanged', 'unchanged', None, 2, 2700),
        ('resync', 'resync', terminal, 5, None)]:
        examples['poll-' + name] = JobUpdate(**common, job_id='job_synthetic', disposition=disposition,
            job=job, revision=revision, cursor=cursor, poll_after_ms=delay)
    output.mkdir(parents=True)
    for name, value in examples.items():
        parsed = type(value).model_validate_json(value.model_dump_json())
        schema = type(value).model_json_schema()
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(parsed.model_dump(mode='json'))
        (output / (name + '.json')).write_text(json.dumps(parsed.model_dump(mode='json'), indent=2) + '\n')
    for model in (DeletionStatus, JobUpdate):
        (output / (model.__name__ + '.schema.json')).write_text(json.dumps(model.model_json_schema(), indent=2) + '\n')
    files = sorted(output.iterdir())
    (output / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in files))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    export(parser.parse_args().output)


if __name__ == '__main__':
    main()
