"""Large native payload references stay private, bounded, hashed and disposable."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from aletheia_lab.hosted.payload_upload_worker import PayloadUploadWorkerSupervisor
from aletheia_lab.hosted.worker_limits import MAX_INPUT_BYTES, MAX_UPLOAD_OUTPUT_BYTES
from aletheia_lab.hosted.worker_payloads import (
    PayloadReference,
    cleanup_exchange,
    create_exchange,
    read_input,
    read_output,
    write_output,
)
from aletheia_lab.hosted.worker_supervisor import WorkerRejected


def test_private_exchange_round_trip_and_exact_output_identity(tmp_path):
    identity = 'a' * 32
    reference = PayloadReference.model_validate_json(create_exchange(tmp_path, identity, b'{"a":1}'))
    assert read_input(tmp_path, reference) == b'{"a":1}'
    result = write_output(tmp_path, reference, b'{"b":2}')
    assert read_output(tmp_path, identity, result) == b'{"b":2}'
    with pytest.raises(ValueError):
        read_output(tmp_path, 'b' * 32, result)
    cleanup_exchange(tmp_path, identity)
    assert not (tmp_path / 'payload-exchanges' / identity).exists()


@pytest.mark.parametrize('change', ['bytes', 'length', 'link'])
def test_changed_or_linked_input_is_rejected(tmp_path, change):
    identity = 'a' * 32
    reference = PayloadReference.model_validate_json(create_exchange(tmp_path, identity, b'{"a":1}'))
    path = tmp_path / 'payload-exchanges' / identity / 'input.json'
    if change == 'link':
        os.link(path, path.with_suffix('.other'))
    else:
        path.write_bytes(b'{"a":2}' if change == 'bytes' else b'{}')
    with pytest.raises(ValueError):
        read_input(tmp_path, reference)


@pytest.mark.parametrize('identity', ['../outside', '', 'a' * 33])
def test_exchange_never_uses_caller_path_segments(tmp_path, identity):
    with pytest.raises(ValueError):
        create_exchange(tmp_path, identity, b'{}')
    assert not (tmp_path.parent / 'outside').exists()


def test_reference_shape_and_hash_are_checked_before_output_read(tmp_path):
    identity = 'a' * 32
    reference = PayloadReference.model_validate_json(create_exchange(tmp_path, identity, b'{}'))
    raw = json.loads(write_output(tmp_path, reference, b'{"ok":true}'))
    raw['sha256'] = '0' * 64
    with pytest.raises(ValueError):
        read_output(tmp_path, identity, json.dumps(raw).encode())
    cleanup_exchange(tmp_path, identity)


def test_cancel_large_private_payload_reaps_real_child_and_cleans_files(tmp_path, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        children = []
        spawn = asyncio.create_subprocess_exec
        async def tracked(*args, **kwargs):
            child = await spawn(*args, **kwargs)
            children.append(child)
            started.set()
            return child
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', tracked)
        supervisor = PayloadUploadWorkerSupervisor(python=Path(sys.executable).absolute(),
            source_root=Path(__file__).resolve().parents[2] / 'src', private_root=tmp_path, slots=1, timeout_seconds=30)
        caller = asyncio.create_task(supervisor.run_upload(b' ' * (MAX_INPUT_BYTES + 1)))
        try:
            await asyncio.wait_for(started.wait(), 30)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert children[0].returncode is not None
            assert not list((tmp_path / 'payload-exchanges').iterdir())
            with pytest.raises(WorkerRejected, match='worker_input_too_large'):
                await supervisor.run_upload(b' ' * (MAX_UPLOAD_OUTPUT_BYTES + 1))
            assert len(children) == 1
        finally:
            await supervisor.aclose()
    asyncio.run(scenario())
