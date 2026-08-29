"""Property tests for deterministic and fail-closed project import."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from aletheia_lab.project import (
    ProjectImportPolicy,
    grant_project_root,
    import_local_project,
)

_STAMP = "2026-08-25T00:00:00Z"
_SAFE_SEGMENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-",
    min_size=1,
    max_size=24,
)
_TEXT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,\n_-",
    min_size=0,
    max_size=200,
)
_EMAIL_SEGMENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
    min_size=1,
    max_size=24,
)


def _run(root: Path, policy: ProjectImportPolicy | None = None):
    return import_local_project(
        grant_project_root(root.resolve()),
        display_name="Property Project",
        ingested_at=_STAMP,
        policy=policy,
    )


@given(
    extensions=st.lists(
        st.sampled_from([".json", ".txt", ".md", ".py", ".toml"]),
        min_size=1,
        max_size=5,
        unique=True,
    )
)
def test_policy_tuple_order_does_not_change_policy_hash(extensions: list[str]) -> None:
    forward = ProjectImportPolicy(allowed_extensions=tuple(extensions))
    reverse = ProjectImportPolicy(allowed_extensions=tuple(reversed(extensions)))

    assert forward == reverse
    assert forward.canonical_sha256() == reverse.canonical_sha256()


# These properties exercise real filesystem and subprocess-adjacent import
# boundaries. Wall-clock deadlines are not semantic assertions and are unstable
# across NTFS, antivirus scanning, and cold filesystem caches on Windows.
@settings(max_examples=30, deadline=None)
@given(segment=_SAFE_SEGMENT, text=_TEXT)
def test_safe_text_import_is_repeatable(segment: str, text: str) -> None:
    with TemporaryDirectory(prefix=f"project-{segment}-") as directory:
        root = Path(directory)
        path = root / "note.txt"
        path.write_text(text, encoding="utf-8")

        first = _run(root)
        second = _run(root)

        assert first.status == second.status == "imported"
        assert first.bundle == second.bundle
        assert first.preview == second.preview
        assert first.artifacts == second.artifacts


@settings(max_examples=30, deadline=None)
@given(first=_TEXT, second=_TEXT)
def test_source_mutation_changes_content_bound_identity(
    first: str,
    second: str,
) -> None:
    assume(first != second)
    with TemporaryDirectory(prefix="project-mutation-") as directory:
        root = Path(directory)
        path = root / "note.txt"
        path.write_text(first, encoding="utf-8")
        before = _run(root)
        path.write_text(second, encoding="utf-8")
        after = _run(root)

        assert before.bundle is not None
        assert after.bundle is not None
        assert before.bundle.items[0].content_sha256 != after.bundle.items[0].content_sha256
        assert before.bundle.project_bundle_id != after.bundle.project_bundle_id


@settings(max_examples=30, deadline=None)
@given(payload=st.binary(min_size=0, max_size=128))
def test_invalid_utf8_prefix_always_blocks_atomically(payload: bytes) -> None:
    with TemporaryDirectory(prefix=hashlib_name(payload)) as directory:
        root = Path(directory)
        (root / "invalid.txt").write_bytes(b"\xff" + payload)

        result = _run(root)

        assert result.status == "blocked"
        assert result.bundle is None
        assert result.artifacts == ()
        assert any(issue.code == "invalid_utf8" for issue in result.preview.issues)


def hashlib_name(payload: bytes) -> str:
    """Return a short deterministic directory name without global random state."""

    return "case-" + hashlib.sha256(payload).hexdigest()[:16]


@settings(max_examples=30, deadline=None)
@given(local=_EMAIL_SEGMENT, domain=_EMAIL_SEGMENT)
def test_email_like_pii_never_survives_diagnosis_artifact(
    local: str,
    domain: str,
) -> None:
    raw = f"{local}@{domain}.invalid"
    with TemporaryDirectory(prefix="project-pii-") as directory:
        root = Path(directory)
        (root / "contact.txt").write_text(f"contact={raw}\n", encoding="utf-8")

        result = _run(root)

        assert result.status == "imported_with_restrictions"
        assert result.bundle is not None
        assert result.bundle.items[0].visibility == "diagnosis"
        assert raw.encode() not in result.artifacts[0].content
        assert b"[REDACTED:pii.email]" in result.artifacts[0].content


@settings(max_examples=30, deadline=None)
@given(secret=_SAFE_SEGMENT.filter(lambda value: len(value) >= 8))
def test_secret_assignment_is_always_local_only_and_withheld(
    secret: str,
) -> None:
    raw = f"password={secret}_SYNTHETIC"
    with TemporaryDirectory(prefix="project-secret-") as directory:
        root = Path(directory)
        (root / "settings.txt").write_text(raw, encoding="utf-8")

        result = _run(root)

        assert result.status == "imported_with_restrictions"
        assert result.bundle is not None
        assert result.bundle.items[0].visibility == "local_only"
        assert result.bundle.items[0].redaction_state == "withheld"
        assert raw.encode() not in result.artifacts[0].content


@settings(max_examples=30, deadline=None)
@given(extension=_SAFE_SEGMENT.map(str.lower))
def test_unsupported_extension_is_never_admitted(extension: str) -> None:
    assume(f".{extension}" not in ProjectImportPolicy().allowed_extensions)
    with TemporaryDirectory(prefix="project-unsupported-") as directory:
        root = Path(directory)
        path = root / f"payload.{extension}"
        path.write_text("untrusted", encoding="utf-8")

        result = _run(root)

        assert result.status == "blocked"
        assert result.bundle is None
        decision = next(
            item for item in result.preview.decisions if item.relative_path == path.name
        )
        assert decision.action == "exclude"
        assert decision.reason_code == "file_type_not_allowed"


@settings(max_examples=30, deadline=None)
@given(names=st.lists(_SAFE_SEGMENT, min_size=1, max_size=8, unique=True))
def test_filesystem_creation_order_does_not_change_import_result(
    names: list[str],
) -> None:
    with TemporaryDirectory(prefix="project-order-") as directory:
        root = Path(directory)
        forward_root = root / "forward"
        reverse_root = root / "reverse"
        forward_root.mkdir()
        reverse_root.mkdir()
        for name in names:
            (forward_root / f"{name}.txt").write_text(name, encoding="utf-8")
        for name in reversed(names):
            (reverse_root / f"{name}.txt").write_text(name, encoding="utf-8")

        forward = _run(forward_root)
        reverse = _run(reverse_root)

        assert forward.bundle is not None
        assert reverse.bundle is not None
        # Root-derived project IDs intentionally differ.  Relative inventory order
        # and exact source digests must nevertheless be identical.
        assert [item.relative_path for item in forward.bundle.items] == [
            item.relative_path for item in reverse.bundle.items
        ]
        assert [item.content_sha256 for item in forward.bundle.items] == [
            item.content_sha256 for item in reverse.bundle.items
        ]
