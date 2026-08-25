"""Property tests for P3 project identity and bundle reconciliation."""

from __future__ import annotations

import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from aletheia_lab.project import (
    ProjectCollector,
    ProjectIdentityError,
    build_project_bundle,
    build_project_item,
    canonical_project_sha256,
    granted_root_fingerprint,
    normalize_relative_project_path,
    project_id_for_root,
)

_STAMP = "2026-08-25T00:00:00Z"
_SAFE_SEGMENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.",
    min_size=1,
    max_size=24,
).filter(lambda value: value not in {".", ".."} and not value.startswith("."))
_SOURCE_BYTES = st.binary(min_size=0, max_size=256)


def _project_id() -> tuple[str, str]:
    fingerprint = granted_root_fingerprint("/property-tests/project")
    return fingerprint, project_id_for_root(fingerprint)


def _item(path: str, content: bytes):
    _, project_id = _project_id()
    return build_project_item(
        project_id=project_id,
        relative_path=path,
        source_type="artifact",
        media_type="application/octet-stream",
        source_schema=None,
        source_bytes=content,
        source_modified_at=_STAMP,
        ingested_at=_STAMP,
        collector=ProjectCollector(name="binary-metadata", version="1.0.0"),
        visibility="local_only",
    )


@given(segment=_SAFE_SEGMENT, content=_SOURCE_BYTES)
def test_repeated_item_construction_is_identity_stable(segment: str, content: bytes) -> None:
    path = f"artifacts/{segment}"
    first = _item(path, content)
    second = _item(path, content)

    assert first == second
    assert first.project_item_id == second.project_item_id
    assert first.canonical_sha256() == second.canonical_sha256()
    assert re.fullmatch(r"p3-item-[0-9a-f]{64}", first.project_item_id)


@given(segment=_SAFE_SEGMENT, first=_SOURCE_BYTES, second=_SOURCE_BYTES)
def test_content_mutation_changes_item_and_bundle_identity(
    segment: str, first: bytes, second: bytes
) -> None:
    assume(first != second)
    fingerprint, project_id = _project_id()
    first_item = _item(f"artifacts/{segment}", first)
    second_item = _item(f"artifacts/{segment}", second)

    first_bundle = build_project_bundle(
        project_id=project_id,
        display_name="Property Project",
        granted_root_fingerprint=fingerprint,
        created_at=_STAMP,
        updated_at=_STAMP,
        items=(first_item,),
    )
    second_bundle = build_project_bundle(
        project_id=project_id,
        display_name="Property Project",
        granted_root_fingerprint=fingerprint,
        created_at=_STAMP,
        updated_at=_STAMP,
        items=(second_item,),
    )

    assert first_item.project_item_id != second_item.project_item_id
    assert first_bundle.project_manifest.manifest_sha256 != second_bundle.project_manifest.manifest_sha256
    assert first_bundle.project_bundle_id != second_bundle.project_bundle_id


@given(
    first_segment=_SAFE_SEGMENT,
    second_segment=_SAFE_SEGMENT,
    first_content=_SOURCE_BYTES,
    second_content=_SOURCE_BYTES,
)
def test_item_permutation_does_not_change_manifest_or_bundle(
    first_segment: str,
    second_segment: str,
    first_content: bytes,
    second_content: bytes,
) -> None:
    assume(first_segment != second_segment)
    fingerprint, project_id = _project_id()
    first = _item(f"items/{first_segment}", first_content)
    second = _item(f"items/{second_segment}", second_content)

    forward = build_project_bundle(
        project_id=project_id,
        display_name="Property Project",
        granted_root_fingerprint=fingerprint,
        created_at=_STAMP,
        updated_at=_STAMP,
        items=(first, second),
    )
    reverse = build_project_bundle(
        project_id=project_id,
        display_name="Property Project",
        granted_root_fingerprint=fingerprint,
        created_at=_STAMP,
        updated_at=_STAMP,
        items=(second, first),
    )

    assert forward == reverse
    assert forward.project_bundle_id == reverse.project_bundle_id
    assert forward.canonical_sha256() == reverse.canonical_sha256()


@given(segment=_SAFE_SEGMENT)
def test_traversal_metamorphosis_is_always_rejected(segment: str) -> None:
    safe = f"data/{segment}"
    assert normalize_relative_project_path(safe) == safe

    unsafe = (
        f"../{safe}",
        f"data/../{segment}",
        f"/{safe}",
        safe.replace("/", "\\"),
        f"C:/{safe}",
        f"//server/{safe}",
    )
    for candidate in unsafe:
        with pytest.raises(ProjectIdentityError):
            normalize_relative_project_path(candidate)


@given(
    keys=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8),
        min_size=1,
        max_size=12,
        unique=True,
    ),
    values=st.lists(st.integers(min_value=-10_000, max_value=10_000), min_size=1, max_size=12),
)
def test_mapping_insertion_order_does_not_change_canonical_hash(
    keys: list[str], values: list[int]
) -> None:
    assume(len(values) >= len(keys))
    pairs = list(zip(keys, values, strict=False))
    forward = dict(pairs)
    reverse = dict(reversed(pairs))

    assert canonical_project_sha256(forward) == canonical_project_sha256(reverse)


@given(policy_a=st.binary(min_size=1, max_size=64), policy_b=st.binary(min_size=1, max_size=64))
def test_policy_binding_changes_bundle_identity(policy_a: bytes, policy_b: bytes) -> None:
    assume(policy_a != policy_b)
    fingerprint, project_id = _project_id()
    item = _item("config/policy.json", b"{}")

    first = build_project_bundle(
        project_id=project_id,
        display_name="Property Project",
        granted_root_fingerprint=fingerprint,
        created_at=_STAMP,
        updated_at=_STAMP,
        items=(item,),
        permission_policy_sha256=canonical_project_sha256({"policy": policy_a.hex()}),
    )
    second = build_project_bundle(
        project_id=project_id,
        display_name="Property Project",
        granted_root_fingerprint=fingerprint,
        created_at=_STAMP,
        updated_at=_STAMP,
        items=(item,),
        permission_policy_sha256=canonical_project_sha256({"policy": policy_b.hex()}),
    )

    assert first.project_bundle_id != second.project_bundle_id
