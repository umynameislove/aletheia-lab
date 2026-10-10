"""Product-facing staging for safe local-project import previews."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.candidates import (
    MappingCandidates,
    build_mapping_candidates,
    confirm_mapping_is_eligible,
)
from aletheia_lab.product.lifecycle import (
    ProductLifecycleRecord,
    ProductLifecycleStore,
    build_product_lifecycle_record,
    put_product_confirmation,
)
from aletheia_lab.product.mapping import (
    build_confirm_mapping,
    load_stored_mapping,
    metric_definitions_match_observations,
)
from aletheia_lab.project.collectors import collect_git_state, collect_project_files
from aletheia_lab.project.identity import SNAPSHOT_ID_PATTERN
from aletheia_lab.project.import_policy import ProjectImportPreview, ProjectValidationIssue
from aletheia_lab.project.importer import (
    ProjectImportArtifact,
    ProjectImportBoundaryError,
    grant_project_root,
    import_local_project,
    inspect_local_project,
)
from aletheia_lab.project.mapping import (
    bind_project_mapping,
    validate_project_mapping,
)
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.snapshots import build_project_snapshot

_PREVIEW_FAILED_MESSAGE: Final[str] = "The project preview could not be completed safely."
_CONFIRM_FAILED_MESSAGE: Final[str] = "The project import could not be confirmed safely."
_PREVIEW_BLOCKED_MESSAGE: Final[str] = "The preview has blockers and cannot be confirmed."
_PREVIEW_STALE_MESSAGE: Final[str] = "The project changed and must be previewed again."
_PREVIEW_EXPIRED_MESSAGE: Final[str] = "The preview expired and must be created again."
_MAPPING_INVALID_MESSAGE: Final[str] = "The project mapping is invalid or incomplete."
_STORE_INTEGRITY_MESSAGE: Final[str] = "Stored product state failed its integrity check."
_IMPORTED_PROJECT_NAME: Final[str] = "Imported Project"
_PREVIEW_TTL: Final[timedelta] = timedelta(minutes=30)
_UTC_TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"
)


@dataclass(frozen=True, slots=True)
class _PreviewState:
    root: str
    source_state_sha256: str | None
    preview: ProjectImportPreview


@dataclass(frozen=True, slots=True)
class _ConfirmationInput:
    state: _PreviewState | None
    leased_at: str | None
    replay: dict[str, object] | None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        return None
    try:
        timestamp = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo == UTC else None


def _issue_projection(issue: ProjectValidationIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "severity": issue.severity,
        "stage": issue.stage,
        "message": issue.message,
        "relative_path": issue.relative_path,
        "occurrences": issue.occurrences,
    }


def _public_preview(
    preview_id: str,
    preview: ProjectImportPreview,
    mapping_candidates: MappingCandidates,
) -> dict[str, object]:
    blockers = tuple(
        _issue_projection(issue) for issue in preview.issues if issue.severity == "blocker"
    )
    warnings = tuple(
        _issue_projection(issue) for issue in preview.issues if issue.severity != "blocker"
    )
    outbound_categories = tuple(
        sorted(
            {
                decision.source_type
                for decision in preview.decisions
                if decision.action in {"include", "redact"} and decision.source_type is not None
            }
        )
    )
    return {
        "preview_id": preview_id,
        "included_count": preview.included_count,
        "excluded_count": preview.excluded_count,
        "redacted_count": preview.redacted_count,
        "withheld_count": preview.withheld_count,
        "blockers": list(blockers),
        "warnings": list(warnings),
        "outbound_categories": list(outbound_categories),
        "mapping_candidates": mapping_candidates.model_dump(mode="json"),
    }


def preview_product_import(store_root: Path, root: str) -> dict[str, object]:
    """Stage a P3 inspection without persisting a project, bundle, or snapshot."""

    failure: tuple[str, str] | None = None
    try:
        grant = grant_project_root(root)
        created_at = _utc_now()
        inspection = inspect_local_project(grant, ingested_at=created_at)
        preview = inspection.preview
        mapping_candidates = build_mapping_candidates(
            inspection.items,
            inspection.artifacts,
        )
        canonical_root = str(Path(root).resolve(strict=True))
        record = build_product_lifecycle_record(
            record_kind="preview",
            payload={
                "root": canonical_root,
                "status": "active",
                "source_state_sha256": inspection.source_state_sha256,
                "p3_preview": preview.model_dump(mode="json"),
            },
        )
        with ProductLifecycleStore(store_root) as lifecycle:
            lifecycle.put_preview(record, leased_at=created_at)
        return _public_preview(record.record_id, preview, mapping_candidates)
    except ProductError:
        raise
    except ProjectImportBoundaryError as exc:
        failure = (f"import_{exc.issue.code}", exc.issue.message)
    except Exception:
        failure = ("preview_failed", _PREVIEW_FAILED_MESSAGE)
    if failure is None:
        raise AssertionError("preview failure mapping did not produce a safe error")
    raise ProductError(*failure)


def _preview_state(record: ProductLifecycleRecord) -> _PreviewState | None:
    try:
        payload = record.payload()
        if set(payload) != {
            "root",
            "status",
            "source_state_sha256",
            "p3_preview",
        }:
            return None
        root = payload["root"]
        status = payload["status"]
        source_state_sha256 = payload["source_state_sha256"]
        if not isinstance(root, str) or status != "active":
            return None
        preview = ProjectImportPreview.model_validate_json(
            json.dumps(
                payload["p3_preview"],
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        blocked = any(issue.severity == "blocker" for issue in preview.issues)
        if blocked:
            if source_state_sha256 is not None:
                return None
        elif (
            not isinstance(source_state_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_state_sha256) is None
        ):
            return None
        return _PreviewState(root, source_state_sha256, preview)
    except (KeyError, TypeError, ValueError):
        return None


def _confirmed_response(
    record: ProductLifecycleRecord,
    *,
    mapping: dict[str, object],
) -> dict[str, object] | None:
    payload = record.payload()
    snapshot_id = payload.get("snapshot_id")
    configuration = load_stored_mapping(payload.get("mapping_configuration"))
    if (
        record.project_id is None
        or payload.get("status") != "confirmed"
        or configuration is None
        or configuration.project_id != record.project_id
        or not isinstance(snapshot_id, str)
        or re.fullmatch(SNAPSHOT_ID_PATTERN, snapshot_id) is None
    ):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    requested = build_confirm_mapping(
        mapping,
        project_id=configuration.project_id,
        project_bundle_id=configuration.project_bundle_id,
        file_collection_sha256=configuration.file_collection_sha256,
    )
    if requested.mapping_sha256 != configuration.mapping_sha256:
        return None
    return {
        "project_id": record.project_id,
        "snapshot_id": snapshot_id,
        "status": "confirmed",
    }


def _artifact_objects(
    artifacts: tuple[ProjectImportArtifact, ...],
) -> dict[str, tuple[str, bytes]]:
    objects: dict[str, tuple[str, bytes]] = {}
    for value in artifacts:
        reference = value.reference
        payload = value.content
        candidate = (reference.media_type, payload)
        existing = objects.get(reference.sha256)
        if existing is not None and existing != candidate:
            raise ValueError("artifact digest collision")
        objects[reference.sha256] = candidate
    return objects


def _write_product_confirmation(
    connection: sqlite3.Connection,
    *,
    preview_id: str,
    record: ProductLifecycleRecord,
) -> None:
    put_product_confirmation(connection, preview_id, record)


def _confirmation_input(
    store_root: Path,
    preview_id: str,
    mapping: dict[str, object],
) -> _ConfirmationInput:
    with ProductLifecycleStore(store_root) as lifecycle:
        confirmed = lifecycle.get_confirmation(preview_id)
        if confirmed is not None:
            replay = _confirmed_response(confirmed, mapping=mapping)
            if replay is None:
                raise ProductError(
                    "preview_already_confirmed",
                    "The preview was already confirmed differently.",
                )
            return _ConfirmationInput(None, None, replay)
        staged = lifecycle.get(preview_id, expected_kind="preview")
        leased_at = lifecycle.preview_leased_at(preview_id)

    state = _preview_state(staged)
    if state is None:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    if state.preview.blocked_count or any(
        issue.severity == "blocker" for issue in state.preview.issues
    ):
        raise ProductError("preview_blocked", _PREVIEW_BLOCKED_MESSAGE)
    if state.source_state_sha256 is None or _utc_timestamp(leased_at) is None:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    return _ConfirmationInput(state, leased_at, None)


def _confirmation_capture_time(leased_at: str) -> str:
    lease_time = _utc_timestamp(leased_at)
    captured_at = _utc_now()
    captured_time = _utc_timestamp(captured_at)
    if lease_time is None or captured_time is None:
        raise ProductError("confirm_failed", _CONFIRM_FAILED_MESSAGE)
    if captured_time >= lease_time + _PREVIEW_TTL:
        raise ProductError("preview_expired", _PREVIEW_EXPIRED_MESSAGE)
    return captured_at


def _confirm_fresh_import(
    store_root: Path,
    preview_id: str,
    mapping: dict[str, object],
    state: _PreviewState,
    captured_at: str,
) -> dict[str, object]:
    grant = grant_project_root(state.root)
    current = inspect_local_project(grant, ingested_at=captured_at)
    if current.source_state_sha256 != state.source_state_sha256 or current.preview != state.preview:
        raise ProductError("preview_stale", _PREVIEW_STALE_MESSAGE)

    imported = import_local_project(
        grant,
        display_name=_IMPORTED_PROJECT_NAME,
        ingested_at=captured_at,
    )
    if imported.bundle is None or imported.preview != state.preview:
        raise ProductError("preview_stale", _PREVIEW_STALE_MESSAGE)
    collection = collect_project_files(imported.bundle, imported.artifacts)
    configuration = build_confirm_mapping(
        mapping,
        project_id=imported.bundle.project_id,
        project_bundle_id=imported.bundle.project_bundle_id,
        file_collection_sha256=collection.collection_sha256,
    )
    current_candidates = build_mapping_candidates(current.items, current.artifacts)
    if not confirm_mapping_is_eligible(configuration, current_candidates):
        raise ProductError("mapping_invalid", _MAPPING_INVALID_MESSAGE)
    mapping_result = validate_project_mapping(
        imported.bundle,
        imported.artifacts,
        collection,
        configuration,
    )
    if mapping_result.status != "valid" or not metric_definitions_match_observations(
        configuration,
        mapping_result,
    ):
        raise ProductError("mapping_invalid", _MAPPING_INVALID_MESSAGE)
    git_state = collect_git_state(grant)
    final_inspection = inspect_local_project(grant, ingested_at=captured_at)
    if (
        final_inspection.source_state_sha256 != state.source_state_sha256
        or final_inspection.preview != state.preview
    ):
        raise ProductError("preview_stale", _PREVIEW_STALE_MESSAGE)

    bound_bundle = bind_project_mapping(
        imported.bundle,
        configuration,
        mapping_result,
    )
    snapshot = build_project_snapshot(
        bound_bundle,
        collection,
        git_state,
        configuration,
        mapping_result,
        captured_at=captured_at,
    )
    project_state = build_product_lifecycle_record(
        record_kind="project_state",
        project_id=bound_bundle.project_id,
        payload={
            "status": "confirmed",
            "root": state.root,
            "preview_id": preview_id,
            "source_state_sha256": state.source_state_sha256,
            "mapping_configuration": configuration.model_dump(mode="json"),
            "project_bundle_id": bound_bundle.project_bundle_id,
            "snapshot_id": snapshot.snapshot_id,
        },
    )
    with ProjectStore(store_root) as store:
        store.persist(
            (bound_bundle, snapshot),
            artifacts=_artifact_objects(imported.artifacts),
            transaction_write=partial(
                _write_product_confirmation,
                preview_id=preview_id,
                record=project_state,
            ),
        )
    return {
        "project_id": bound_bundle.project_id,
        "snapshot_id": snapshot.snapshot_id,
        "status": "confirmed",
    }


def confirm_product_import(
    store_root: Path,
    preview_id: str,
    mapping: dict[str, object],
) -> dict[str, object]:
    """Revalidate and atomically confirm one staged P3 import."""

    confirmation = _confirmation_input(store_root, preview_id, mapping)
    if confirmation.replay is not None:
        return confirmation.replay
    if confirmation.state is None or confirmation.leased_at is None:
        raise AssertionError("active confirmation input is incomplete")
    captured_at = _confirmation_capture_time(confirmation.leased_at)
    failure: tuple[str, str] | None = None
    try:
        return _confirm_fresh_import(
            store_root,
            preview_id,
            mapping,
            confirmation.state,
            captured_at,
        )
    except ProductError:
        raise
    except ProjectImportBoundaryError:
        failure = ("preview_stale", _PREVIEW_STALE_MESSAGE)
    except Exception:
        failure = ("confirm_failed", _CONFIRM_FAILED_MESSAGE)
    if failure is None:
        raise AssertionError("confirmation failure mapping did not produce a safe error")
    raise ProductError(*failure)
