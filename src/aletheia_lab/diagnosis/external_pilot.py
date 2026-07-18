"""Fail-closed execution of frozen OpenAI smoke and full matched plans."""

from __future__ import annotations

import hashlib
import json
import math
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
    preflight_matches_recomputed,
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
FULL_AUTHORIZATION_SCHEMA_VERSION: Final[
    Literal["external-full-authorization/1"]
] = "external-full-authorization/1"
SMOKE_REQUEST_COUNT: Final[int] = 8
FULL_REQUEST_COUNT: Final[int] = 30
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


class ExternalFullAuthorization(_StrictFrozenModel):
    """Immutable proof of explicit approval for the complete 30-request run."""

    schema_version: Literal["external-full-authorization/1"]
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_evidence_store_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    outbound_payload_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    confirmed_estimated_full_retry_ceiling_usd: float = Field(
        ge=0.0, allow_inf_nan=False
    )
    full_request_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _exact_full_set(self) -> Self:
        if len(self.full_request_ids) != FULL_REQUEST_COUNT:
            raise ValueError("full authorization must bind exactly 30 requests")
        if len(set(self.full_request_ids)) != FULL_REQUEST_COUNT:
            raise ValueError("full authorization contains duplicate request IDs")
        return self


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    if not value or value != value.strip() or "\\" in value or ":" in value:
        raise ValueError("external paths must be canonical relative POSIX paths")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("external path is absolute, non-canonical or traverses parents")
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
        raise ValueError(f"external artifact must not be a symlink: {relative_path}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"external artifact escapes output root: {relative_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"external artifact missing: {relative_path}")
    return path


def authorize_openai_smoke(
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    persisted_preflight: OpenAIPreflightReport,
    confirmed_preflight_sha256: str,
) -> tuple[OpenAIPreflightReport, ExternalSmokeAuthorization]:
    recomputed = build_openai_preflight(evidence_store_dir, config)
    if not preflight_matches_recomputed(persisted_preflight, recomputed):
        raise ValueError("persisted preflight differs from the independently recomputed plan")
    if not recomputed.passed:
        raise ValueError("external execution requires a passing preflight")
    # Confirmation binds the exact artifact the human inspected. A genuine
    # legacy artifact may omit only the additive cost block while matching the
    # independently recomputed execution plan.
    digest = openai_preflight_sha256(persisted_preflight)
    if confirmed_preflight_sha256 != digest:
        raise ValueError("human confirmation does not match the exact preflight SHA-256")
    authorization = ExternalSmokeAuthorization(
        schema_version=AUTHORIZATION_SCHEMA_VERSION,
        preflight_sha256=digest,
        source_evidence_store_sha256=persisted_preflight.source_evidence_store_sha256,
        config_sha256=persisted_preflight.config_sha256,
        request_set_sha256=persisted_preflight.request_set_sha256,
        outbound_payload_set_sha256=persisted_preflight.outbound_payload_set_sha256,
        smoke_request_ids=persisted_preflight.smoke_request_ids,
    )
    return persisted_preflight, authorization


def authorize_openai_full(
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    persisted_preflight: OpenAIPreflightReport,
    confirmed_preflight_sha256: str,
    confirmed_estimated_full_retry_ceiling_usd: float,
) -> tuple[OpenAIPreflightReport, ExternalFullAuthorization]:
    """Authorize all 30 requests only after exact plan and cost confirmation."""

    recomputed = build_openai_preflight(evidence_store_dir, config)
    if not preflight_matches_recomputed(persisted_preflight, recomputed):
        raise ValueError("persisted preflight differs from the independently recomputed plan")
    if not recomputed.passed:
        raise ValueError("external execution requires a passing preflight")
    if persisted_preflight.cost_estimates is None:
        raise ValueError("full execution requires an explicit four-budget preflight")
    digest = openai_preflight_sha256(persisted_preflight)
    if confirmed_preflight_sha256 != digest:
        raise ValueError("human confirmation does not match the exact preflight SHA-256")
    expected_ceiling = (
        persisted_preflight.cost_estimates.full_retry_ceiling.estimated_cost_usd
    )
    if not math.isfinite(confirmed_estimated_full_retry_ceiling_usd) or abs(
        confirmed_estimated_full_retry_ceiling_usd - expected_ceiling
    ) > 1e-12:
        raise ValueError("human cost confirmation does not match the full retry ceiling")

    store = load_bundle_store(evidence_store_dir)
    views = tuple(project_diagnosis_evidence(bundle) for bundle in store.bundles)
    requests = build_matched_requests(
        views, provider_identity=config.provider_identity, settings=config.settings
    )
    validate_matched_requests(requests)
    validate_source_binding(requests, views)
    if len(views) != 15 or len(requests) != FULL_REQUEST_COUNT:
        raise ValueError("full execution requires the canonical 15-context/30-request plan")
    authorization = ExternalFullAuthorization(
        schema_version=FULL_AUTHORIZATION_SCHEMA_VERSION,
        preflight_sha256=digest,
        source_evidence_store_sha256=persisted_preflight.source_evidence_store_sha256,
        config_sha256=persisted_preflight.config_sha256,
        request_set_sha256=persisted_preflight.request_set_sha256,
        outbound_payload_set_sha256=persisted_preflight.outbound_payload_set_sha256,
        confirmed_estimated_full_retry_ceiling_usd=expected_ceiling,
        full_request_ids=tuple(request.request_id for request in requests),
    )
    return persisted_preflight, authorization


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


def run_openai_full(
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    preflight_path: str | Path,
    output_dir: str | Path,
    *,
    confirmed_preflight_sha256: str,
    confirmed_estimated_full_retry_ceiling_usd: float,
    adapter: DiagnosisAdapter,
) -> PilotManifest:
    """Execute the exact 15-context x 2-variant plan after dual confirmation.

    As with smoke execution, the immutable output directory is created before
    the first provider call and an interrupted directory intentionally fails
    validation while retaining every response already received.
    """

    persisted = load_openai_preflight(preflight_path)
    _, authorization = authorize_openai_full(
        evidence_store_dir,
        config,
        persisted,
        confirmed_preflight_sha256,
        confirmed_estimated_full_retry_ceiling_usd,
    )
    if adapter.identity != config.provider_identity:
        raise ValueError("adapter identity differs from the frozen preflight identity")

    store = load_bundle_store(evidence_store_dir)
    views = tuple(project_diagnosis_evidence(bundle) for bundle in store.bundles)
    requests = build_matched_requests(
        views, provider_identity=config.provider_identity, settings=config.settings
    )
    validate_matched_requests(requests)
    validate_source_binding(requests, views)
    if tuple(request.request_id for request in requests) != authorization.full_request_ids:
        raise ValueError("full execution request order differs from authorization")

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
        context_count=15,
        variant_count=2,
        run_count=FULL_REQUEST_COUNT,
        success_count=success_count,
        unresolved_count=FULL_REQUEST_COUNT - success_count,
        entries=tuple(sorted(entries, key=lambda item: item.request_id)),
    )
    _write_bytes(output, "pilot-manifest.json", _json_bytes(manifest.model_dump(mode="json")))
    validate_openai_full(output, evidence_store_dir, config, preflight_path)
    return manifest


