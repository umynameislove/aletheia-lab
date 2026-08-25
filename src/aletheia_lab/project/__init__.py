"""Versioned contracts for safe local-project ingestion."""

from aletheia_lab.project.contracts import (
    PROJECT_BUNDLE_SCHEMA_VERSION,
    PROJECT_ITEM_SCHEMA_VERSION,
    PROJECT_MANIFEST_SCHEMA_VERSION,
    ImmutableArtifactReference,
    ProjectBundle,
    ProjectCollector,
    ProjectItem,
    ProjectManifest,
    ProjectParseWarning,
    build_project_bundle,
    build_project_item,
    build_project_manifest,
    verify_project_item_artifact,
    verify_project_item_source,
)
from aletheia_lab.project.identity import (
    ProjectIdentityError,
    canonical_project_sha256,
    content_sha256,
    granted_root_fingerprint,
    normalize_relative_project_path,
    project_id_for_root,
)

__all__ = [
    "PROJECT_BUNDLE_SCHEMA_VERSION",
    "PROJECT_ITEM_SCHEMA_VERSION",
    "PROJECT_MANIFEST_SCHEMA_VERSION",
    "ImmutableArtifactReference",
    "ProjectBundle",
    "ProjectCollector",
    "ProjectIdentityError",
    "ProjectItem",
    "ProjectManifest",
    "ProjectParseWarning",
    "build_project_bundle",
    "build_project_item",
    "build_project_manifest",
    "canonical_project_sha256",
    "content_sha256",
    "granted_root_fingerprint",
    "normalize_relative_project_path",
    "project_id_for_root",
    "verify_project_item_artifact",
    "verify_project_item_source",
]
