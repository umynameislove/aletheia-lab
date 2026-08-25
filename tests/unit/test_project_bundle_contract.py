from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.project import (
    PROJECT_BUNDLE_SCHEMA_VERSION,
    PROJECT_ITEM_SCHEMA_VERSION,
    PROJECT_MANIFEST_SCHEMA_VERSION,
    ImmutableArtifactReference,
    ProjectBundle,
    ProjectCollector,
    ProjectIdentityError,
    ProjectItem,
    ProjectParseWarning,
    build_project_bundle,
    build_project_item,
    build_project_manifest,
    canonical_project_sha256,
    content_sha256,
    granted_root_fingerprint,
    normalize_relative_project_path,
    project_id_for_root,
    verify_project_item_artifact,
    verify_project_item_source,
)
from aletheia_lab.project.contracts import ProjectContractError

_CREATED = "2026-08-25T01:02:03Z"
_UPDATED = "2026-08-25T01:03:04.500000Z"
_POLICY_SHA = "a" * 64
_ROOT_PATH = "/private/research/customer-project"
_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "project_bundle_v1.json"
_FIXTURE_CANONICAL_SHA256 = "c14f01868af4be7848e69cea8b83342fc4a7d7431e7e71435b79cc139087b617"


def _identity() -> tuple[str, str]:
    fingerprint = granted_root_fingerprint(_ROOT_PATH)
    return fingerprint, project_id_for_root(fingerprint)


def _collector(name: str = "json-config", version: str = "1.2.0") -> ProjectCollector:
    return ProjectCollector(name=name, version=version)


def _item(
    *,
    relative_path: str = "configs/model.json",
    source_bytes: bytes = b'{"model":"logistic"}\n',
    artifact_bytes: bytes | None = None,
    collector: ProjectCollector | None = None,
    ingested_at: str = _CREATED,
) -> ProjectItem:
    _, project_id = _identity()
    return build_project_item(
        project_id=project_id,
        relative_path=relative_path,
        source_type="config",
        media_type="application/json",
        source_schema="model-config/v1",
        source_bytes=source_bytes,
        source_modified_at="2026-08-24T23:59:59Z",
        ingested_at=ingested_at,
        collector=collector or _collector(),
        visibility="diagnosis",
        artifact_bytes=artifact_bytes,
    )


def _bundle(*, items: tuple[ProjectItem, ...] | None = None, **overrides: object) -> ProjectBundle:
    fingerprint, project_id = _identity()
    values: dict[str, object] = {
        "project_id": project_id,
        "display_name": "Customer Churn Audit",
        "granted_root_fingerprint": fingerprint,
        "created_at": _CREATED,
        "updated_at": _UPDATED,
        "items": items or (_item(),),
        "permission_policy_sha256": _POLICY_SHA,
        "provider_policy_sha256": "b" * 64,
        "ingestion_report_sha256": "c" * 64,
        "snapshot_refs": (f"p3-snapshot-{'d' * 64}",),
        "evidence_bundle_refs": ("evidence-bundle-1",),
    }
    values.update(overrides)
    return build_project_bundle(**values)  # type: ignore[arg-type]


def _replace_payload(model: ProjectItem | ProjectBundle, **updates: object) -> dict[str, object]:
    payload = model.model_dump(mode="python")
    payload.update(updates)
    return payload


def test_schema_versions_and_public_round_trip_are_stable() -> None:
    bundle = _bundle()

    assert bundle.schema_version == PROJECT_BUNDLE_SCHEMA_VERSION
    assert bundle.items[0].schema_version == PROJECT_ITEM_SCHEMA_VERSION
    assert bundle.project_manifest.schema_version == PROJECT_MANIFEST_SCHEMA_VERSION

    encoded = bundle.model_dump_json()
    loaded = ProjectBundle.model_validate_json(encoded)
    assert loaded == bundle
    assert loaded.canonical_sha256() == bundle.canonical_sha256()
    assert loaded.identity_payload() == bundle.identity_payload()
    assert loaded.project_manifest.canonical_sha256() == bundle.project_manifest.canonical_sha256()
    assert json.loads(encoded)["contract_state"] == "schema_validated"