def _validate_openai_execution(
    output_dir: str | Path,
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    preflight_path: str | Path,
    *,
    full: bool,
) -> PilotManifest:
    """Recompute authorization, source binding and every external artifact hash."""

    root = Path(output_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"external output is not a real directory: {root}")
    report = load_openai_preflight(preflight_path)
    recomputed = build_openai_preflight(evidence_store_dir, config)
    if not preflight_matches_recomputed(report, recomputed):
        raise ValueError("preflight artifact no longer matches the recomputed plan")
    store = load_bundle_store(evidence_store_dir)
    views = tuple(project_diagnosis_evidence(bundle) for bundle in store.bundles)
    all_requests = build_matched_requests(
        views, provider_identity=config.provider_identity, settings=config.settings
    )
    if full:
        if report.cost_estimates is None:
            raise ValueError("full execution requires an explicit four-budget preflight")
        full_authorization = ExternalFullAuthorization.model_validate_json(
            _confined_file(root, "execution-authorization.json").read_text("utf-8")
        )
        expected_full_authorization = ExternalFullAuthorization(
            schema_version=FULL_AUTHORIZATION_SCHEMA_VERSION,
            preflight_sha256=openai_preflight_sha256(report),
            source_evidence_store_sha256=report.source_evidence_store_sha256,
            config_sha256=report.config_sha256,
            request_set_sha256=report.request_set_sha256,
            outbound_payload_set_sha256=report.outbound_payload_set_sha256,
            confirmed_estimated_full_retry_ceiling_usd=(
                report.cost_estimates.full_retry_ceiling.estimated_cost_usd
            ),
            full_request_ids=tuple(request.request_id for request in all_requests),
        )
        if full_authorization != expected_full_authorization:
            raise ValueError("execution authorization differs from the frozen preflight")
        expected_census = (15, 2, FULL_REQUEST_COUNT)
        expected_request_ids = set(expected_full_authorization.full_request_ids)
    else:
        smoke_authorization = ExternalSmokeAuthorization.model_validate_json(
            _confined_file(root, "execution-authorization.json").read_text("utf-8")
        )
        expected_smoke_authorization = ExternalSmokeAuthorization(
            schema_version=AUTHORIZATION_SCHEMA_VERSION,
            preflight_sha256=openai_preflight_sha256(report),
            source_evidence_store_sha256=report.source_evidence_store_sha256,
            config_sha256=report.config_sha256,
            request_set_sha256=report.request_set_sha256,
            outbound_payload_set_sha256=report.outbound_payload_set_sha256,
            smoke_request_ids=report.smoke_request_ids,
        )
        if smoke_authorization != expected_smoke_authorization:
            raise ValueError("execution authorization differs from the frozen preflight")
        expected_census = (4, 2, SMOKE_REQUEST_COUNT)
        expected_request_ids = set(expected_smoke_authorization.smoke_request_ids)

    manifest = PilotManifest.model_validate_json(
        _confined_file(root, "pilot-manifest.json").read_text("utf-8")
    )
    if manifest.source_evidence_store_sha256 != store.manifest.store_sha256:
        raise ValueError("external output is not bound to the supplied evidence store")
    if manifest.provider_identity != config.provider_identity or manifest.settings != config.settings:
        raise ValueError("external output changes the frozen provider or generation settings")
    if (manifest.context_count, manifest.variant_count, manifest.run_count) != expected_census:
        raise ValueError("external manifest does not preserve its frozen census")
    if {entry.request_id for entry in manifest.entries} != expected_request_ids:
        raise ValueError("external manifest request set differs from authorization")

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
    selected_views = tuple(
        project_diagnosis_evidence(bundle)
        for bundle in store.bundles
        if bundle.diagnosis_context_id in {entry.diagnosis_context_id for entry in manifest.entries}
    )
    validate_source_binding(requests, selected_views)
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("external output contains a symlink")
    if actual_paths != expected_paths:
        raise ValueError(
            "external output file set differs from manifest: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"unexpected={sorted(actual_paths - expected_paths)}"
        )
    return manifest


def validate_openai_smoke(
    output_dir: str | Path,
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    preflight_path: str | Path,
) -> PilotManifest:
    """Validate the exact eight-request external smoke execution."""

    return _validate_openai_execution(
        output_dir, evidence_store_dir, config, preflight_path, full=False
    )


def validate_openai_full(
    output_dir: str | Path,
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
    preflight_path: str | Path,
) -> PilotManifest:
    """Validate the exact 15-context x 2-variant external execution."""

    return _validate_openai_execution(
        output_dir, evidence_store_dir, config, preflight_path, full=True
    )
