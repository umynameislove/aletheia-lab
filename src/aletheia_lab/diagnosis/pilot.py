"""Executable, immutable matched-pilot runner and validator."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal

from pydantic import ValidationError

from aletheia_lab.diagnosis.adapters import (
    AdapterError,
    DeterministicMockAdapter,
    DiagnosisAdapter,
)
from aletheia_lab.diagnosis.prompts import (
    PROMPT_VERSION,
    RESPONSE_FORMAT,
    render_evidence_for,
    rendering_version_for,
    system_prompt_for,
)
from aletheia_lab.diagnosis.schema import (
    ATTEMPT_SCHEMA_VERSION,
    PILOT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    AttemptRecord,
    DiagnosisRequest,
    DiagnosisRunRecord,
    GenerationSettings,
    PilotManifest,
    PilotRunEntry,
    PilotVariant,
    ProviderIdentity,
    parse_diagnosis_output,
)
from aletheia_lab.evidence.schema import DiagnosisEvidenceView, project_diagnosis_evidence
from aletheia_lab.evidence.store import load_bundle_store

P1_VARIANTS: Final[tuple[PilotVariant, ...]] = (
    PilotVariant.B1_PLAIN,
    PilotVariant.A3_EVIDENCE_CONTRACT,
)
DEFAULT_SETTINGS: Final[GenerationSettings] = GenerationSettings(
    temperature=0.0,
    top_p=1.0,
    max_output_tokens=600,
    seed=17,
    timeout_seconds=60.0,
    max_attempts=2,
)


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    if not value or value != value.strip() or "\\" in value or ":" in value:
        raise ValueError("pilot paths must be non-empty canonical POSIX paths")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("pilot paths must be normalized, relative and non-traversing")
    return value


def _write_bytes(root: Path, relative_path: str, payload: bytes) -> str:
    path = root / _safe_relative_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return _sha256_bytes(payload)


def build_matched_requests(
    diagnosis_views: tuple[DiagnosisEvidenceView, ...],
    *,
    provider_identity: ProviderIdentity,
    settings: GenerationSettings = DEFAULT_SETTINGS,
) -> tuple[DiagnosisRequest, ...]:
    """Build and verify the complete two-variant request matrix."""

    requests: list[DiagnosisRequest] = []
    for view in diagnosis_views:
        if not isinstance(view, DiagnosisEvidenceView):
            raise TypeError("matched requests require DiagnosisEvidenceView objects")
        for variant in P1_VARIANTS:
            requests.append(
                DiagnosisRequest.build(
                    variant=variant,
                    provider_identity=provider_identity,
                    settings=settings,
                    prompt_version=PROMPT_VERSION,
                    rendering_version=rendering_version_for(variant),
                    system_prompt=system_prompt_for(variant),
                    response_format=RESPONSE_FORMAT,
                    diagnosis_view=view,
                    rendered_evidence=render_evidence_for(variant, view),
                )
            )
    result = tuple(sorted(requests, key=lambda item: item.request_id))
    validate_matched_requests(result)
    return result


def validate_matched_requests(requests: tuple[DiagnosisRequest, ...]) -> None:
    """Prove that only the frozen instruction intervention differs by variant."""

    if not requests:
        raise ValueError("matched request set must not be empty")
    by_context: dict[str, list[DiagnosisRequest]] = defaultdict(list)
    for request in requests:
        by_context[request.diagnosis_view.diagnosis_context_id].append(request)
    expected_variants = set(P1_VARIANTS)
    for context_id, siblings in by_context.items():
        if (
            len(siblings) != len(P1_VARIANTS)
            or {item.variant for item in siblings} != expected_variants
        ):
            raise ValueError(f"context {context_id} does not contain the frozen variant matrix")
        first = siblings[0]
        for sibling in siblings[1:]:
            if sibling.diagnosis_view != first.diagnosis_view:
                raise ValueError(f"context {context_id} variants do not receive identical facts")
            if sibling.facts_sha256 != first.facts_sha256:
                raise ValueError(f"context {context_id} variants have different facts hashes")
            if sibling.provider_identity != first.provider_identity:
                raise ValueError(f"context {context_id} silently changes provider/model identity")
            if sibling.settings != first.settings:
                raise ValueError(f"context {context_id} variants have different generation budgets")
            if sibling.response_format != first.response_format:
                raise ValueError(f"context {context_id} variants have different output contracts")
        for sibling in siblings:
            if sibling.prompt_version != PROMPT_VERSION:
                raise ValueError(f"context {context_id} contains an unknown prompt version")
            if sibling.system_prompt != system_prompt_for(sibling.variant):
                raise ValueError(f"context {context_id} contains a non-canonical system prompt")
            if sibling.response_format != RESPONSE_FORMAT:
                raise ValueError(f"context {context_id} contains a non-canonical output contract")
            expected_rendering = render_evidence_for(sibling.variant, sibling.diagnosis_view)
            if sibling.rendered_evidence != expected_rendering:
                raise ValueError(
                    f"context {context_id} contains a non-canonical evidence rendering"
                )
            if sibling.rendering_version != rendering_version_for(sibling.variant):
                raise ValueError(f"context {context_id} contains an unknown evidence renderer")
        if len({item.prompt_sha256 for item in siblings}) != len(P1_VARIANTS):
            raise ValueError(f"context {context_id} does not preserve distinct interventions")


def validate_source_binding(
    requests: tuple[DiagnosisRequest, ...],
    source_views: tuple[DiagnosisEvidenceView, ...],
) -> None:
    """Reject a fully rehashed request matrix that no longer matches source evidence."""

    canonical = {view.diagnosis_context_id: view for view in source_views}
    if len(canonical) != len(source_views):
        raise ValueError("source evidence contains duplicate diagnosis contexts")
    request_contexts = {request.diagnosis_view.diagnosis_context_id for request in requests}
    if request_contexts != set(canonical):
        raise ValueError("pilot request contexts differ from the source evidence store")
    for request in requests:
        source_view = canonical[request.diagnosis_view.diagnosis_context_id]
        if request.diagnosis_view != source_view:
            raise ValueError("pilot diagnosis view differs from its source evidence projection")


def _execute_request(
    request: DiagnosisRequest,
    adapter: DiagnosisAdapter,
    stage: Path,
) -> DiagnosisRunRecord:
    attempts: list[AttemptRecord] = []
    visible_ids = {item.evidence_id for item in request.diagnosis_view.items}
    for attempt_index in range(1, request.settings.max_attempts + 1):
        try:
            response = adapter.complete(request)
        except AdapterError as exc:
            attempts.append(
                AttemptRecord(
                    schema_version=ATTEMPT_SCHEMA_VERSION,
                    attempt_index=attempt_index,
                    status="adapter_error",
                    error_type=exc.error_type,
                    error_message=str(exc),
                )
            )
            continue

        raw_path = f"raw/{request.request_id}/attempt-{attempt_index}.txt"
        raw_payload = response.raw_text.encode("utf-8")
        # Ordering is a trust boundary: preserve the exact raw bytes before any
        # identity check or parser can reject/transform the response.
        raw_sha = _write_bytes(stage, raw_path, raw_payload)

        if response.provider_identity != request.provider_identity:
            attempts.append(
                AttemptRecord(
                    schema_version=ATTEMPT_SCHEMA_VERSION,
                    attempt_index=attempt_index,
                    status="identity_mismatch",
                    response_id=response.response_id,
                    provider_identity=response.provider_identity,
                    raw_relative_path=raw_path,
                    raw_sha256=raw_sha,
                    usage=response.usage,
                    latency_ms=response.latency_ms,
                    error_type="provider_identity_mismatch",
                    error_message="response provider/model/version differs from the frozen request",
                )
            )
            continue

        try:
            parsed = parse_diagnosis_output(response.raw_text, visible_ids)
        except (ValueError, ValidationError) as exc:
            attempts.append(
                AttemptRecord(
                    schema_version=ATTEMPT_SCHEMA_VERSION,
                    attempt_index=attempt_index,
                    status="parse_failure",
                    response_id=response.response_id,
                    provider_identity=response.provider_identity,
                    raw_relative_path=raw_path,
                    raw_sha256=raw_sha,
                    usage=response.usage,
                    latency_ms=response.latency_ms,
                    error_type="output_parse_failure",
                    error_message=str(exc),
                )
            )
            continue

        parsed_path = f"parsed/{request.request_id}/attempt-{attempt_index}.json"
        parsed_sha = _write_bytes(stage, parsed_path, _json_bytes(parsed.model_dump(mode="json")))
        attempts.append(
            AttemptRecord(
                schema_version=ATTEMPT_SCHEMA_VERSION,
                attempt_index=attempt_index,
                status="success",
                response_id=response.response_id,
                provider_identity=response.provider_identity,
                raw_relative_path=raw_path,
                raw_sha256=raw_sha,
                parsed_relative_path=parsed_path,
                parsed_sha256=parsed_sha,
                usage=response.usage,
                latency_ms=response.latency_ms,
            )
        )
        break

    final_status: Literal["success", "unresolved"] = (
        "success" if attempts[-1].status == "success" else "unresolved"
    )
    return DiagnosisRunRecord(
        schema_version=RUN_SCHEMA_VERSION,
        request=request,
        attempts=tuple(attempts),
        final_status=final_status,
    )


def run_p1_matched_pilot(
    evidence_store_dir: str | Path,
    output_dir: str | Path,
    *,
    adapter: DiagnosisAdapter,
    settings: GenerationSettings = DEFAULT_SETTINGS,
) -> PilotManifest:
    """Run all 15 contexts x two variants and persist an immutable pilot store."""

    evidence_store = load_bundle_store(evidence_store_dir)
    if len(evidence_store.bundles) != 15:
        raise ValueError("P1 matched pilot requires the canonical 15-bundle evidence store")
    if adapter.identity != DeterministicMockAdapter().identity:
        raise ValueError(
            "the offline runner only authorizes the deterministic mock"
        )
    views = tuple(project_diagnosis_evidence(bundle) for bundle in evidence_store.bundles)
    if len({view.diagnosis_context_id for view in views}) != 15:
        raise ValueError("P1 evidence store does not contain 15 unique diagnosis contexts")
    requests = build_matched_requests(
        views,
        provider_identity=adapter.identity,
        settings=settings,
    )
    if settings != DEFAULT_SETTINGS:
        raise ValueError("settings differ from the frozen mock-pilot contract")
    validate_source_binding(requests, views)

    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to replace an existing pilot store: {output}")
    stage = Path(tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}.stage-"))
    try:
        entries: list[PilotRunEntry] = []
        for request in requests:
            record = _execute_request(request, adapter, stage)
            relative_path = f"runs/{request.request_id}.json"
            record_sha = _write_bytes(
                stage, relative_path, _json_bytes(record.model_dump(mode="json"))
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
        successes = sum(entry.final_status == "success" for entry in entries)
        manifest = PilotManifest(
            schema_version=PILOT_SCHEMA_VERSION,
            source_evidence_store_sha256=evidence_store.manifest.store_sha256,
            provider_identity=adapter.identity,
            settings=settings,
            context_count=15,
            variant_count=len(P1_VARIANTS),
            run_count=len(entries),
            success_count=successes,
            unresolved_count=len(entries) - successes,
            entries=tuple(sorted(entries, key=lambda item: item.request_id)),
        )
        _write_bytes(stage, "pilot-manifest.json", _json_bytes(manifest.model_dump(mode="json")))
        os.replace(stage, output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    validate_p1_matched_pilot(output, evidence_store_dir)
    return manifest


def _confined_file(root: Path, relative_path: str) -> Path:
    path = root / _safe_relative_path(relative_path)
    if path.is_symlink():
        raise ValueError(f"pilot artifact must not be a symlink: {relative_path}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"pilot artifact escapes its store: {relative_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"pilot artifact missing: {relative_path}")
    return path


def validate_p1_matched_pilot(
    output_dir: str | Path,
    evidence_store_dir: str | Path,
) -> PilotManifest:
    """Recompute source binding, matchedness and every persisted artifact hash."""

    root = Path(output_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"pilot store is not a real directory: {root}")
    evidence_store = load_bundle_store(evidence_store_dir)
    manifest_path = _confined_file(root, "pilot-manifest.json")
    manifest = PilotManifest.model_validate_json(manifest_path.read_text("utf-8"))
    if manifest.source_evidence_store_sha256 != evidence_store.manifest.store_sha256:
        raise ValueError("pilot is not bound to the supplied evidence store")
    if manifest.provider_identity != DeterministicMockAdapter().identity:
        raise ValueError("manifest changes the frozen mock provider identity")
    if manifest.settings != DEFAULT_SETTINGS:
        raise ValueError("manifest changes the frozen generation settings")

    expected_paths = {"pilot-manifest.json"}
    records: list[DiagnosisRunRecord] = []
    for entry in manifest.entries:
        run_path = _confined_file(root, entry.relative_path)
        expected_paths.add(entry.relative_path)
        run_payload = run_path.read_bytes()
        if _sha256_bytes(run_payload) != entry.file_sha256:
            raise ValueError(f"run file hash mismatch: {entry.relative_path}")
        record = DiagnosisRunRecord.model_validate_json(run_payload)
        if (
            record.request.request_id != entry.request_id
            or record.request.diagnosis_view.diagnosis_context_id != entry.diagnosis_context_id
            or record.request.variant != entry.variant
            or record.final_status != entry.final_status
        ):
            raise ValueError(f"run identity differs from manifest: {entry.relative_path}")
        if record.request.provider_identity != manifest.provider_identity:
            raise ValueError("run silently changes the frozen provider/model identity")
        if record.request.settings != manifest.settings:
            raise ValueError("run silently changes the frozen generation budget")
        visible_ids = {item.evidence_id for item in record.request.diagnosis_view.items}
        for attempt in record.attempts:
            if attempt.status in {"success", "parse_failure"} and (
                attempt.provider_identity != record.request.provider_identity
            ):
                raise ValueError("accepted attempt silently changes provider/model identity")
            if attempt.status == "identity_mismatch" and (
                attempt.provider_identity == record.request.provider_identity
            ):
                raise ValueError("identity-mismatch attempt does not contain a mismatch")
            raw_text: str | None = None
            if attempt.raw_relative_path is not None:
                raw_path = _confined_file(root, attempt.raw_relative_path)
                expected_paths.add(attempt.raw_relative_path)
                raw_payload = raw_path.read_bytes()
                if _sha256_bytes(raw_payload) != attempt.raw_sha256:
                    raise ValueError(f"raw response hash mismatch: {attempt.raw_relative_path}")
                raw_text = raw_payload.decode("utf-8")
            if attempt.status == "parse_failure" and raw_text is not None:
                try:
                    parse_diagnosis_output(raw_text, visible_ids)
                except (ValueError, ValidationError):
                    pass
                else:
                    raise ValueError("attempt is labeled parse_failure but raw output is valid")
            if attempt.parsed_relative_path is not None:
                parsed_path = _confined_file(root, attempt.parsed_relative_path)
                expected_paths.add(attempt.parsed_relative_path)
                parsed_payload = parsed_path.read_bytes()
                if _sha256_bytes(parsed_payload) != attempt.parsed_sha256:
                    raise ValueError(f"parsed output hash mismatch: {attempt.parsed_relative_path}")
                parsed = parse_diagnosis_output(parsed_payload.decode("utf-8"), visible_ids)
                if attempt.raw_relative_path is None:
                    raise ValueError("parsed output has no preserved raw response")
                if raw_text is None:
                    raise ValueError("parsed output raw response could not be loaded")
                if parsed != parse_diagnosis_output(raw_text, visible_ids):
                    raise ValueError("parsed output does not match its raw response")
        records.append(record)

    requests = tuple(record.request for record in records)
    validate_matched_requests(requests)
    source_views = tuple(project_diagnosis_evidence(bundle) for bundle in evidence_store.bundles)
    validate_source_binding(requests, source_views)
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"pilot store contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise ValueError(
            "pilot store file set differs from manifest: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"unexpected={sorted(actual_paths - expected_paths)}"
        )
    return manifest
