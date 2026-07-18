"""Fail-closed execution of the frozen eight-request OpenAI smoke plan."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.diagnosis.adapters import DiagnosisAdapter
from aletheia_lab.diagnosis.openai_preflight import (
    OpenAIPilotConfig,
    OpenAIPreflightReport,
    build_openai_preflight,
    load_openai_preflight,
    openai_preflight_sha256,
)
from aletheia_lab.diagnosis.pilot import (
    build_matched_requests,
    execute_diagnosis_request,
    validate_matched_requests,
    validate_source_binding,
)
from aletheia_lab.diagnosis.schema import (
    PILOT_SCHEMA_VERSION,
    DiagnosisRunRecord,
    PilotManifest,
    PilotRunEntry,
    parse_diagnosis_output,
)
from aletheia_lab.evidence.schema import project_diagnosis_evidence
from aletheia_lab.evidence.store import load_bundle_store

AUTHORIZATION_SCHEMA_VERSION: Final[Literal["external-smoke-authorization/1"]] = (
    "external-smoke-authorization/1"
)
SMOKE_REQUEST_COUNT: Final[int] = 8
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExternalSmokeAuthorization(_StrictFrozenModel):
    """Immutable proof that execution was bound to one exact preflight."""

    schema_version: Literal["external-smoke-authorization/1"]
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_evidence_store_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    outbound_payload_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    smoke_request_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _exact_smoke_set(self) -> Self:
        if len(self.smoke_request_ids) != SMOKE_REQUEST_COUNT:
            raise ValueError("authorization must bind exactly eight requests")
        if len(set(self.smoke_request_ids)) != SMOKE_REQUEST_COUNT:
            raise ValueError("authorization contains duplicate request IDs")
        return self


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    if not value or value != value.strip() or "\\" in value or ":" in value:
        raise ValueError("smoke paths must be canonical relative POSIX paths")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("smoke path is absolute, non-canonical or traverses parents")
    return value


def _write_bytes(root: Path, relative_path: str, payload: bytes) -> str:
    path = root / _safe_relative_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return _sha256_bytes(payload)


def _confined_file(root: Path, relative_path: str) -> Path:
    path = root / _safe_relative_path(relative_path)
    if path.is_symlink():
        raise ValueError(f"smoke artifact must not be a symlink: {relative_path}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"smoke artifact escapes output root: {relative_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"smoke artifact missing: {relative_path}")
    return path


def authorize_openai_smoke(
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    persisted_preflight: OpenAIPreflightReport,
    confirmed_preflight_sha256: str,
) -> tuple[OpenAIPreflightReport, ExternalSmokeAuthorization]:
    recomputed = build_openai_preflight(evidence_store_dir, config)
    if persisted_preflight != recomputed:
        raise ValueError("persisted preflight differs from the independently recomputed plan")
    if not recomputed.passed:
        raise ValueError("external execution requires a passing preflight")
    digest = openai_preflight_sha256(recomputed)
    if confirmed_preflight_sha256 != digest:
        raise ValueError("human confirmation does not match the exact preflight SHA-256")
    authorization = ExternalSmokeAuthorization(
        schema_version=AUTHORIZATION_SCHEMA_VERSION,
        preflight_sha256=digest,
        source_evidence_store_sha256=recomputed.source_evidence_store_sha256,
        config_sha256=recomputed.config_sha256,
        request_set_sha256=recomputed.request_set_sha256,
        outbound_payload_set_sha256=recomputed.outbound_payload_set_sha256,
        smoke_request_ids=recomputed.smoke_request_ids,
    )
    return recomputed, authorization


def run_openai_smoke(
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    preflight_path: str | Path,
    output_dir: str | Path,
    *,
    confirmed_preflight_sha256: str,
    adapter: DiagnosisAdapter,
) -> PilotManifest:
    """Execute only the eight preflight-bound requests and preserve every attempt.

    The output directory is created before the first provider call. If execution
    is interrupted it intentionally remains incomplete, retaining any raw
    provider artifacts already received; an incomplete directory never validates.
    """

    persisted = load_openai_preflight(preflight_path)
    report, authorization = authorize_openai_smoke(
        evidence_store_dir, config, persisted, confirmed_preflight_sha256
    )
    if adapter.identity != config.provider_identity:
        raise ValueError("adapter identity differs from the frozen preflight identity")

    store = load_bundle_store(evidence_store_dir)
    all_views = tuple(project_diagnosis_evidence(bundle) for bundle in store.bundles)
    all_requests = build_matched_requests(
        all_views, provider_identity=config.provider_identity, settings=config.settings
    )
    by_id = {request.request_id: request for request in all_requests}
    if set(report.smoke_request_ids) - set(by_id):
        raise ValueError("preflight smoke plan references an unknown request")
    requests = tuple(by_id[request_id] for request_id in report.smoke_request_ids)
    validate_matched_requests(requests)
    context_ids = {request.diagnosis_view.diagnosis_context_id for request in requests}
    selected_views = tuple(view for view in all_views if view.diagnosis_context_id in context_ids)
    if len(selected_views) != 4:
        raise ValueError("smoke plan must contain four matched diagnosis contexts")
    validate_source_binding(requests, selected_views)

    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    _write_bytes(
        output,
        "execution-authorization.json",
        _json_bytes(authorization.model_dump(mode="json")),
    )

    entries: list[PilotRunEntry] = []
    for request in requests:
        record = execute_diagnosis_request(request, adapter, output)
        relative_path = f"runs/{request.request_id}.json"
        record_sha = _write_bytes(
            output, relative_path, _json_bytes(record.model_dump(mode="json"))
        )
        entries.append(
            PilotRunEntry(
                request_id=request.request_id,
                diagnosis_context_id=request.diagnosis_view.diagnosis_context_id,
                variant=request.variant,
                final_status=record.final_status,
                relative_path=relative_path,
                file_sha256=record_sha,
            )
        )

    success_count = sum(entry.final_status == "success" for entry in entries)
    manifest = PilotManifest(
        schema_version=PILOT_SCHEMA_VERSION,
        source_evidence_store_sha256=store.manifest.store_sha256,
        provider_identity=config.provider_identity,
        settings=config.settings,
        context_count=4,
        variant_count=2,
        run_count=SMOKE_REQUEST_COUNT,
        success_count=success_count,
        unresolved_count=SMOKE_REQUEST_COUNT - success_count,
        entries=tuple(sorted(entries, key=lambda item: item.request_id)),
    )
    _write_bytes(output, "pilot-manifest.json", _json_bytes(manifest.model_dump(mode="json")))
    validate_openai_smoke(output, evidence_store_dir, config, preflight_path)
    return manifest


def validate_openai_smoke(
    output_dir: str | Path,
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    preflight_path: str | Path,
) -> PilotManifest:
    """Recompute authorization, source binding and every external artifact hash."""

    root = Path(output_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"smoke output is not a real directory: {root}")
    report = load_openai_preflight(preflight_path)
    recomputed = build_openai_preflight(evidence_store_dir, config)
    if report != recomputed:
        raise ValueError("preflight artifact no longer matches the recomputed plan")
    authorization = ExternalSmokeAuthorization.model_validate_json(
        _confined_file(root, "execution-authorization.json").read_text("utf-8")
    )
    expected_authorization = ExternalSmokeAuthorization(
        schema_version=AUTHORIZATION_SCHEMA_VERSION,
        preflight_sha256=openai_preflight_sha256(report),
        source_evidence_store_sha256=report.source_evidence_store_sha256,
        config_sha256=report.config_sha256,
        request_set_sha256=report.request_set_sha256,
        outbound_payload_set_sha256=report.outbound_payload_set_sha256,
        smoke_request_ids=report.smoke_request_ids,
    )
    if authorization != expected_authorization:
        raise ValueError("execution authorization differs from the frozen preflight")

    manifest = PilotManifest.model_validate_json(
        _confined_file(root, "pilot-manifest.json").read_text("utf-8")
    )
    store = load_bundle_store(evidence_store_dir)
    if manifest.source_evidence_store_sha256 != store.manifest.store_sha256:
        raise ValueError("smoke output is not bound to the supplied evidence store")
    if manifest.provider_identity != config.provider_identity or manifest.settings != config.settings:
        raise ValueError("smoke output changes the frozen provider or generation settings")
    if (manifest.context_count, manifest.variant_count, manifest.run_count) != (4, 2, 8):
        raise ValueError("smoke manifest does not preserve the frozen 4x2 census")
    if {entry.request_id for entry in manifest.entries} != set(report.smoke_request_ids):
        raise ValueError("smoke manifest request set differs from preflight")

    expected_paths = {"execution-authorization.json", "pilot-manifest.json"}
    records: list[DiagnosisRunRecord] = []
    for entry in manifest.entries:
        run_path = _confined_file(root, entry.relative_path)
        expected_paths.add(entry.relative_path)
        payload = run_path.read_bytes()
        if _sha256_bytes(payload) != entry.file_sha256:
            raise ValueError(f"run file hash mismatch: {entry.relative_path}")
        record = DiagnosisRunRecord.model_validate_json(payload)
        if (
            record.request.request_id != entry.request_id
            or record.request.diagnosis_view.diagnosis_context_id != entry.diagnosis_context_id
            or record.request.variant != entry.variant
            or record.final_status != entry.final_status
        ):
            raise ValueError(f"run identity differs from manifest: {entry.relative_path}")
        if record.request.provider_identity != config.provider_identity:
            raise ValueError("run changes the frozen provider identity")
        if record.request.settings != config.settings:
            raise ValueError("run changes the frozen settings")
        visible = {item.evidence_id for item in record.request.diagnosis_view.items}
        for attempt in record.attempts:
            if attempt.status in {"success", "parse_failure"} and (
                attempt.provider_identity != record.request.provider_identity
            ):
                raise ValueError("accepted attempt changes provider/model identity")
            if attempt.status == "identity_mismatch" and (
                attempt.provider_identity == record.request.provider_identity
            ):
                raise ValueError("identity-mismatch attempt does not contain a mismatch")
            raw_text: str | None = None
            if attempt.raw_relative_path is not None:
                raw_path = _confined_file(root, attempt.raw_relative_path)
                expected_paths.add(attempt.raw_relative_path)
                raw = raw_path.read_bytes()
                if _sha256_bytes(raw) != attempt.raw_sha256:
                    raise ValueError(f"raw response hash mismatch: {attempt.raw_relative_path}")
                raw_text = raw.decode("utf-8")
            if attempt.status == "parse_failure" and raw_text is not None:
                try:
                    parse_diagnosis_output(raw_text, visible)
                except (ValueError, ValidationError):
                    pass
                else:
                    raise ValueError("attempt is labeled parse_failure but raw output is valid")
            if attempt.parsed_relative_path is not None:
                parsed_path = _confined_file(root, attempt.parsed_relative_path)
                expected_paths.add(attempt.parsed_relative_path)
                parsed_payload = parsed_path.read_bytes()
                if _sha256_bytes(parsed_payload) != attempt.parsed_sha256:
                    raise ValueError(
                        f"parsed output hash mismatch: {attempt.parsed_relative_path}"
                    )
                parsed = parse_diagnosis_output(parsed_payload.decode("utf-8"), visible)
                if attempt.raw_relative_path is None:
                    raise ValueError("parsed output lacks its raw provider response")
                raw_text = _confined_file(root, attempt.raw_relative_path).read_text("utf-8")
                if parsed != parse_diagnosis_output(raw_text, visible):
                    raise ValueError("parsed output does not match its raw response")
        records.append(record)

    requests = tuple(record.request for record in records)
    validate_matched_requests(requests)
    views = tuple(
        project_diagnosis_evidence(bundle)
        for bundle in store.bundles
        if bundle.diagnosis_context_id in {entry.diagnosis_context_id for entry in manifest.entries}
    )
    validate_source_binding(requests, views)
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("smoke output contains a symlink")
    if actual_paths != expected_paths:
        raise ValueError(
            "smoke output file set differs from manifest: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"unexpected={sorted(actual_paths - expected_paths)}"
        )
    return manifest
