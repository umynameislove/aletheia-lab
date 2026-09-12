"""Contract tests for V2 extraction and relation-prerequisite closeout."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation import claim_validation_v2_extraction as extraction
from aletheia_lab.evaluation.claim_validation_v2_extraction import (
    build_dashboard_input_usage_observation,
    checked_extraction_run_directory,
)
from aletheia_lab.evaluation.claim_validation_v2_extraction_contracts import (
    ClaimValidationV2ExtractionError,
    V2ClaimExtractionCloseout,
    V2ExtractionRequestRecord,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256


def _sha(value: int) -> str:
    return f"{value:064x}"


def _records() -> tuple[V2ExtractionRequestRecord, ...]:
    records = []
    variants = ("B0", "A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")
    for sequence in range(1, 361):
        payload = {
            "sequence": sequence,
            "schedule_round": (sequence - 1) // 24 + 1,
            "v2_request_sha256": _sha(sequence),
            "gateway_request_identity_sha256": _sha(sequence + 1000),
            "family_id": f"data-drift-family-{(sequence - 1) % 5 + 1}",
            "mechanism": "data_drift",
            "evidence_condition": ("full", "missing_key", "noisy")[(sequence - 1) % 3],
            "variant": variants[(sequence - 1) % len(variants)],
            "attempt_count": 1,
            "provider_failure_categories": (),
            "source_record_sha256": _sha(sequence % 116 + 2000),
            "normalized_output_sha256": _sha(sequence % 116 + 3000),
            "output_status": "completed",
            "atomic_claim_count": 1,
            "selected_claim_local_ids": ("claim-1",),
            "selected_claim_text_sha256s": (_sha(sequence % 112 + 4000),),
        }
        records.append(
            V2ExtractionRequestRecord.model_validate(
                {
                    **payload,
                    "record_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    return tuple(records)


def _closeout_payload() -> dict[str, object]:
    records = _records()
    return {
        "schema_version": "claim-support-validation-v2-extraction-closeout/v1",
        "status": "claim_support_validation_v2_extraction_relation_blocked",
        "source_commit_ref": "a" * 40,
        "protocol_sha256": "1" * 64,
        "runtime_manifest_sha256": "2" * 64,
        "extraction_implementation_sha256": "6" * 64,
        "cohort_authorization_sha256": "3" * 64,
        "cohort_receipt_sha256": "4" * 64,
        "cohort_terminal_store_sha256": "5" * 64,
        "terminal_request_count": 360,
        "parsed_terminal_count": 360,
        "normalized_output_count": 360,
        "completed_output_count": 360,
        "source_provider_attempt_count": 315,
        "provider_failure_attempt_count": 0,
        "raw_atomic_claim_count": 360,
        "selected_source_claim_count": 360,
        "distinct_canonical_claim_text_count": 112,
        "repeated_claim_instance_count": 248,
        "distinct_normalized_output_count": 116,
        "repeated_normalized_output_instance_count": 244,
        "maximum_source_claims_per_output": 2,
        "global_deduplication_before_relation_forbidden": True,
        "request_records": records,
        "relation_request_ceiling": 1440,
        "sample_target": 200,
        "repeated_canonical_claim_text_forbidden_in_sample": True,
        "distinct_text_prerequisite_satisfied": False,
        "source_output_instance_identity_unique": False,
        "exact_frozen_selection_feasibility": "impossible_distinct_text_shortfall",
        "relation_frame_batch_built": False,
        "relation_request_count_known": False,
        "relation_outcomes_observed": False,
        "relation_execution_technically_unlocked": True,
        "relation_execution_ready": False,
        "relation_execution_authorized": False,
        "extraction_blockers": (
            "insufficient_distinct_canonical_claim_texts",
            "non_unique_source_output_identity_for_frozen_relation_batch",
        ),
        "next_authorized_action": "review_separately_versioned_prospective_design",
        "dashboard_input_usage_observation": None,
        "additional_provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }


def _closeout(payload: dict[str, object]) -> V2ClaimExtractionCloseout:
    identity = {
        **payload,
        "request_records": tuple(
            item.model_dump(mode="json") for item in payload["request_records"]
        ),
    }
    return V2ClaimExtractionCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_execution_sha256(identity)}
    )


def test_closeout_blocks_paid_relation_work_when_200_distinct_claims_are_impossible() -> None:
    closeout = _closeout(_closeout_payload())

    assert closeout.distinct_canonical_claim_text_count == 112
    assert closeout.distinct_normalized_output_count == 116
    assert closeout.exact_frozen_selection_feasibility == ("impossible_distinct_text_shortfall")
    assert closeout.relation_execution_technically_unlocked is True
    assert closeout.relation_execution_ready is False
    assert closeout.relation_execution_authorized is False
    assert closeout.relation_frame_batch_built is False
    assert closeout.additional_provider_calls_executed is False


def test_closeout_rejects_post_hoc_deduplication_or_hidden_gate_changes() -> None:
    changed = _closeout_payload()
    changed["selected_source_claim_count"] = 112
    changed["repeated_claim_instance_count"] = 0
    with pytest.raises(ValidationError, match="does not reconcile"):
        _closeout(changed)

    changed = _closeout_payload()
    changed["extraction_blockers"] = ()
    changed["relation_execution_ready"] = True
    with pytest.raises(ValidationError, match="does not reconcile"):
        _closeout(changed)


def test_dashboard_input_tokens_remain_non_attributable_supplemental_evidence() -> None:
    observation = build_dashboard_input_usage_observation(
        observed_utc_date="2026-09-10",
        input_token_count=452_573,
        evidence=b"screenshot bytes",
    )

    assert observation.input_token_count == 452_573
    assert observation.cohort_exclusive_attribution_established is False
    assert observation.provider_output_token_count_available is False
    assert observation.exact_realized_cost_available is False


def test_extraction_destination_must_be_private_and_known(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    with pytest.raises(ClaimValidationV2ExtractionError, match="outside"):
        checked_extraction_run_directory(repository, repository / "private")
    with pytest.raises(ClaimValidationV2ExtractionError, match="outside"):
        checked_extraction_run_directory(repository, tmp_path)

    destination = tmp_path / "private"
    destination.mkdir()
    assert checked_extraction_run_directory(repository, destination) == destination
    (destination / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ClaimValidationV2ExtractionError, match="unknown"):
        checked_extraction_run_directory(repository, destination)


def test_closeout_builder_derives_both_prerequisite_blockers_from_terminal_records(
    monkeypatch,
) -> None:
    records = _records()
    receipt = SimpleNamespace(
        source_commit_ref="a" * 40,
        authorization_sha256="3" * 64,
        receipt_sha256="4" * 64,
        terminal_store_sha256="5" * 64,
        provider_attempt_count=315,
        outcomes=(),
        relation_execution_unlocked=True,
    )
    manifest = SimpleNamespace(manifest_sha256="2" * 64)
    protocol = SimpleNamespace(
        protocol_sha256="1" * 64,
        sampling_policy=SimpleNamespace(repeated_canonical_claim_text_forbidden=True),
    )
    monkeypatch.setattr(
        extraction,
        "_normalized_records",
        lambda *_args, **_kwargs: (receipt, manifest, records),
    )
    monkeypatch.setattr(
        extraction,
        "_load_inputs",
        lambda _root: (protocol, SimpleNamespace(), SimpleNamespace()),
    )

    closeout = extraction.build_v2_extraction_closeout(
        Path.cwd(),
        qualification_run_dir=Path("qualification"),
        cohort_run_dir=Path("cohort"),
    )

    assert closeout.status == "claim_support_validation_v2_extraction_relation_blocked"
    assert closeout.distinct_canonical_claim_text_count == 112
    assert closeout.distinct_normalized_output_count == 116
    assert closeout.extraction_blockers == (
        "insufficient_distinct_canonical_claim_texts",
        "non_unique_source_output_identity_for_frozen_relation_batch",
    )
    assert closeout.relation_execution_ready is False
    assert closeout.relation_execution_authorized is False


def test_normalized_records_replay_terminal_bindings_without_post_output_deduplication(
    monkeypatch,
) -> None:
    templates = _records()[:2]
    parsed = [
        {"schema_version": "diagnosis-provider-output/2", "result": {"ordinal": ordinal}}
        for ordinal in (1, 2)
    ]
    parsed_hashes = [canonical_execution_sha256(item) for item in parsed]
    schedules = tuple(
        SimpleNamespace(
            sequence=item.sequence,
            schedule_round=item.schedule_round,
            source_request_sha256=item.v2_request_sha256,
            v2_request_sha256=_sha(item.sequence + 5000),
            family_id=item.family_id,
            mechanism=item.mechanism,
            evidence_condition=item.evidence_condition,
            variant=item.variant,
        )
        for item in templates
    )
    identities = [_sha(item.sequence + 6000) for item in templates]
    context = SimpleNamespace(items=(SimpleNamespace(evidence_id="ev-visible"),))
    prepared = tuple(
        SimpleNamespace(
            request=SimpleNamespace(
                initial_attempt=SimpleNamespace(request_identity_sha256=identity),
                context=context,
            )
        )
        for identity in identities
    )
    terminals = tuple(
        SimpleNamespace(
            v2_request_sha256=schedule.v2_request_sha256,
            gateway_request_identity_sha256=identity,
            parsed_response_sha256=parsed_hash,
            attempt_count=1,
            provider_failure_categories=(),
        )
        for schedule, identity, parsed_hash in zip(
            schedules, identities, parsed_hashes, strict=True
        )
    )
    receipt = SimpleNamespace(
        technical_admission_passed=True,
        parsed_count=360,
        technical_failure_count=0,
        outcomes=terminals,
    )
    manifest = SimpleNamespace(diagnosis_schedule=schedules)
    census = SimpleNamespace(
        primary_requests=tuple(
            SimpleNamespace(request_sha256=schedule.source_request_sha256) for schedule in schedules
        )
    )

    class Reader:
        def __init__(self, index):
            self.index = index

        def terminal_inventory(self, _identity):
            return SimpleNamespace(
                gateway_status="parsed",
                parsed_response_sha256=parsed_hashes[self.index],
            )

        def terminal_parsed_payload(self, _identity):
            return parsed[self.index]

    normalized = SimpleNamespace(
        output_sha256="7" * 64,
        output_status="completed",
        atomic_claims=(
            SimpleNamespace(claim_local_id="claim-1", claim_text="First claim."),
            SimpleNamespace(claim_local_id="claim-2", claim_text="Second claim."),
        ),
    )
    monkeypatch.setattr(extraction, "verify_completed_v2_cohort", lambda *_a, **_k: receipt)
    monkeypatch.setattr(
        extraction,
        "load_cohort_authorization",
        lambda _path: SimpleNamespace(source_commit_ref="a" * 40),
    )
    monkeypatch.setattr(extraction, "load_verified_qualification", lambda *_a, **_k: object())
    monkeypatch.setattr(extraction, "build_cohort_plan", lambda *_a, **_k: object())
    monkeypatch.setattr(extraction, "build_v2_runtime_manifest", lambda _root: manifest)
    monkeypatch.setattr(
        extraction,
        "_load_inputs",
        lambda _root: (SimpleNamespace(), census, SimpleNamespace()),
    )
    monkeypatch.setattr(
        extraction,
        "build_v2_cohort_gateway_requests",
        lambda *_a, **_k: prepared,
    )
    monkeypatch.setattr(extraction, "ModelVisibleEvidenceContext", SimpleNamespace)
    monkeypatch.setattr(
        extraction,
        "_reader",
        lambda _store, identity: Reader(identities.index(identity)),
    )
    monkeypatch.setattr(
        extraction,
        "normalize_provider_output_v2",
        lambda *_a, **_k: normalized,
    )
    monkeypatch.setattr(extraction, "_selected_claims", lambda output: output.atomic_claims)

    actual_receipt, actual_manifest, records = extraction._normalized_records(
        Path.cwd(),
        qualification_run_dir=Path("qualification"),
        cohort_run_dir=Path("cohort"),
    )

    assert actual_receipt is receipt
    assert actual_manifest is manifest
    assert len(records) == 2
    assert records[0].selected_claim_local_ids == ("claim-1", "claim-2")
    assert records[0].source_record_sha256 == parsed_hashes[0]
    assert records[0].record_sha256 != records[1].record_sha256


def test_published_closeout_loads_and_verify_rebuilds_without_dashboard_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    closeout = _closeout(_closeout_payload())
    disposition = extraction.publish_v2_extraction_closeout(tmp_path, closeout)
    assert disposition == "created"
    assert extraction.load_v2_extraction_closeout(tmp_path / "closeout.json") == closeout
    repository = tmp_path.parent / "separate-repository"

    monkeypatch.setattr(
        extraction,
        "build_v2_extraction_closeout",
        lambda *_args, **_kwargs: closeout,
    )
    assert (
        extraction.verify_v2_extraction_closeout(
            repository,
            qualification_run_dir=tmp_path / "qualification",
            cohort_run_dir=tmp_path / "cohort",
            extraction_run_dir=tmp_path,
        )
        == closeout
    )

    monkeypatch.setattr(
        extraction,
        "build_v2_extraction_closeout",
        lambda *_args, **_kwargs: closeout.model_copy(update={"source_commit_ref": "b" * 40}),
    )
    with pytest.raises(ClaimValidationV2ExtractionError, match="differs"):
        extraction.verify_v2_extraction_closeout(
            repository,
            qualification_run_dir=tmp_path / "qualification",
            cohort_run_dir=tmp_path / "cohort",
            extraction_run_dir=tmp_path,
        )


def test_extraction_loader_rejects_missing_and_linked_closeouts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ClaimValidationV2ExtractionError, match="unavailable"):
        extraction.load_v2_extraction_closeout(missing)

    linked = tmp_path / "linked.json"
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked or original_is_symlink(path),
    )
    with pytest.raises(ClaimValidationV2ExtractionError, match="unavailable"):
        extraction.load_v2_extraction_closeout(linked)
