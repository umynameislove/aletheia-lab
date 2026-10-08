"""Pinned official local-key signing, separate from actual-use association.

The optional dependency is imported only for an explicit development run.
Local EC verification does not exercise OIDC, Fulcio or Rekor. A valid bundle
authenticates its declared file closure, not the model resident in a session.
"""

from __future__ import annotations

import base64
import importlib
import importlib.metadata
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.filesystem import write_new_file


def artifact_closure(root: Path) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("real artifact directory required")
    paths = sorted(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("symbolic links are outside the declared closure")
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in paths
        if path.is_file()
    ]
    if not files:
        raise ValueError("nonempty artifact closure required")
    return {
        "files": files,
        "total_bytes": sum(row["bytes"] for row in files),
        "closure_sha256": digest(files),
    }


def _package(component: str) -> Any:
    if importlib.metadata.version("model-signing") != "1.1.1":
        raise ValueError("this development adapter requires model-signing 1.1.1")
    return importlib.import_module(f"model_signing.{component}")


def sign_local(root: Path, bundle: Path, public_key: Path) -> dict[str, Any]:
    """Sign full closure with a disposable key; keep only the public material."""
    if bundle.exists() or public_key.exists() or bundle.is_symlink() or public_key.is_symlink():
        raise FileExistsError("fresh signature destinations required")
    if root.resolve() in bundle.resolve().parents or root.resolve() in public_key.resolve().parents:
        raise ValueError("signature material must be outside the signed artifact")
    signing = _package("signing")
    ec = importlib.import_module("cryptography.hazmat.primitives.asymmetric.ec")
    serialization = importlib.import_module("cryptography.hazmat.primitives.serialization")
    before = artifact_closure(root)
    with tempfile.TemporaryDirectory(prefix="local-model-signing-") as directory:
        key = ec.generate_private_key(ec.SECP256R1())
        private_path = Path(directory) / "private.pem"
        write_new_file(
            private_path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        os.chmod(private_path, 0o600)
        write_new_file(
            public_key,
            key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ),
        )
        signing.Config().use_elliptic_key_signer(private_key=private_path).set_hashing_config(
            _full_hashing_config()
        ).sign(root, bundle)
    if artifact_closure(root) != before:
        raise ValueError("artifact changed while signing")
    return before


def verify_local(root: Path, bundle: Path, public_key: Path) -> dict[str, Any]:
    """A fresh verifier per control avoids cross-control configuration mutation."""
    if bundle.is_symlink() or public_key.is_symlink():
        raise ValueError("verification material must not be symbolic links")
    before = artifact_closure(root)
    verifying = _package("verifying")
    verifying.Config().use_elliptic_key_verifier(public_key=public_key).set_hashing_config(
        _full_hashing_config()
    ).set_ignore_unsigned_files(False).verify(root, bundle)
    _check_signed_closure(bundle, before)
    if artifact_closure(root) != before:
        raise ValueError("artifact changed while verifying")
    return before


def _full_hashing_config() -> Any:
    return (
        _package("hashing")
        .Config()
        .set_ignored_paths(paths=[], ignore_git_paths=False)
        .use_file_serialization(hashing_algorithm="sha256", max_workers=1, allow_symlinks=False)
    )


def _check_signed_closure(bundle: Path, closure: dict[str, Any]) -> None:
    """After official crypto verification, require every declared file in the payload.

    Version 1.1.1 normally ignores Git metadata. A valid partial signature must
    not silently be promoted to our stricter full-directory evidence contract.
    """
    envelope = json.loads(bundle.read_text(encoding="utf-8"))["dsseEnvelope"]
    payload = json.loads(base64.b64decode(envelope["payload"], validate=True))
    predicate = payload["predicate"]
    serialization = predicate["serialization"]
    if (
        serialization["method"] != "files"
        or serialization["hash_type"] != "sha256"
        or serialization["allow_symlinks"] is not False
    ):
        raise ValueError("signature serialization differs from full file closure")
    observed = sorted(
        (Path(row["name"]).as_posix(), row["algorithm"], row["digest"])
        for row in predicate["resources"]
    )
    expected = sorted((row["path"], "sha256", row["sha256"]) for row in closure["files"])
    if observed != expected:
        raise ValueError("signed resources differ from the full declared closure")


def associated_enrollment(
    expected: str, resident: str | None, associated: bool, closure_complete: bool
) -> str:
    """Common resolver for integrated ordinary baseline and candidate view."""
    if not associated or not closure_complete or resident is None:
        return "unknown"
    return "compliant" if expected == resident else "noncompliant"