def test_versioned_fixture_loads_and_keeps_its_locked_canonical_hash() -> None:
    bundle = ProjectBundle.model_validate_json(_FIXTURE_PATH.read_text(encoding="utf-8"))

    assert bundle.canonical_sha256() == _FIXTURE_CANONICAL_SHA256
    verify_project_item_source(bundle.items[0], b'{"model":"logistic"}\n')
    verify_project_item_artifact(bundle.items[0], b'{"model":"logistic"}\n')


def test_models_are_strict_frozen_and_forbid_unknown_fields() -> None:
    item = _item()

    with pytest.raises(ValidationError, match="frozen"):
        item.relative_path = "other.json"  # type: ignore[misc]

    payload = item.model_dump(mode="python")
    payload["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectItem.model_validate(payload)

    payload = item.model_dump(mode="python")
    payload["byte_size"] = str(item.byte_size)
    with pytest.raises(ValidationError):
        ProjectItem.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [
        "",
        " configs/model.json",
        "configs/model.json ",
        "/etc/passwd",
        "//server/share/file.json",
        r"C:\secret\file.json",
        "C:relative/file.json",
        r"\\server\share\file.json",
        r"configs\model.json",
        "configs/../secret.json",
        "configs/./model.json",
        "../secret.json",
        ".",
        "configs//model.json",
        "configs/\x00model.json",
        "cafe\u0301/model.json",
    ],
)
def test_relative_project_path_rejects_noncanonical_or_unsafe_strings(path: str) -> None:
    with pytest.raises(ProjectIdentityError):
        normalize_relative_project_path(path)


@pytest.mark.parametrize(
    "path",
    ["configs/model.json", ".config/settings.json", "runs/2026-08-25/metrics.csv"],
)
def test_relative_project_path_accepts_canonical_posix_paths(path: str) -> None:
    assert normalize_relative_project_path(path) == path


def test_root_path_is_domain_hashed_and_not_serialized() -> None:
    fingerprint, project_id = _identity()
    bundle = _bundle()

    assert len(fingerprint) == 64
    assert project_id.startswith("p3-project-")
    assert project_id_for_root(fingerprint) == project_id
    assert _ROOT_PATH not in bundle.model_dump_json()
    assert _ROOT_PATH not in bundle.canonical_sha256()


def test_project_identity_changes_when_root_identity_changes() -> None:
    first = granted_root_fingerprint("/projects/one")
    second = granted_root_fingerprint("/projects/two")

    assert first != second
    assert project_id_for_root(first) != project_id_for_root(second)
    with pytest.raises(ProjectIdentityError):
        project_id_for_root("A" * 64)


def test_item_builder_derives_source_artifact_and_identity_hashes() -> None:
    raw = b'{"token":"REDACTED"}\n'
    artifact = b'{"token":"[REDACTED]"}\n'
    item = _item(source_bytes=raw, artifact_bytes=artifact)

    assert item.content_sha256 == content_sha256(raw)
    assert item.byte_size == len(raw)
    assert item.artifact.sha256 == content_sha256(artifact)
    assert item.artifact.byte_size == len(artifact)
    assert item.artifact.artifact_id == f"p3-artifact-{content_sha256(artifact)}"
    assert item.project_item_id.startswith("p3-item-")
    verify_project_item_source(item, raw)
    verify_project_item_artifact(item, artifact)


def test_item_identity_is_stable_across_ingestion_audit_time_only() -> None:
    first = _item(ingested_at="2026-08-25T01:00:00Z")
    second = _item(ingested_at="2026-08-25T02:00:00Z")

    assert first.project_item_id == second.project_item_id
    assert first.identity_payload() == second.identity_payload()
    assert first.canonical_sha256() != second.canonical_sha256()


