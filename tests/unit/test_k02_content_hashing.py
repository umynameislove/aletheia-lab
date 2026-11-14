"""One byte-hash owner preserves IDs, binary output and bounded streaming."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.project.identity import content_hasher, content_sha256


@pytest.mark.parametrize('payload', [b'', b'abc', bytes(range(256)), 'độ_chính_xác'.encode(), b'\x00\xff' * 100000])
def test_exact_bytes_match_standard_sha256_for_binary_and_hex_outputs(payload):
    expected = hashlib.sha256(payload)
    assert content_hasher(payload).digest() == expected.digest()
    assert content_hasher(payload).hexdigest() == expected.hexdigest() == content_sha256(payload)


def test_streaming_copies_and_instances_are_independent():
    streamed = content_hasher()
    streamed.update(b'first\x00')
    copied = streamed.copy()
    streamed.update(b'second')
    copied.update(b'different')
    assert streamed.digest() == hashlib.sha256(b'first\x00second').digest()
    assert copied.hexdigest() == hashlib.sha256(b'first\x00different').hexdigest()
    assert content_hasher().hexdigest() == hashlib.sha256(b'').hexdigest()


def test_factory_preserves_buffers_and_security_option_without_changing_strict_byte_helper():
    for payload in (bytearray(b'abc'), memoryview(b'abc')):
        assert content_hasher(payload, usedforsecurity=False).digest() == hashlib.sha256(payload).digest()
        with pytest.raises(TypeError):
            content_sha256(payload)


@pytest.mark.parametrize('module', ['sessions', 'login', 'admission_migration'])
def test_auth_first_import_has_no_project_cycle(module):
    source = Path(__file__).resolve().parents[2] / 'src'
    env = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1')
    code = ('import importlib,json;importlib.import_module("aletheia_lab.hosted_auth.' + module + '");'
            'from aletheia_lab.project.identity import content_hasher;'
            'print(json.dumps({"sha256":content_hasher(b"abc").hexdigest()}))')
    completed = subprocess.run([sys.executable, '-B', '-c', code], env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['sha256'] == hashlib.sha256(b'abc').hexdigest()
