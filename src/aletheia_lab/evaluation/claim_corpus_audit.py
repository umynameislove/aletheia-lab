"""Independent, read-only audit of a published claim-corpus run."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusManifest,
    ClaimCorpusStoreReceipt,
    ClaimSupportCorpusEntry,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN, content_sha256

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
_RUN_ID = re.compile(r"^ccrun-[0-9a-f]{64}$")
AuditCode = Literal[
    "invalid_run_id",
    "missing_metadata",
    "invalid_metadata",
    "identity_mismatch",
    "missing_object",
    "corrupt_object",
    "unexpected_file",
    "duplicate_entry",
    "duplicate_source_claim",
    "cross_source_binding",
    "visibility_leakage",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ClaimCorpusAuditFinding(_StrictFrozenModel):
    code: AuditCode
    subject: str


class ClaimCorpusAuditReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-support-corpus-audit/v1"] = (
        "claim-support-corpus-audit/v1"
    )
    run_id: str
    findings: tuple[ClaimCorpusAuditFinding, ...]
    entry_count: int = Field(ge=0)
    ready: bool
    writer_state_trusted: Literal[False] = False
    audit_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"audit_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.ready == bool(self.findings):
            raise ValueError("audit readiness must be the inverse of findings")
        if self.audit_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("corpus audit hash does not match")
        return self


def audit_claim_corpus_run(store_root: Path, run_id: str) -> ClaimCorpusAuditReceipt:
    """Reconcile only persisted bytes; no writer or mutable state is consulted."""

    findings: list[ClaimCorpusAuditFinding] = []
    entries: list[ClaimSupportCorpusEntry] = []
    if _RUN_ID.fullmatch(run_id) is None:
        findings.append(ClaimCorpusAuditFinding(code="invalid_run_id", subject=run_id))
        return _receipt(run_id, findings, entries)

    run_root = store_root / "runs" / run_id
    loaded = _load_metadata(run_root, run_id, findings)
    if loaded is None:
        return _receipt(run_id, findings, entries)
    manifest, receipt = loaded
    if (
        receipt.run_id != run_id
        or receipt.manifest_sha256 != manifest.manifest_sha256
        or receipt.entry_count != len(manifest.entries)
    ):
        findings.append(ClaimCorpusAuditFinding(code="identity_mismatch", subject=run_id))

    actual_paths = _discover_object_paths(run_root)
    entries.extend(_audit_expected_objects(manifest, actual_paths, findings))
    _audit_unexpected_files(run_root, manifest, actual_paths, findings)
    _audit_entries(entries, findings)
    return _receipt(run_id, findings, entries)


def _load_metadata(
    run_root: Path,
    run_id: str,
    findings: list[ClaimCorpusAuditFinding],
) -> tuple[ClaimCorpusManifest, ClaimCorpusStoreReceipt] | None:
    manifest_path = run_root / "manifest.json"
    receipt_path = run_root / "receipt.json"
    if (
        run_root.is_symlink()
        or not run_root.is_dir()
        or not _regular(manifest_path)
        or not _regular(receipt_path)
    ):
        findings.append(ClaimCorpusAuditFinding(code="missing_metadata", subject=run_id))
        return None
    try:
        manifest = ClaimCorpusManifest.model_validate_json(manifest_path.read_bytes())
        receipt = ClaimCorpusStoreReceipt.model_validate_json(receipt_path.read_bytes())
    except (OSError, ValidationError):
        findings.append(ClaimCorpusAuditFinding(code="invalid_metadata", subject=run_id))
        return None
    return manifest, receipt


def _discover_object_paths(run_root: Path) -> dict[str, Path]:
    object_root = run_root / "objects" / "sha256"
    actual_paths: dict[str, Path] = {}
    if object_root.is_dir() and not object_root.is_symlink():
        for object_path in object_root.rglob("*"):
            if not object_path.is_file() or object_path.is_symlink():
                continue
            relative = object_path.relative_to(object_root)
            if (
                len(relative.parts) == 2
                and len(relative.parts[0]) == 2
                and object_path.suffix == ".json"
            ):
                actual_paths[relative.parts[0] + object_path.stem] = object_path
    return actual_paths


def _audit_expected_objects(
    manifest: ClaimCorpusManifest,
    actual_paths: dict[str, Path],
    findings: list[ClaimCorpusAuditFinding],
) -> list[ClaimSupportCorpusEntry]:
    entries: list[ClaimSupportCorpusEntry] = []
    pointers = {item.object_sha256: item.entry_sha256 for item in manifest.entries}
    for digest in manifest.object_sha256s:
        candidate_object = actual_paths.get(digest)
        if candidate_object is None:
            findings.append(ClaimCorpusAuditFinding(code="missing_object", subject=digest))
            continue
        try:
            payload = candidate_object.read_bytes()
            if content_sha256(payload) != digest:
                raise ValueError("content hash differs")
            entry = ClaimSupportCorpusEntry.model_validate_json(payload)
        except (OSError, ValueError, ValidationError):
            findings.append(ClaimCorpusAuditFinding(code="corrupt_object", subject=digest))
            continue
        if pointers.get(digest) != entry.entry_sha256:
            findings.append(ClaimCorpusAuditFinding(code="identity_mismatch", subject=digest))
        entries.append(entry)
    return entries


def _audit_unexpected_files(
    run_root: Path,
    manifest: ClaimCorpusManifest,
    actual_paths: dict[str, Path],
    findings: list[ClaimCorpusAuditFinding],
) -> None:
    for digest in sorted(set(actual_paths) - set(manifest.object_sha256s)):
        findings.append(ClaimCorpusAuditFinding(code="unexpected_file", subject=digest))
    permitted_files = {
        run_root / "manifest.json",
        run_root / "receipt.json",
        *actual_paths.values(),
    }
    if run_root.is_dir():
        for path in run_root.rglob("*"):
            if path.is_file() and path not in permitted_files:
                findings.append(
                    ClaimCorpusAuditFinding(
                        code="unexpected_file",
                        subject=str(path.relative_to(run_root)),
                    )
                )


def _audit_entries(
    entries: list[ClaimSupportCorpusEntry],
    findings: list[ClaimCorpusAuditFinding],
) -> None:
    identities: set[str] = set()
    source_claims: set[tuple[str, str]] = set()
    source_outputs: dict[str, str] = {}
    for entry in entries:
        if entry.entry_sha256 in identities:
            findings.append(
                ClaimCorpusAuditFinding(code="duplicate_entry", subject=entry.entry_sha256)
            )
        identities.add(entry.entry_sha256)
        source_claim = (entry.source_record_sha256, entry.claim_local_id)
        if source_claim in source_claims:
            findings.append(
                ClaimCorpusAuditFinding(
                    code="duplicate_source_claim",
                    subject=f"{source_claim[0]}:{source_claim[1]}",
                )
            )
        source_claims.add(source_claim)
        previous = source_outputs.setdefault(entry.source_record_sha256, entry.output_sha256)
        if previous != entry.output_sha256:
            findings.append(
                ClaimCorpusAuditFinding(
                    code="cross_source_binding", subject=entry.source_record_sha256
                )
            )
        if (
            entry.hidden_ground_truth_present
            or entry.human_judgment_present
            or entry.main_outcome_present
        ):
            findings.append(
                ClaimCorpusAuditFinding(code="visibility_leakage", subject=entry.entry_sha256)
            )


def _receipt(
    run_id: str,
    findings: list[ClaimCorpusAuditFinding],
    entries: list[ClaimSupportCorpusEntry],
) -> ClaimCorpusAuditReceipt:
    ordered = tuple(sorted(set(findings), key=lambda item: (item.code, item.subject)))
    payload = {
        "schema_version": "claim-support-corpus-audit/v1",
        "run_id": run_id,
        "findings": tuple(item.model_dump(mode="json") for item in ordered),
        "entry_count": len(entries),
        "ready": not ordered,
        "writer_state_trusted": False,
    }
    return ClaimCorpusAuditReceipt.model_validate(
        {**payload, "audit_sha256": canonical_execution_sha256(payload)}
    )


def _regular(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


__all__ = [
    "ClaimCorpusAuditFinding",
    "ClaimCorpusAuditReceipt",
    "audit_claim_corpus_run",
]