def test_item_identity_changes_for_every_source_defining_change() -> None:
    baseline = _item()
    changed_content = _item(source_bytes=b'{"model":"tree"}\n')
    changed_path = _item(relative_path="configs/other.json")
    changed_collector = _item(collector=_collector(version="1.2.1"))
    _, project_id = _identity()
    changed_schema = build_project_item(
        project_id=project_id,
        relative_path="configs/model.json",
        source_type="config",
        media_type="application/json",
        source_schema="model-config/v2",
        source_bytes=b'{"model":"logistic"}\n',
        source_modified_at="2026-08-24T23:59:59Z",
        ingested_at=_CREATED,
        collector=_collector(),
        visibility="diagnosis",
    )

    ids = {
        baseline.project_item_id,
        changed_content.project_item_id,
        changed_path.project_item_id,
        changed_collector.project_item_id,
        changed_schema.project_item_id,
    }
    assert len(ids) == 5


def test_source_and_artifact_verification_rejects_tampering() -> None:
    item = _item(artifact_bytes=b"normalized")

    with pytest.raises(ProjectContractError, match="source bytes"):
        verify_project_item_source(item, b"tampered")
    with pytest.raises(ProjectContractError, match="artifact bytes"):
        verify_project_item_artifact(item, b"tampered")

    wrong_size = item.model_copy(update={"byte_size": item.byte_size + 1})
    with pytest.raises(ProjectContractError, match="byte_size"):
        verify_project_item_source(wrong_size, b'{"model":"logistic"}\n')

    wrong_artifact_size = item.artifact.model_copy(
        update={"byte_size": item.artifact.byte_size + 1}
    )
    wrong_artifact = item.model_copy(update={"artifact": wrong_artifact_size})
    with pytest.raises(ProjectContractError, match="artifact byte_size"):
        verify_project_item_artifact(wrong_artifact, b"normalized")


def test_unsafe_model_copy_cannot_bypass_item_identity_validation() -> None:
    item = _item()
    forged = item.model_copy(update={"content_sha256": "f" * 64})

    with pytest.raises(ValidationError, match="project_item_id"):
        forged.canonical_sha256()
    with pytest.raises(ValidationError, match="project_item_id"):
        build_project_manifest(item.project_id, (forged,))


@pytest.mark.parametrize(
    ("state", "reasons", "visibility", "valid"),
    [
        ("none", (), "diagnosis", True),
        ("none", ("secret",), "diagnosis", False),
        ("redacted", ("secret",), "diagnosis", True),
        ("redacted", (), "diagnosis", False),
        ("withheld", ("private-key",), "local_only", True),
        ("withheld", ("private-key",), "diagnosis", False),
        ("withheld", (), "local_only", False),
    ],
)
def test_redaction_state_is_reconciled(
    state: str, reasons: tuple[str, ...], visibility: str, valid: bool
) -> None:
    _, project_id = _identity()
    kwargs = {
        "project_id": project_id,
        "relative_path": "configs/model.json",
        "source_type": "config",
        "media_type": "application/json",
        "source_schema": "model-config/v1",
        "source_bytes": b"{}",
        "source_modified_at": _CREATED,
        "ingested_at": _UPDATED,
        "collector": _collector(),
        "visibility": visibility,
        "redaction_state": state,
        "redaction_reasons": reasons,
    }
    if valid:
        assert build_project_item(**kwargs).redaction_state == state  # type: ignore[arg-type]
    else:
        with pytest.raises(ValidationError):
            build_project_item(**kwargs)  # type: ignore[arg-type]


