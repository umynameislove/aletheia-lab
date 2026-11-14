"""Export reproducible synthetic consumer drafts from the current producer models."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ValidationError

from aletheia_lab.hosted.application import _problem
from aletheia_lab.hosted.import_errors import PreviewRequired
from aletheia_lab.hosted.imports_http import import_rejection
from aletheia_lab.hosted.preview_leases import PreviewChanged, PreviewExpired
from aletheia_lab.hosted_contract.imports import (
    ConfirmImport,
    MappingFailure,
    MappingIssue,
    PreviewRequest,
    preview_fixture,
)
from aletheia_lab.hosted_contract.session_capabilities import Problem, problem_consumer_schema
from aletheia_lab.hosted_contract.uploads import UploadManifest, UploadPart, UploadStatus


def _bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _model(outputs: dict[str, bytes], path: str, value: BaseModel, *, schema: bool = True) -> None:
    wire = value.model_dump(mode='json')
    shape = type(value).model_json_schema()
    Draft202012Validator.check_schema(shape)
    Draft202012Validator(shape, format_checker=FormatChecker()).validate(wire)
    assert type(value).model_validate_json(value.model_dump_json()) == value
    outputs[path + '.json'] = _bytes(wire)
    if schema:
        outputs[path + '.schema.json'] = _bytes(shape)


def _multipart() -> tuple[UploadManifest, list[bytes]]:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
        entry = zipfile.ZipInfo('metrics.csv', date_time=(2020, 1, 1, 0, 0, 0))
        entry.create_system = 0
        archive.writestr(entry, 'run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n')
    source = stream.getvalue()
    width = (len(source) + 2) // 3
    parts = [source[offset:offset + width] for offset in range(0, len(source), width)]
    manifest = UploadManifest(schema_version='product-api/v1-draft', source_type='zip_bundle',
        byte_size=len(source), sha256=hashlib.sha256(source).hexdigest(),
        parts=[UploadPart(part_number=index, byte_size=len(part), sha256=hashlib.sha256(part).hexdigest())
               for index, part in enumerate(parts, 1)])
    assert len(parts) == 3 and b''.join(parts) == source
    assert sum(part.byte_size for part in manifest.parts) == manifest.byte_size
    assert hashlib.sha256(b''.join(parts)).hexdigest() == manifest.sha256
    return manifest, parts


def build() -> dict[str, bytes]:
    assert not FormatChecker().conforms('2030-02-30T00:00:00Z', 'date-time'), 'Date-time validation dependency is required.'
    outputs: dict[str, bytes] = {}
    preview = {
        'preview': preview_fixture(),
        'preview-request': PreviewRequest(source_id='src_synthetic'),
        'confirm-import': ConfirmImport(mapping={'runs': [], 'baseline_run_id': 'baseline'}),
        'mapping-failure': MappingFailure(schema_version='hosted-upload-error/v1', code='mapping_invalid',
            issues=[MappingIssue(code='metric_missing', field='metric_sources[0].metric_name', record_index=2)]),
    }
    for name, preview_model in preview.items():
        _model(outputs, 'preview-v1-draft/' + name, preview_model)

    manifest, parts = _multipart()
    _model(outputs, 'uploads-v1-draft/upload-manifest', manifest)
    common: dict[str, object] = dict(workspace_id='ws_synthetic', upload_id='upl_synthetic',
        byte_size=manifest.byte_size, sha256=manifest.sha256, expires_at=datetime(2030, 1, 1, tzinfo=UTC))
    examples = {
        'active-new': dict(state='active', received_bytes=0, received_parts=[], missing_parts=[1, 2, 3]),
        'active-partial': dict(state='active', received_bytes=len(parts[1]), received_parts=[2], missing_parts=[1, 3]),
        'active-all-bytes': dict(state='active', received_bytes=manifest.byte_size,
                                 received_parts=[1, 2, 3], missing_parts=[]),
        'ready': dict(state='ready', received_bytes=manifest.byte_size, received_parts=[1, 2, 3],
                      missing_parts=[], source_id='src_upload_synthetic'),
        'cancelled': dict(state='cancelled', received_bytes=len(parts[1]), received_parts=[2], missing_parts=[1, 3]),
        'expired': dict(state='expired', received_bytes=len(parts[1]), received_parts=[2], missing_parts=[1, 3],
                        expires_at=datetime(2020, 1, 1, tzinfo=UTC)),
    }
    for name, fields in examples.items():
        status_model = UploadStatus.model_validate({**common, **fields})
        _model(outputs, 'uploads-v1-draft/upload-' + name, status_model, schema=False)
        if name == 'active-partial':
            assert status_model.received_bytes == sum(manifest.parts[number - 1].byte_size for number in status_model.received_parts)
        if name == 'active-all-bytes':
            assert status_model.received_bytes == status_model.byte_size and status_model.source_id is None and status_model.state != 'ready'
    outputs['uploads-v1-draft/upload-status.schema.json'] = _bytes(UploadStatus.model_json_schema())

    errors = [
        (410, 'upload_expired', 'The upload lease has expired.'),
        (409, 'upload_conflict', 'The upload cannot be changed in its current state.'),
        (413, 'upload_too_large', 'The source exceeds the configured upload limit.'),
        (413, 'upload_quota_exceeded', 'The workspace upload quota is exhausted.'),
        (422, 'upload_part_invalid', 'The upload part length does not match the manifest.'),
        (409, 'upload_part_conflict', 'The upload part does not match its sealed manifest.'),
        (409, 'upload_incomplete', 'The upload is missing one or more verified parts.'),
        (422, 'upload_integrity_failed', 'The upload integrity check failed.'),
        (422, 'source_invalid', 'The source bundle could not be accepted.'),
        (409, 'idempotency_conflict', 'The request key was already used.'),
        (403, 'permission_denied', 'The operation is not permitted.'),
        (404, 'not_found', 'The requested resource is unavailable.'),
    ]
    for error in (PreviewExpired(), PreviewChanged(), PreviewRequired()):
        rejection = import_rejection(error)
        errors.append((rejection.status, rejection.code, rejection.safe_message))
    for status, code, detail in errors:
        response = _problem(status, code, detail, 'req_synthetic_k07')
        problem = Problem.model_validate_json(response.body)
        assert response.status == problem.status == status and problem.code == code
        assert response.content_type == b'application/problem+json'
        _model(outputs, 'problems-v1-draft/problem-' + code.replace('_', '-'), problem, schema=False)
    outputs['problems-v1-draft/problem-producer.schema.json'] = _bytes(Problem.model_json_schema())
    outputs['problems-v1-draft/problem-consumer.schema.json'] = _bytes(problem_consumer_schema())

    # Cross-field validators remain essential beyond JSON Schema's structural checks.
    malformed = manifest.model_dump(mode='json')
    malformed['parts'] = malformed['parts'][:-1]
    try:
        UploadManifest.model_validate_json(json.dumps(malformed))
    except ValidationError:
        pass
    else:
        raise AssertionError('A missing part must not pass the manifest model.')

    outputs['README.md'] = (
        b'# K07 synthetic consumer draft\n\n'
        b'Generated from current shared producer models by scripts/export_k07_contract.py. '
        b'These examples are consumer drafts, not joint acceptance or deployment evidence.\n\n'
        b'preview-v1-draft preserves the original eight preview/mapping fixtures. '
        b'uploads-v1-draft adds the current UploadManifest and UploadStatus schemas, a deterministic '
        b'three-part synthetic ZIP manifest and new/partial/all-bytes-active/ready/cancelled/expired states. '
        b'The ZIP bytes are generated in memory only and are not exported. '
        b'Preview and upload examples are independent synthetic objects.\n\n'
        b'received_bytes counts verified bytes; active with all bytes is still awaiting complete/validation, '
        b'and only ready has a source_id. Job progress remains null when unknown in the existing job contract.\n\n'
        b'problems-v1-draft uses the actual application._problem serializer. '
        b'K07 codes are draft extensions; the generic shared Problem schema does not yet pin every '
        b'K07 code/status pair. Consumers must check actual HTTP status, fail closed for unknown codes '
        b'and coordinate the known-code registry through J01. No raw exception or private source path is included.\n\n'
        b'mapping-failure is a safe failed-job diagnostic envelope, not a standalone HTTP Problem '
        b'or a successful result. Some illustrative mapping selections remain unresolved and are not '
        b'a confirmable source fixture.\n\n'
        b'The exporter checks JSON Schema, model roundtrips, multipart hashes and progress semantics '
        b'before writing. Identical files are retained; differing existing files stop the export before '
        b'any write. manifest.json and SHA256SUMS.txt identify the exact generated bytes.\n'
    )
    manifest_data = {
        'schema_version': 'k07-consumer-fixtures/v1-draft',
        'disposition': 'synthetic_consumer_draft_not_joint_acceptance',
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(outputs.items())},
    }
    outputs['manifest.json'] = _bytes(manifest_data)
    outputs['SHA256SUMS.txt'] = ''.join(
        hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in sorted(outputs.items())
    ).encode('ascii')
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, nargs='?', default=Path(__file__).resolve().parents[1] / 'fixtures' / 'k07')
    destination = parser.parse_args().output
    outputs = build()
    for name, data in outputs.items():
        path = destination / name
        if path.exists() and path.read_bytes() != data:
            raise FileExistsError('Different existing fixture was preserved: ' + name)
    for name, data in outputs.items():
        path = destination / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as target:
                target.write(data)
        assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest()
    print('Verified ' + str(len(outputs)) + ' synthetic consumer draft files.')
    print('manifest.json SHA256=' + hashlib.sha256(outputs['manifest.json']).hexdigest())


if __name__ == '__main__':
    main()
