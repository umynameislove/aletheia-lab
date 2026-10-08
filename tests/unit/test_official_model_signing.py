from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.evaluation import official_model_signing as signing


def test_full_closure_includes_external_weights_and_rejects_links(tmp_path: Path):
    (tmp_path / "model.onnx").write_bytes(b"graph")
    (tmp_path / "weights.bin").write_bytes(b"weights")
    before = signing.artifact_closure(tmp_path)
    assert before["total_bytes"] == 12
    assert [row["path"] for row in before["files"]] == ["model.onnx", "weights.bin"]
    (tmp_path / "weights.bin").write_bytes(b"changed")
    assert signing.artifact_closure(tmp_path) != before
    (tmp_path / "link").symlink_to(tmp_path / "weights.bin")
    with pytest.raises(ValueError, match="symbolic"):
        signing.artifact_closure(tmp_path)


def test_empty_and_symlink_roots_are_not_signed_closures(tmp_path: Path):
    with pytest.raises(ValueError, match="nonempty"):
        signing.artifact_closure(tmp_path)
    link = tmp_path / "directory-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="real artifact"):
        signing.artifact_closure(link)


def test_verification_uses_fresh_configs_and_rejects_unsigned_files(tmp_path: Path, monkeypatch):
    (tmp_path / "graph").write_bytes(b"graph")
    bundle = tmp_path.parent / "model.sig.json"
    _bundle(bundle, signing.artifact_closure(tmp_path))
    calls = []

    class Config:
        def __init__(self):
            calls.append(self)

        def use_elliptic_key_verifier(self, **kwargs):
            self.key = kwargs["public_key"]
            return self

        def set_ignore_unsigned_files(self, value):
            assert value is False
            return self

        def set_hashing_config(self, value):
            assert isinstance(value, HashConfig)
            return self

        def verify(self, root, bundle):
            assert root == tmp_path
            assert bundle.name == "model.sig.json"
            return None  # The actual API signals verification by not raising.

    monkeypatch.setattr(
        signing,
        "_package",
        lambda component: SimpleNamespace(Config=HashConfig if component == "hashing" else Config),
    )
    for _ in range(2):
        signing.verify_local(tmp_path, bundle, tmp_path.parent / "public.pem")
    assert len(calls) == 2 and calls[0] is not calls[1]


def test_verifier_failure_or_artifact_mutation_is_not_success(tmp_path: Path, monkeypatch):
    (tmp_path / "graph").write_bytes(b"before")
    bundle = tmp_path.parent / "bundle"
    _bundle(bundle, signing.artifact_closure(tmp_path))

    class Config:
        def use_elliptic_key_verifier(self, **kwargs):
            return self

        def set_ignore_unsigned_files(self, value):
            return self

        def set_hashing_config(self, value):
            return self

        def verify(self, root, bundle):
            (root / "graph").write_bytes(b"after")

    monkeypatch.setattr(
        signing,
        "_package",
        lambda component: SimpleNamespace(Config=HashConfig if component == "hashing" else Config),
    )
    with pytest.raises(ValueError, match="changed while verifying"):
        signing.verify_local(tmp_path, bundle, tmp_path.parent / "key")


def _bundle(path: Path, closure: dict) -> None:
    predicate = {
        "serialization": {"method": "files", "hash_type": "sha256", "allow_symlinks": False},
        "resources": [
            {"name": row["path"], "algorithm": "sha256", "digest": row["sha256"]}
            for row in closure["files"]
        ],
    }
    payload = base64.b64encode(json.dumps({"predicate": predicate}).encode()).decode()
    path.write_text(json.dumps({"dsseEnvelope": {"payload": payload}}))


class HashConfig:
    def set_ignored_paths(self, *, paths, ignore_git_paths):
        assert paths == [] and ignore_git_paths is False
        return self

    def use_file_serialization(self, **kwargs):
        assert kwargs == {"hashing_algorithm": "sha256", "max_workers": 1, "allow_symlinks": False}
        return self


def test_valid_partial_signature_is_not_promoted_to_full_closure(tmp_path: Path):
    root = tmp_path / "model"
    root.mkdir()
    (root / "graph").write_bytes(b"graph")
    bundle = tmp_path / "bundle"
    _bundle(bundle, signing.artifact_closure(root))
    (root / ".gitignore").write_text("ignored by default signer")
    with pytest.raises(ValueError, match="full declared closure"):
        signing._check_signed_closure(bundle, signing.artifact_closure(root))


def test_duplicate_signed_resource_is_not_complete_closure(tmp_path: Path):
    (tmp_path / "graph").write_bytes(b"graph")
    closure = signing.artifact_closure(tmp_path)
    duplicated = {**closure, "files": closure["files"] * 2}
    bundle = tmp_path.parent / "bundle"
    _bundle(bundle, duplicated)
    with pytest.raises(ValueError, match="full declared closure"):
        signing._check_signed_closure(bundle, closure)


def test_signing_does_not_overwrite_or_include_verification_material(tmp_path: Path):
    root = tmp_path / "model"
    root.mkdir()
    (root / "graph").write_bytes(b"graph")
    with pytest.raises(ValueError, match="outside"):
        signing.sign_local(root, root / "signature", tmp_path / "key")
    existing = tmp_path / "signature"
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        signing.sign_local(root, existing, tmp_path / "key")
    assert existing.read_bytes() == b"keep"
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing")
    with pytest.raises(FileExistsError):
        signing.sign_local(root, dangling, tmp_path / "key")


@pytest.mark.parametrize(
    "resident,associated,complete,expected",
    [
        ("A", True, True, "compliant"),
        ("B", True, True, "noncompliant"),
        (None, True, True, "unknown"),
        ("A", False, True, "unknown"),
        ("A", True, False, "unknown"),
        ("B", False, False, "unknown"),
    ],
)
def test_artifact_authenticity_is_not_actual_use_association(
    resident, associated, complete, expected
):
    assert signing.associated_enrollment("A", resident, associated, complete) == expected


def test_optional_dependency_version_is_checked_before_import(monkeypatch):
    monkeypatch.setattr(signing.importlib.metadata, "version", lambda name: "999")
    with pytest.raises(ValueError, match="1.1.1"):
        signing._package("verifying")