def test_redaction_reason_must_be_a_unique_stable_code() -> None:
    _, project_id = _identity()
    common = {
        "project_id": project_id,
        "relative_path": "secret.json",
        "source_type": "config",
        "media_type": "application/json",
        "source_schema": None,
        "source_bytes": b"{}",
        "source_modified_at": _CREATED,
        "ingested_at": _UPDATED,
        "collector": _collector(),
        "visibility": "local_only",
        "redaction_state": "withheld",
    }
    with pytest.raises(ValidationError, match="stable reason codes"):
        build_project_item(**common, redaction_reasons=("contains a secret",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="unique"):
        build_project_item(**common, redaction_reasons=("secret", "secret"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "visibility", "warnings", "valid"),
    [
        ("parsed", "diagnosis", (), True),
        ("parsed", "diagnosis", (ProjectParseWarning(code="coerced", message="Field normalized"),), True),
        ("unparsed", "local_only", (), False),
        ("unparsed", "local_only", (ProjectParseWarning(code="unknown", message="Unknown schema"),), True),
        ("failed", "diagnosis", (ProjectParseWarning(code="invalid", message="Parse failed"),), False),
        ("failed", "local_only", (ProjectParseWarning(code="invalid", message="Parse failed"),), True),
    ],
)
def test_parse_state_is_fail_closed(
    status: str,
    visibility: str,
    warnings: tuple[ProjectParseWarning, ...],
    valid: bool,
) -> None:
    _, project_id = _identity()
    kwargs = {
        "project_id": project_id,
        "relative_path": "configs/model.json",
        "source_type": "config",
        "media_type": "application/json",
        "source_schema": None,
        "source_bytes": b"{}",
        "source_modified_at": _CREATED,
        "ingested_at": _UPDATED,
        "collector": _collector(),
        "visibility": visibility,
        "parse_status": status,
        "parse_warnings": warnings,
    }
    if valid:
        assert build_project_item(**kwargs).parse_status == status  # type: ignore[arg-type]
    else:
        with pytest.raises(ValidationError):
            build_project_item(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-25",
        "2026-08-25T01:02:03",
        "2026-08-25T01:02:03+00:00",
        "2026-08-25T01:02:03+07:00",
        "2026-13-25T01:02:03Z",
        " 2026-08-25T01:02:03Z",
    ],
)
def test_item_requires_canonical_utc_timestamps(timestamp: str) -> None:
    _, project_id = _identity()
    with pytest.raises((ProjectIdentityError, ValidationError)):
        build_project_item(
            project_id=project_id,
            relative_path="config.json",
            source_type="config",
            media_type="application/json",
            source_schema=None,
            source_bytes=b"{}",
            source_modified_at=timestamp,
            ingested_at=_UPDATED,
            collector=_collector(),
            visibility="local_only",
        )


def test_artifact_reference_rejects_forged_id_and_wrong_types() -> None:
    artifact = ImmutableArtifactReference.from_bytes(b"content", media_type="text/plain")
    payload = artifact.model_dump(mode="python")
    payload["artifact_id"] = f"p3-artifact-{'0' * 64}"

    with pytest.raises(ValidationError, match="artifact_id"):
        ImmutableArtifactReference.model_validate(payload)
    with pytest.raises(ValidationError):
        ImmutableArtifactReference.from_bytes(b"content", media_type="TEXT/plain")


def test_manifest_and_bundle_are_order_independent() -> None:
    first = _item(relative_path="configs/a.json", source_bytes=b"a")
    second = _item(relative_path="configs/b.json", source_bytes=b"b")

    manifest_forward = build_project_manifest(first.project_id, (first, second))
    manifest_reverse = build_project_manifest(first.project_id, (second, first))
    bundle_forward = _bundle(items=(first, second))
    bundle_reverse = _bundle(items=(second, first))

    assert manifest_forward == manifest_reverse
    assert manifest_forward.manifest_sha256 == manifest_reverse.manifest_sha256
    assert bundle_forward == bundle_reverse
    assert bundle_forward.project_bundle_id == bundle_reverse.project_bundle_id
    assert bundle_forward.canonical_sha256() == bundle_reverse.canonical_sha256()


def test_duplicate_path_is_rejected_even_when_content_differs() -> None:
    first = _item(source_bytes=b"first")
    second = _item(source_bytes=b"second")

    with pytest.raises(ValidationError, match="duplicate relative_path"):
        build_project_manifest(first.project_id, (first, second))
    with pytest.raises(ValidationError, match="duplicate relative_path"):
        _bundle(items=(first, second))


