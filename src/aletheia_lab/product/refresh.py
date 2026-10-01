"""Product refresh orchestration over immutable P3 project state."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.lifecycle import (
    ProductLifecycleRecord,
    ProductLifecycleStore,
    build_product_lifecycle_record,
    put_current_project_state,
    validate_product_record_id,
)
from aletheia_lab.product.mapping import (
    adverse_metric_change_ids,
    load_stored_mapping,
    metric_definitions_match_observations,
    rebind_stored_mapping,
)
from aletheia_lab.project.closeout import build_project_closeout
from aletheia_lab.project.collectors import collect_git_state, collect_project_files
from aletheia_lab.project.contracts import ProjectBundle
from aletheia_lab.project.identity import (
    PROJECT_BUNDLE_ID_PATTERN,
    PROJECT_EVIDENCE_BUNDLE_ID_PATTERN,
    REGRESSION_EVENT_ID_PATTERN,
    SHA256_PATTERN,
    SNAPSHOT_COMPARISON_ID_PATTERN,
    SNAPSHOT_ID_PATTERN,
)
from aletheia_lab.project.import_policy import ProjectImportPreview
from aletheia_lab.project.importer import (
    ProjectImportArtifact,
    ProjectImportBoundaryError,
    ProjectImportInspection,
    grant_project_root,
    import_local_project,
    inspect_local_project,
)
from aletheia_lab.project.lineage import ProjectLineageGraph, build_regression_lineage
from aletheia_lab.project.mapping import (
    ProjectMappingConfiguration,
    bind_project_mapping,
    validate_project_mapping,
)
from aletheia_lab.project.persistence import ProjectStore, StoredProjectModel
from aletheia_lab.project.regression import (
    ProjectEvidenceBundle,
    ProjectRegressionEvent,
    ProjectSnapshotComparison,
    build_project_regression_event,
    build_project_regression_evidence,
    compare_project_snapshots,
)
from aletheia_lab.project.snapshots import ProjectSnapshot, build_project_snapshot

_STORE_INTEGRITY_MESSAGE: Final[str] = "Stored product state failed its integrity check."
_REFRESH_FAILED_MESSAGE: Final[str] = "The project could not be refreshed safely."
_SOURCE_UNAVAILABLE_MESSAGE: Final[str] = (
    "The project source is unavailable and cannot be refreshed."
)
_MAPPING_INVALID_MESSAGE: Final[str] = "The project mapping is invalid or incomplete."
_SOURCE_CHANGED_MESSAGE: Final[str] = "The project changed while it was being refreshed."
_IMPORTED_PROJECT_NAME: Final[str] = "Imported Project"


@dataclass(frozen=True, slots=True)
class _StoredProjectState:
    status: str
    root: str
    preview_id: str
    source_state_sha256: str
    project_bundle_id: str
    snapshot_id: str
    mapping: ProjectMappingConfiguration
    previous_snapshot_id: str | None
    comparison_id: str | None
    event_id: str | None
    evidence_bundle_id: str | None
    lineage_graph_id: str | None
    adverse_metric_change_ids: tuple[str, ...]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _stored_project_state(record: ProductLifecycleRecord) -> _StoredProjectState | None:
    try:
        payload = record.payload()
        base_keys = {
            "status",
            "root",
            "preview_id",
            "source_state_sha256",
            "mapping_configuration",
            "project_bundle_id",
            "snapshot_id",
        }
        generation_keys = {
            "previous_snapshot_id",
            "comparison_id",
            "event_id",
            "evidence_bundle_id",
            "lineage_graph_id",
            "adverse_metric_change_ids",
        }
        status = payload.get("status")
        if not (
            (status == "confirmed" and set(payload) == base_keys)
            or (status == "refreshed" and set(payload) == base_keys | generation_keys)
        ):
            return None
        if not isinstance(status, str):
            return None
        if record.project_id is None:
            return None
        root = payload["root"]
        preview_id = payload["preview_id"]
        source_state_sha256 = payload["source_state_sha256"]
        project_bundle_id = payload["project_bundle_id"]
        snapshot_id = payload["snapshot_id"]
        if (
            not isinstance(root, str)
            or not isinstance(preview_id, str)
            or not isinstance(source_state_sha256, str)
            or re.fullmatch(SHA256_PATTERN, source_state_sha256) is None
            or not isinstance(project_bundle_id, str)
            or re.fullmatch(PROJECT_BUNDLE_ID_PATTERN, project_bundle_id) is None
            or not isinstance(snapshot_id, str)
            or re.fullmatch(SNAPSHOT_ID_PATTERN, snapshot_id) is None
        ):
            return None
        validate_product_record_id(preview_id, expected_kind="preview")
        mapping = load_stored_mapping(payload["mapping_configuration"])
        if mapping is None or mapping.project_id != record.project_id:
            return None
        previous_snapshot_id: str | None = None
        comparison_id: str | None = None
        event_id: str | None = None
        evidence_bundle_id: str | None = None
        lineage_graph_id: str | None = None
        adverse_metric_change_ids: tuple[str, ...] = ()
        if status == "refreshed":
            raw_previous_snapshot_id = payload["previous_snapshot_id"]
            raw_comparison_id = payload["comparison_id"]
            raw_event_id = payload["event_id"]
            raw_evidence_bundle_id = payload["evidence_bundle_id"]
            raw_lineage_graph_id = payload["lineage_graph_id"]
            raw_adverse_ids = payload["adverse_metric_change_ids"]
            if (
                not isinstance(raw_previous_snapshot_id, str)
                or re.fullmatch(SNAPSHOT_ID_PATTERN, raw_previous_snapshot_id) is None
                or not isinstance(raw_comparison_id, str)
                or re.fullmatch(SNAPSHOT_COMPARISON_ID_PATTERN, raw_comparison_id) is None
                or not isinstance(raw_adverse_ids, list)
                or any(
                    not isinstance(value, str)
                    or re.fullmatch(r"p3-metric-change-[0-9a-f]{64}", value) is None
                    for value in raw_adverse_ids
                )
                or len(raw_adverse_ids) != len(set(raw_adverse_ids))
            ):
                return None
            previous_snapshot_id = raw_previous_snapshot_id
            comparison_id = raw_comparison_id
            adverse_metric_change_ids = tuple(sorted(raw_adverse_ids))
            generation_ids = (
                raw_event_id,
                raw_evidence_bundle_id,
                raw_lineage_graph_id,
            )
            if adverse_metric_change_ids:
                if (
                    not isinstance(raw_event_id, str)
                    or re.fullmatch(REGRESSION_EVENT_ID_PATTERN, raw_event_id) is None
                    or not isinstance(raw_evidence_bundle_id, str)
                    or re.fullmatch(
                        PROJECT_EVIDENCE_BUNDLE_ID_PATTERN,
                        raw_evidence_bundle_id,
                    )
                    is None
                    or not isinstance(raw_lineage_graph_id, str)
                    or re.fullmatch(
                        r"p3-lineage-graph-[0-9a-f]{64}",
                        raw_lineage_graph_id,
                    )
                    is None
                ):
                    return None
                event_id = raw_event_id
                evidence_bundle_id = raw_evidence_bundle_id
                lineage_graph_id = raw_lineage_graph_id
            elif any(value is not None for value in generation_ids):
                return None
        return _StoredProjectState(
            status=status,
            root=root,
            preview_id=preview_id,
            source_state_sha256=source_state_sha256,
            project_bundle_id=project_bundle_id,
            snapshot_id=snapshot_id,
            mapping=mapping,
            previous_snapshot_id=previous_snapshot_id,
            comparison_id=comparison_id,
            event_id=event_id,
            evidence_bundle_id=evidence_bundle_id,
            lineage_graph_id=lineage_graph_id,
            adverse_metric_change_ids=adverse_metric_change_ids,
        )
    except (KeyError, TypeError, ValueError, ProductError):
        return None


def _load_immutable_state(
    store_root: Path,
    record: ProductLifecycleRecord,
) -> tuple[_StoredProjectState, ProjectBundle, ProjectSnapshot]:
    state = _stored_project_state(record)
    if state is None or record.project_id is None:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    bundle: StoredProjectModel | None = None
    snapshot: StoredProjectModel | None = None
    load_failed = False
    try:
        with ProjectStore(store_root) as store:
            bundle = store.load(state.project_bundle_id)
            snapshot = store.load(state.snapshot_id)
            if state.status == "refreshed":
                _reconcile_refresh_generation(store, state, record.project_id)
    except Exception:
        load_failed = True
    if load_failed:
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    if (
        not isinstance(bundle, ProjectBundle)
        or not isinstance(snapshot, ProjectSnapshot)
        or bundle.project_id != record.project_id
        or snapshot.project_id != record.project_id
        or snapshot.snapshot_id != state.snapshot_id
        or snapshot.source_bundle_id != bundle.project_bundle_id
        or bundle.mapping_configuration_sha256 != state.mapping.mapping_sha256
        or snapshot.mapping_configuration_sha256 != state.mapping.mapping_sha256
    ):
        raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
    return state, bundle, snapshot


def _reconcile_refresh_generation(
    store: ProjectStore,
    state: _StoredProjectState,
    project_id: str,
) -> None:
    if state.previous_snapshot_id is None or state.comparison_id is None:
        raise ValueError("refreshed state omits generation identifiers")
    previous = store.load(state.previous_snapshot_id)
    comparison = store.load(state.comparison_id)
    if (
        not isinstance(previous, ProjectSnapshot)
        or not isinstance(comparison, ProjectSnapshotComparison)
        or previous.project_id != project_id
        or comparison.project_id != project_id
        or comparison.status != "changed"
        or comparison.before_snapshot_id != previous.snapshot_id
        or comparison.after_snapshot_id != state.snapshot_id
    ):
        raise ValueError("refreshed comparison does not reconcile")
    expected_adverse = adverse_metric_change_ids(
        comparison,
        state.mapping.metric_definitions,
    )
    if expected_adverse != state.adverse_metric_change_ids:
        raise ValueError("stored adverse metric changes do not reconcile")
    if not expected_adverse:
        return
    if (
        state.event_id is None
        or state.evidence_bundle_id is None
        or state.lineage_graph_id is None
    ):
        raise ValueError("adverse generation omits regression records")
    event = store.load(state.event_id)
    evidence = store.load(state.evidence_bundle_id)
    lineage = store.load(state.lineage_graph_id)
    before_bundle = store.load(previous.source_bundle_id)
    after_bundle = store.load(state.project_bundle_id)
    if (
        not isinstance(event, ProjectRegressionEvent)
        or not isinstance(evidence, ProjectEvidenceBundle)
        or not isinstance(lineage, ProjectLineageGraph)
        or not isinstance(before_bundle, ProjectBundle)
        or not isinstance(after_bundle, ProjectBundle)
    ):
        raise ValueError("refreshed regression generation has invalid record types")
    build_project_closeout(
        store,
        project_id=project_id,
        before_bundle_id=before_bundle.project_bundle_id,
        after_bundle_id=after_bundle.project_bundle_id,
        before_snapshot_id=previous.snapshot_id,
        after_snapshot_id=state.snapshot_id,
        comparison_id=comparison.comparison_id,
        event_id=event.event_id,
        evidence_bundle_id=evidence.evidence_bundle_id,
        lineage_graph_id=lineage.graph_id,
    )


def _artifact_objects(
    artifacts: tuple[ProjectImportArtifact, ...],
) -> dict[str, tuple[str, bytes]]:
    objects: dict[str, tuple[str, bytes]] = {}
    for artifact in artifacts:
        reference = artifact.reference
        candidate = (reference.media_type, artifact.content)
        existing = objects.get(reference.sha256)
        if existing is not None and existing != candidate:
            raise ValueError("artifact digest collision")
        objects[reference.sha256] = candidate
    return objects


def _write_project_state(
    connection: sqlite3.Connection,
    *,
    record: ProductLifecycleRecord,
) -> None:
    put_current_project_state(connection, record)


def _changed_refresh(
    store_root: Path,
    record: ProductLifecycleRecord,
    state: _StoredProjectState,
    before_bundle: ProjectBundle,
    before_snapshot: ProjectSnapshot,
    *,
    source_state_sha256: str,
    preview: ProjectImportPreview,
) -> dict[str, object]:
    captured_at = _utc_now()
    try:
        grant = grant_project_root(state.root)
        imported = import_local_project(
            grant,
            display_name=_IMPORTED_PROJECT_NAME,
            ingested_at=captured_at,
        )
        if imported.bundle is None or imported.preview != preview:
            raise ProductError("refresh_stale", _SOURCE_CHANGED_MESSAGE)
        collection = collect_project_files(imported.bundle, imported.artifacts)
        configuration = rebind_stored_mapping(
            state.mapping,
            before_bundle,
            imported.bundle,
            file_collection_sha256=collection.collection_sha256,
        )
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
            final_inspection.source_state_sha256 != source_state_sha256
            or final_inspection.preview != preview
        ):
            raise ProductError("refresh_stale", _SOURCE_CHANGED_MESSAGE)

        bound_bundle = bind_project_mapping(
            imported.bundle,
            configuration,
            mapping_result,
        )
        after_snapshot = build_project_snapshot(
            bound_bundle,
            collection,
            git_state,
            configuration,
            mapping_result,
            captured_at=captured_at,
        )
        comparison = compare_project_snapshots(
            before_snapshot,
            after_snapshot,
            after_git_state=git_state,
        )
        if comparison.status != "changed":
            raise ProductError("refresh_failed", _REFRESH_FAILED_MESSAGE)

        event_id: str | None = None
        evidence_bundle_id: str | None = None
        lineage_graph_id: str | None = None
        adverse_change_ids = adverse_metric_change_ids(
            comparison,
            configuration.metric_definitions,
        )
        if adverse_change_ids:
            event = build_project_regression_event(comparison)
            evidence = build_project_regression_evidence(
                before_snapshot,
                after_snapshot,
                comparison,
                event,
            )
            lineage = build_regression_lineage(
                before_snapshot,
                after_snapshot,
                comparison,
                event,
                evidence,
            )
            generation_models: tuple[StoredProjectModel, ...] = (
                bound_bundle,
                after_snapshot,
                comparison,
                event,
                evidence,
                lineage,
            )
            event_id = event.event_id
            evidence_bundle_id = evidence.evidence_bundle_id
            lineage_graph_id = lineage.graph_id
        else:
            generation_models = (bound_bundle, after_snapshot, comparison)

        project_state = build_product_lifecycle_record(
            record_kind="project_state",
            project_id=record.project_id,
            payload={
                "status": "refreshed",
                "root": state.root,
                "preview_id": state.preview_id,
                "source_state_sha256": source_state_sha256,
                "mapping_configuration": configuration.model_dump(mode="json"),
                "project_bundle_id": bound_bundle.project_bundle_id,
                "snapshot_id": after_snapshot.snapshot_id,
                "previous_snapshot_id": before_snapshot.snapshot_id,
                "comparison_id": comparison.comparison_id,
                "event_id": event_id,
                "evidence_bundle_id": evidence_bundle_id,
                "lineage_graph_id": lineage_graph_id,
                "adverse_metric_change_ids": adverse_change_ids,
            },
        )
        with ProjectStore(store_root) as store:
            store.persist(
                generation_models,
                artifacts=_artifact_objects(imported.artifacts),
                transaction_write=partial(_write_project_state, record=project_state),
            )
        return {
            "project_id": record.project_id,
            "snapshot_id": after_snapshot.snapshot_id,
            "status": "new_snapshot",
        }
    except ProductError:
        raise
    except Exception:
        pass
    raise ProductError("refresh_failed", _REFRESH_FAILED_MESSAGE)


def refresh_product_project(store_root: Path, project_id: str) -> dict[str, object]:
    """Revalidate one project and avoid minting state when its source is unchanged."""

    with ProductLifecycleStore(store_root) as lifecycle:
        record = lifecycle.get_project_state(project_id)
    state, bundle, snapshot = _load_immutable_state(store_root, record)
    inspection: ProjectImportInspection | None = None
    failure: tuple[str, str] | None = None
    try:
        grant = grant_project_root(state.root)
        inspection = inspect_local_project(grant, ingested_at=_utc_now())
    except ProjectImportBoundaryError:
        failure = ("refresh_source_unavailable", _SOURCE_UNAVAILABLE_MESSAGE)
    except ProductError:
        raise
    except Exception:
        failure = ("refresh_failed", _REFRESH_FAILED_MESSAGE)
    if failure is not None:
        raise ProductError(*failure)
    if inspection is None:
        raise AssertionError("refresh inspection did not produce a result")
    if inspection.source_state_sha256 is None:
        raise ProductError("refresh_source_unavailable", _SOURCE_UNAVAILABLE_MESSAGE)
    if inspection.source_state_sha256 != state.source_state_sha256:
        return _changed_refresh(
            store_root,
            record,
            state,
            bundle,
            snapshot,
            source_state_sha256=inspection.source_state_sha256,
            preview=inspection.preview,
        )
    return {
        "project_id": record.project_id,
        "snapshot_id": snapshot.snapshot_id,
        "status": "unchanged",
    }
