"""Real filesystem publication/integrity faults; no external service required."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.hosted.object_store import (
    FilesystemObjectStore,
    ObjectCandidate,
    ObjectDescriptor,
    ObjectIntegrityError,
)
from aletheia_lab.project.persistence import _json_bytes


def test_publish_is_idempotent_and_checks_canonical_envelope(tmp_path):
    store = FilesystemObjectStore(tmp_path)
    payload = _json_bytes({"schema_version": "synthetic/v1", "value": "Kiên"})
    first = store.publish(payload, media_type="application/json", schema_version="synthetic/v1")
    assert store.publish(payload, media_type="application/json", schema_version="synthetic/v1") == first
    assert first.sha256 == hashlib.sha256(payload).hexdigest()
    assert first.byte_size == len(payload)
    assert store.read(first) == payload
    assert not tuple(tmp_path.rglob("*.stage"))
    with pytest.raises(ObjectIntegrityError):
        store.publish(payload, media_type="application/json", schema_version="synthetic/v2")
    with pytest.raises(ObjectIntegrityError):
        store.publish(b'{"value":1,"schema_version":"synthetic/v1"}',
                      media_type="application/json", schema_version="synthetic/v1")


def test_publish_near_windows_path_limit(tmp_path):
    # The object itself fits MAX_PATH, but the longer sibling stage does not.
    root = tmp_path / ("a" * 40)
    root /= "b" * (185 - len(str(root.resolve())) - 1)
    store = FilesystemObjectStore(root)
    payload = b"synthetic deep path"
    descriptor = store.publish(payload, media_type="application/octet-stream")
    assert len(str(store.path(descriptor.sha256))) == 258
    assert store.publish(payload, media_type="application/octet-stream") == descriptor
    assert store.read(descriptor) == payload
    candidate, = store.candidates()
    assert not candidate.staged


@pytest.mark.parametrize("corruption", ["missing", "truncated", "same_length", "oversized"])
def test_one_object_corruption_fails_closed(tmp_path, corruption):
    store = FilesystemObjectStore(tmp_path)
    descriptor = store.publish(b"synthetic bytes", media_type="application/octet-stream")
    path = store.path(descriptor.sha256)
    if corruption == "missing":
        path.unlink()
    elif corruption == "truncated":
        path.write_bytes(b"synthetic")
    elif corruption == "same_length":
        path.write_bytes(b"X" * descriptor.byte_size)
    else:
        path.write_bytes(b"X" * (descriptor.byte_size + 100))
    with pytest.raises(ObjectIntegrityError):
        store.read(descriptor)


def test_failure_before_publish_cleans_partial_stage_and_retry_succeeds(tmp_path, monkeypatch):
    from aletheia_lab import filesystem

    store = FilesystemObjectStore(tmp_path)
    payload = b"synthetic publication fault"
    original = filesystem._link_staged_file

    def fail(*args):
        raise OSError("injected local I/O failure")

    monkeypatch.setattr(filesystem, "_link_staged_file", fail)
    with pytest.raises(ObjectIntegrityError):
        store.publish(payload, media_type="application/octet-stream")
    assert not store.candidates()
    monkeypatch.setattr(filesystem, "_link_staged_file", original)
    assert store.read(store.publish(payload, media_type="application/octet-stream")) == payload


def test_crash_after_stage_fsync_leaves_only_reconcilable_stage(tmp_path):
    FilesystemObjectStore(tmp_path)
    code = "\n".join([
        "import os,sys",
        "from aletheia_lab import filesystem",
        "from aletheia_lab.hosted.object_store import FilesystemObjectStore",
        "filesystem._link_staged_file=lambda *args: os._exit(93)",
        "FilesystemObjectStore(sys.argv[1]).publish(b'synthetic crash',media_type='application/octet-stream')",
    ])
    result = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path)],
                            capture_output=True, timeout=20, check=False)
    assert result.returncode == 93, result.stderr.decode(errors="replace")
    candidates = FilesystemObjectStore(tmp_path).candidates()
    assert len(candidates) == 1 and candidates[0].staged
    assert not FilesystemObjectStore(tmp_path).path(candidates[0].sha256).exists()


def test_scoped_read_touches_one_object_despite_unrelated_corruption(tmp_path, monkeypatch):
    store = FilesystemObjectStore(tmp_path)
    requested = store.publish(b"requested payload", media_type="application/octet-stream")
    for index in range(100):
        descriptor = store.publish(f"unrelated {index}".encode(), media_type="application/octet-stream")
    store.path(descriptor.sha256).write_bytes(b"corrupt unrelated")
    opened = []
    original = Path.open

    def track(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track)
    assert store.read(requested) == b"requested payload"
    assert opened == [store.path(requested.sha256)]


def test_managed_root_and_cleanup_never_adopt_user_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "user.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(ObjectIntegrityError):
        FilesystemObjectStore(source)
    store = FilesystemObjectStore(tmp_path / "managed")
    descriptor = store.publish(b"synthetic", media_type="application/octet-stream")
    candidate = store.candidates()[0]
    with pytest.raises(ObjectIntegrityError):
        store.remove_candidate(ObjectCandidate(descriptor.sha256, "../source/user.txt", 0, False))
    assert (source / "user.txt").read_text(encoding="utf-8") == "preserve"
    os.utime(store.path(descriptor.sha256), (candidate.modified_at + 2, candidate.modified_at + 2))
    assert not store.remove_candidate(candidate)


def test_limits_and_invalid_hashes_rejected(tmp_path):
    store = FilesystemObjectStore(tmp_path, maximum_object_bytes=4)
    with pytest.raises(ValueError):
        store.publish(b"12345", media_type="application/octet-stream")
    with pytest.raises(ValueError):
        store.path("../../unsafe")
    with pytest.raises(ValueError):
        ObjectDescriptor("A" * 64, 1, "application/octet-stream")
    with pytest.raises(ValueError):
        store.candidates(limit=0)


def test_same_hash_existing_bytes_are_never_overwritten(tmp_path):
    store = FilesystemObjectStore(tmp_path)
    original = store.publish(b"original", media_type="application/octet-stream")
    store.path(original.sha256).write_bytes(b"corrupt!")
    with pytest.raises(ObjectIntegrityError):
        store.publish(b"original", media_type="application/octet-stream")
    assert store.path(original.sha256).read_bytes() == b"corrupt!"


def test_symlink_bucket_is_rejected_when_platform_allows_it(tmp_path):
    store = FilesystemObjectStore(tmp_path / "managed")
    target = tmp_path / "target"
    target.mkdir()
    descriptor = store.describe(b"synthetic", media_type="application/octet-stream")
    bucket = store.path(descriptor.sha256).parent
    try:
        bucket.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    with pytest.raises(ObjectIntegrityError):
        store.publish(b"synthetic", media_type="application/octet-stream")
    assert not list(target.iterdir())



def test_real_partial_stage_write_never_publishes_and_retry_recovers(tmp_path, monkeypatch):
    from aletheia_lab import filesystem

    store = FilesystemObjectStore(tmp_path)
    original = filesystem.os.fdopen
    written = []

    class PartialWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, payload):
            self.handle.write(payload[:3])
            self.handle.flush()
            os.fsync(self.handle.fileno())
            written.append(3)
            raise OSError("injected failure after three durable stage bytes")

    monkeypatch.setattr(filesystem.os, "fdopen", lambda *args, **kwargs: PartialWriter(original(*args, **kwargs)))
    with pytest.raises(ObjectIntegrityError):
        store.publish(b"synthetic partial publication", media_type="application/octet-stream")
    assert written == [3] and not store.candidates()
    monkeypatch.setattr(filesystem.os, "fdopen", original)
    descriptor = store.publish(b"synthetic partial publication", media_type="application/octet-stream")
    assert store.read(descriptor) == b"synthetic partial publication"



def test_new_root_and_bucket_entries_are_directory_synced(tmp_path, monkeypatch):
    from aletheia_lab.hosted import object_store

    synced = []
    monkeypatch.setattr(object_store, "_fsync_directory", lambda path: synced.append(path.resolve()))
    root = tmp_path / "new-parent" / "managed"
    store = FilesystemObjectStore(root)
    assert root.resolve() in synced
    assert root.parent.resolve() in synced and tmp_path.resolve() in synced
    synced.clear()
    descriptor = store.publish(b"synthetic durable namespace", media_type="application/octet-stream")
    bucket = store.path(descriptor.sha256).parent
    assert synced == [store.object_root, bucket]