def test_manifest_counts_and_digest_fail_closed() -> None:
    manifest = build_project_manifest(_item().project_id, (_item(),))

    for field, value, message in (
        ("item_count", 2, "item_count"),
        ("source_byte_count", manifest.source_byte_count + 1, "source_byte_count"),
        ("artifact_byte_count", manifest.artifact_byte_count + 1, "artifact_byte_count"),
        ("manifest_sha256", "f" * 64, "manifest_sha256"),
    ):
        payload = manifest.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError, match=message):
            type(manifest).model_validate(payload)


def test_empty_manifest_and_bundle_are_rejected() -> None:
    item = _item()
    manifest = build_project_manifest(item.project_id, (item,))

    with pytest.raises(ValueError, match="empty project manifest"):
        build_project_manifest(item.project_id, ())

    manifest_payload = manifest.model_dump(mode="python")
    manifest_payload["entries"] = ()
    with pytest.raises(ValidationError, match="at least one entry"):
        type(manifest).model_validate(manifest_payload)

    bundle = _bundle()
    bundle_payload = bundle.model_dump(mode="python")
    bundle_payload["items"] = ()
    with pytest.raises(ValidationError, match="at least one item"):
        ProjectBundle.model_validate(bundle_payload)

    bundle_payload = bundle.model_dump(mode="python")
    bundle_payload["collector_versions"] = ()
    with pytest.raises(ValidationError, match="at least one collector"):
        ProjectBundle.model_validate(bundle_payload)


def test_bundle_rejects_cross_project_items_and_root_identity_mismatch() -> None:
    bundle = _bundle()
    other_fingerprint = granted_root_fingerprint("/projects/other")
    other_project_id = project_id_for_root(other_fingerprint)
    foreign = build_project_item(
        project_id=other_project_id,
        relative_path="config.json",
        source_type="config",
        media_type="application/json",
        source_schema=None,
        source_bytes=b"{}",
        source_modified_at=_CREATED,
        ingested_at=_UPDATED,
        collector=_collector(),
        visibility="local_only",
    )

    with pytest.raises(ValueError, match="foreign project items"):
        _bundle(items=(foreign,))

    payload = _replace_payload(bundle, granted_root_fingerprint=other_fingerprint)
    with pytest.raises(ValidationError, match="project_id"):
        ProjectBundle.model_validate(payload)

    payload = bundle.model_dump(mode="python")
    payload["project_manifest"] = build_project_manifest(other_project_id, (foreign,))
    with pytest.raises(ValidationError, match="manifest belongs to a different project"):
        ProjectBundle.model_validate(payload)

    with pytest.raises(ValueError, match="foreign project items"):
        build_project_manifest(other_project_id, bundle.items)


def test_bundle_rejects_stale_manifest_and_collector_inventory() -> None:
    first = _item()
    second = _item(
        relative_path="metrics/run.json",
        source_bytes=b'{"loss":1.0}',
        collector=_collector(name="json-metrics"),
    )
    bundle = _bundle(items=(first, second))

    payload = _replace_payload(bundle, items=(first,))
    with pytest.raises(ValidationError, match="manifest"):
        ProjectBundle.model_validate(payload)

    payload = _replace_payload(bundle, collector_versions=(_collector(),))
    with pytest.raises(ValidationError, match="collector_versions"):
        ProjectBundle.model_validate(payload)


def test_bundle_identity_changes_when_a_bound_policy_changes() -> None:
    first = _bundle(permission_policy_sha256="1" * 64)
    second = _bundle(permission_policy_sha256="2" * 64)

    assert first.project_manifest == second.project_manifest
    assert first.project_bundle_id != second.project_bundle_id
    assert first.canonical_sha256() != second.canonical_sha256()


def test_bundle_references_are_sorted_unique_and_bound_to_identity() -> None:
    first_ref = f"p3-snapshot-{'1' * 64}"
    second_ref = f"p3-snapshot-{'2' * 64}"
    bundle = _bundle(snapshot_refs=(second_ref, first_ref))

    assert bundle.snapshot_refs == (first_ref, second_ref)
    with pytest.raises(ValueError, match="unique"):
        _bundle(snapshot_refs=(first_ref, first_ref))
    with pytest.raises(ValueError, match="invalid"):
        _bundle(snapshot_refs=("../snapshot",))


