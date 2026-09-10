"""Provider-free extraction and relation-prerequisite audit for V2 outputs."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    normalize_provider_output_v2,
)
from aletheia_lab.evaluation.claim_corpus_terminal_reader import (
    ClaimCorpusTerminalReader,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort import (
    build_cohort_plan,
    load_cohort_authorization,
    load_verified_qualification,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort_contracts import (
    V2CohortReceipt,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort_execution import (
    build_v2_cohort_gateway_requests,
    verify_completed_v2_cohort,
)
from aletheia_lab.evaluation.claim_validation_v2_extraction_contracts import (
    EXTRACTION_SCHEMA_VERSION,
    MAXIMUM_SOURCE_CLAIMS_PER_OUTPUT,
    RELATION_REQUEST_CEILING,
    SAMPLE_TARGET,
    ClaimValidationV2ExtractionError,
    V2ClaimExtractionCloseout,
    V2DashboardInputUsageObservation,
    V2ExtractionRequestRecord,
)
from aletheia_lab.evaluation.claim_validation_v2_relation_frames import (
    _selected_claims,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    _load_inputs,
    build_v2_runtime_manifest,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime_contracts import (
    ClaimSupportValidationV2RuntimeManifest,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import ImmutableFileDisposition, publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_ALLOWED_EXTRACTION_FILES = {
    "closeout.json",
    "dashboard-input-usage-evidence.png",
}
_EXTRACTION_IMPLEMENTATION_PATHS = (
    "src/aletheia_lab/evaluation/claim_validation_v2_extraction.py",
    "src/aletheia_lab/evaluation/claim_validation_v2_extraction_contracts.py",
)


def checked_extraction_run_directory(root: Path, run_dir: Path) -> Path:
    """Require a private, non-linked extraction destination outside the repo."""

    repository = root.resolve()
    expanded = run_dir.expanduser()
    if expanded.is_symlink():
        raise ClaimValidationV2ExtractionError(
            "V2 extraction destination must not be a symbolic link"
        )
    destination = expanded.resolve()
    if (
        destination == repository
        or destination.is_relative_to(repository)
        or repository.is_relative_to(destination)
    ):
        raise ClaimValidationV2ExtractionError(
            "V2 extraction destination must remain outside the repository tree"
        )
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise ClaimValidationV2ExtractionError("V2 extraction destination is not a real directory")
    if destination.exists() and any(
        item.is_symlink() or item.name not in _ALLOWED_EXTRACTION_FILES
        for item in destination.iterdir()
    ):
        raise ClaimValidationV2ExtractionError(
            "V2 extraction destination contains unknown or linked artifacts"
        )
    return destination


def build_dashboard_input_usage_observation(
    *, observed_utc_date: str, input_token_count: int, evidence: bytes
) -> V2DashboardInputUsageObservation:
    """Bind a screenshot without claiming cohort-exclusive token attribution."""

    payload = {
        "scope": "openai_dashboard_utc_day_all_input_tokens",
        "observed_utc_date": observed_utc_date,
        "input_token_count": input_token_count,
        "evidence_content_sha256": content_sha256(evidence),
        "cohort_exclusive_attribution_established": False,
        "provider_output_token_count_available": False,
        "exact_realized_cost_available": False,
    }
    return V2DashboardInputUsageObservation.model_validate(
        {
            **payload,
            "observation_sha256": canonical_execution_sha256(payload),
        }
    )


def _reader(store_root: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store_root / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _implementation_sha256(root: Path) -> str:
    bindings = tuple(
        (relative, content_sha256((root / relative).read_bytes()))
        for relative in _EXTRACTION_IMPLEMENTATION_PATHS
    )
    return canonical_execution_sha256(
        {
            "algorithm": "claim-support-validation-v2-extraction/v1",
            "source_bindings": bindings,
        }
    )


def _normalized_records(
    root: Path,
    *,
    qualification_run_dir: Path,
    cohort_run_dir: Path,
) -> tuple[
    V2CohortReceipt,
    ClaimSupportValidationV2RuntimeManifest,
    tuple[V2ExtractionRequestRecord, ...],
]:
    receipt = verify_completed_v2_cohort(
        root,
        qualification_run_dir=qualification_run_dir,
        run_dir=cohort_run_dir,
    )
    if (
        not receipt.technical_admission_passed
        or receipt.parsed_count != 360
        or receipt.technical_failure_count
    ):
        raise ClaimValidationV2ExtractionError(
            "V2 extraction requires the verified all-parsed cohort"
        )
    authorization = load_cohort_authorization(cohort_run_dir / "authorization.json")
    qualification = load_verified_qualification(root, qualification_run_dir)
    plan = build_cohort_plan(
        root,
        source_commit_ref=authorization.source_commit_ref,
        qualification_receipt=qualification,
    )
    manifest = build_v2_runtime_manifest(root)
    _, census, _ = _load_inputs(root)
    prepared = build_v2_cohort_gateway_requests(root, plan, authorization)
    frozen_by_sha = {item.request_sha256: item for item in census.primary_requests}
    receipt_by_sha = {item.v2_request_sha256: item for item in receipt.outcomes}
    store = cohort_run_dir / "attempt-store"
    records: list[V2ExtractionRequestRecord] = []
    for scheduled, prepared_item in zip(manifest.diagnosis_schedule, prepared, strict=True):
        identity = prepared_item.request.initial_attempt.request_identity_sha256
        source = frozen_by_sha.get(scheduled.source_request_sha256)
        terminal = receipt_by_sha.get(scheduled.v2_request_sha256)
        if (
            source is None
            or terminal is None
            or terminal.gateway_request_identity_sha256 != identity
        ):
            raise ClaimValidationV2ExtractionError(
                "V2 extraction lost a frozen request or terminal binding"
            )
        reader = _reader(store, identity)
        inventory = reader.terminal_inventory(identity)
        parsed = reader.terminal_parsed_payload(identity)
        if (
            parsed is None
            or inventory.gateway_status != "parsed"
            or inventory.parsed_response_sha256 != terminal.parsed_response_sha256
        ):
            raise ClaimValidationV2ExtractionError(
                "V2 terminal payload differs from the verified cohort receipt"
            )
        context = prepared_item.request.context
        if not isinstance(context, ModelVisibleEvidenceContext):
            raise ClaimValidationV2ExtractionError(
                "V2 extraction encountered a non-evidence context"
            )
        source_record_sha256 = canonical_execution_sha256(parsed)
        if source_record_sha256 != inventory.parsed_response_sha256:
            raise ClaimValidationV2ExtractionError(
                "V2 parsed object identity differs from its terminal ledger"
            )
        output = normalize_provider_output_v2(
            source,
            parsed,
            source_record_sha256=source_record_sha256,
            visible_evidence_ids=tuple(item.evidence_id for item in context.items),
        )
        # The selector is reused byte-for-byte from the hash-bound V2 runtime.
        # Global deduplication here would be an unregistered post-output filter.
        selected = _selected_claims(output)
        payload = {
            "sequence": scheduled.sequence,
            "schedule_round": scheduled.schedule_round,
            "v2_request_sha256": scheduled.v2_request_sha256,
            "gateway_request_identity_sha256": identity,
            "family_id": scheduled.family_id,
            "mechanism": scheduled.mechanism,
            "evidence_condition": scheduled.evidence_condition,
            "variant": scheduled.variant,
            "attempt_count": terminal.attempt_count,
            "provider_failure_categories": terminal.provider_failure_categories,
            "source_record_sha256": source_record_sha256,
            "normalized_output_sha256": output.output_sha256,
            "output_status": output.output_status,
            "atomic_claim_count": len(output.atomic_claims),
            "selected_claim_local_ids": tuple(item.claim_local_id for item in selected),
            "selected_claim_text_sha256s": tuple(
                canonical_execution_sha256({"canonical_claim_text": item.claim_text})
                for item in selected
            ),
        }
        records.append(
            V2ExtractionRequestRecord.model_validate(
                {
                    **payload,
                    "record_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    return receipt, manifest, tuple(records)


def build_v2_extraction_closeout(
    root: Path,
    *,
    qualification_run_dir: Path,
    cohort_run_dir: Path,
    dashboard_observation: V2DashboardInputUsageObservation | None = None,
) -> V2ClaimExtractionCloseout:
    """Rebuild every normalized output and fail closed on infeasible relation input."""

    checked_root = root.resolve()
    receipt, manifest, records = _normalized_records(
        checked_root,
        qualification_run_dir=qualification_run_dir.resolve(),
        cohort_run_dir=cohort_run_dir.resolve(),
    )
    protocol, _, _ = _load_inputs(checked_root)
    selected_text_hashes = tuple(
        digest for record in records for digest in record.selected_claim_text_sha256s
    )
    distinct_text_count = len(set(selected_text_hashes))
    distinct_output_count = len({record.normalized_output_sha256 for record in records})
    blockers = []
    if distinct_text_count < SAMPLE_TARGET:
        blockers.append("insufficient_distinct_canonical_claim_texts")
    if distinct_output_count < len(records):
        blockers.append("non_unique_source_output_identity_for_frozen_relation_batch")
    ready = not blockers
    payload: dict[str, object] = {
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "status": (
            "claim_support_validation_v2_extraction_relation_prerequisite_passed"
            if ready
            else "claim_support_validation_v2_extraction_relation_blocked"
        ),
        "source_commit_ref": receipt.source_commit_ref,
        "protocol_sha256": protocol.protocol_sha256,
        "runtime_manifest_sha256": manifest.manifest_sha256,
        "extraction_implementation_sha256": _implementation_sha256(checked_root),
        "cohort_authorization_sha256": receipt.authorization_sha256,
        "cohort_receipt_sha256": receipt.receipt_sha256,
        "cohort_terminal_store_sha256": receipt.terminal_store_sha256,
        "terminal_request_count": 360,
        "parsed_terminal_count": 360,
        "normalized_output_count": 360,
        "completed_output_count": sum(item.output_status == "completed" for item in records),
        "source_provider_attempt_count": receipt.provider_attempt_count,
        "provider_failure_attempt_count": sum(
            len(item.provider_failure_categories) for item in receipt.outcomes
        ),
        "raw_atomic_claim_count": sum(item.atomic_claim_count for item in records),
        "selected_source_claim_count": len(selected_text_hashes),
        "distinct_canonical_claim_text_count": distinct_text_count,
        "repeated_claim_instance_count": len(selected_text_hashes) - distinct_text_count,
        "distinct_normalized_output_count": distinct_output_count,
        "repeated_normalized_output_instance_count": len(records) - distinct_output_count,
        "maximum_source_claims_per_output": MAXIMUM_SOURCE_CLAIMS_PER_OUTPUT,
        "global_deduplication_before_relation_forbidden": True,
        "request_records": records,
        "relation_request_ceiling": RELATION_REQUEST_CEILING,
        "sample_target": SAMPLE_TARGET,
        "repeated_canonical_claim_text_forbidden_in_sample": (
            protocol.sampling_policy.repeated_canonical_claim_text_forbidden
        ),
        "distinct_text_prerequisite_satisfied": distinct_text_count >= SAMPLE_TARGET,
        "source_output_instance_identity_unique": distinct_output_count == len(records),
        "exact_frozen_selection_feasibility": (
            "impossible_distinct_text_shortfall"
            if distinct_text_count < SAMPLE_TARGET
            else "not_yet_assessed"
        ),
        "relation_frame_batch_built": False,
        "relation_request_count_known": False,
        "relation_outcomes_observed": False,
        "relation_execution_technically_unlocked": (receipt.relation_execution_unlocked),
        "relation_execution_ready": ready,
        "relation_execution_authorized": False,
        "extraction_blockers": tuple(blockers),
        "next_authorized_action": "review_separately_versioned_prospective_design",
        "dashboard_input_usage_observation": dashboard_observation,
        "additional_provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    identity = {
        **payload,
        "request_records": tuple(item.model_dump(mode="json") for item in records),
        "dashboard_input_usage_observation": (
            dashboard_observation.model_dump(mode="json")
            if dashboard_observation is not None
            else None
        ),
    }
    return V2ClaimExtractionCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_execution_sha256(identity)}
    )


def publish_v2_extraction_closeout(
    run_dir: Path, closeout: V2ClaimExtractionCloseout
) -> ImmutableFileDisposition:
    payload = (canonical_project_json(closeout.model_dump(mode="json")) + "\n").encode()
    return publish_immutable_file(run_dir / "closeout.json", payload)


def load_v2_extraction_closeout(path: Path) -> V2ClaimExtractionCloseout:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("closeout is not a regular file")
        return V2ClaimExtractionCloseout.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2ExtractionError(
            "V2 extraction closeout is unavailable or invalid"
        ) from exc


def verify_v2_extraction_closeout(
    root: Path,
    *,
    qualification_run_dir: Path,
    cohort_run_dir: Path,
    extraction_run_dir: Path,
) -> V2ClaimExtractionCloseout:
    """Rebuild the closeout and compare it to immutable private artifacts."""

    run = checked_extraction_run_directory(root, extraction_run_dir)
    actual = load_v2_extraction_closeout(run / "closeout.json")
    observation = actual.dashboard_input_usage_observation
    evidence_path = run / "dashboard-input-usage-evidence.png"
    if observation is None:
        if evidence_path.exists() or evidence_path.is_symlink():
            raise ClaimValidationV2ExtractionError(
                "dashboard evidence exists without a bound observation"
            )
    else:
        try:
            evidence = evidence_path.read_bytes()
        except OSError as exc:
            raise ClaimValidationV2ExtractionError(
                "dashboard usage evidence is unavailable"
            ) from exc
        observation = build_dashboard_input_usage_observation(
            observed_utc_date=observation.observed_utc_date,
            input_token_count=observation.input_token_count,
            evidence=evidence,
        )
    expected = build_v2_extraction_closeout(
        root,
        qualification_run_dir=qualification_run_dir,
        cohort_run_dir=cohort_run_dir,
        dashboard_observation=observation,
    )
    if actual != expected:
        raise ClaimValidationV2ExtractionError(
            "V2 extraction closeout differs from authenticated cohort state"
        )
    return expected


__all__ = [
    "build_dashboard_input_usage_observation",
    "build_v2_extraction_closeout",
    "checked_extraction_run_directory",
    "load_v2_extraction_closeout",
    "publish_v2_extraction_closeout",
    "verify_v2_extraction_closeout",
]