def test_bundle_updated_at_cannot_precede_created_at() -> None:
    with pytest.raises(ValidationError, match="updated_at"):
        _bundle(updated_at="2026-08-24T00:00:00Z")


def test_forged_bundle_id_and_unsafe_nested_copy_are_rejected() -> None:
    bundle = _bundle()
    forged_id = bundle.model_copy(update={"project_bundle_id": f"p3-bundle-{'0' * 64}"})
    with pytest.raises(ValidationError, match="project_bundle_id"):
        forged_id.canonical_sha256()

    forged_item = bundle.items[0].model_copy(update={"content_sha256": "f" * 64})
    forged_nested = bundle.model_copy(update={"items": (forged_item,)})
    with pytest.raises(ValidationError, match="project_item_id"):
        forged_nested.canonical_sha256()


def test_canonical_project_hash_is_key_order_and_nfc_invariant() -> None:
    composed = {"b": "café", "a": [1, True, None]}
    decomposed = {"a": [1, True, None], "b": "cafe\u0301"}

    assert canonical_project_sha256(composed) == canonical_project_sha256(decomposed)
    with pytest.raises(ProjectIdentityError, match="non-finite"):
        canonical_project_sha256({"value": float("nan")})
    with pytest.raises(ProjectIdentityError, match="negative zero"):
        canonical_project_sha256({"value": -0.0})
    assert canonical_project_sha256({"value": 1}) != canonical_project_sha256({"value": "1"})


def test_canonical_project_hash_matches_an_independent_manual_oracle() -> None:
    expected_bytes = b'{"a":"bar","b":"foo"}'
    expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()

    assert canonical_project_sha256({"b": "foo", "a": "bar"}) == expected_sha256


def test_collector_and_warning_models_are_canonical_and_deterministic() -> None:
    warning_a = ProjectParseWarning(code="z-last", message="Second warning")
    warning_b = ProjectParseWarning(code="a-first", message="First warning")
    _, project_id = _identity()
    item = build_project_item(
        project_id=project_id,
        relative_path="unknown.data",
        source_type="other",
        media_type="application/octet-stream",
        source_schema=None,
        source_bytes=b"opaque",
        source_modified_at=_CREATED,
        ingested_at=_UPDATED,
        collector=_collector(),
        visibility="local_only",
        parse_status="unparsed",
        parse_warnings=(warning_a, warning_b),
    )

    assert [warning.code for warning in item.parse_warnings] == ["a-first", "z-last"]
    with pytest.raises(ValidationError, match="unique"):
        build_project_item(
            project_id=project_id,
            relative_path="duplicate.data",
            source_type="other",
            media_type="application/octet-stream",
            source_schema=None,
            source_bytes=b"opaque",
            source_modified_at=_CREATED,
            ingested_at=_UPDATED,
            collector=_collector(),
            visibility="local_only",
            parse_status="unparsed",
            parse_warnings=(warning_a, warning_a),
        )


def test_parent_run_identifier_and_content_hash_input_are_strict() -> None:
    _, project_id = _identity()
    common = {
        "project_id": project_id,
        "relative_path": "runs/metrics.json",
        "source_type": "metrics",
        "media_type": "application/json",
        "source_schema": None,
        "source_bytes": b"{}",
        "source_modified_at": _CREATED,
        "ingested_at": _UPDATED,
        "collector": _collector(),
        "visibility": "local_only",
    }
    item = build_project_item(**common, parent_run_id="run-001")  # type: ignore[arg-type]
    assert item.parent_run_id == "run-001"

    with pytest.raises(ValidationError, match="portable identifier"):
        build_project_item(**common, parent_run_id="../run")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="requires bytes"):
        content_sha256(bytearray(b"not-bytes"))  # type: ignore[arg-type]
