"""Offline regression boundaries for the prospective V2 runtime."""

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    AtomicClaimV2,
    DiagnosisOutputV2,
    MaterialClaimPart,
)
from aletheia_lab.evaluation.claim_evidence_semantics import build_visible_evidence_item
from aletheia_lab.evaluation.claim_validation_v2_frames import (
    conflicting_measurement_ids,
    covered_parts,
    material_measurements,
    measurements,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    ClaimValidationV2RuntimeError,
    _load_inputs,
    build_v2_relation_frame_batch,
    build_v2_runtime_manifest,
    build_v2_runtime_readiness,
    canonical_json,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime_contracts import (
    evaluate_v2_technical_admission,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.validation_v2 import (
    V2RetryController,
    _provider_failure_category,
    _retry_after_ms,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def manifest():
    return build_v2_runtime_manifest(ROOT)


def test_exact_schedule_balances_every_round_without_reusing_v1_identity(manifest):
    schedule = manifest.diagnosis_schedule
    assert len({item.source_request_sha256 for item in schedule}) == 360
    assert len({item.v2_request_sha256 for item in schedule}) == 360
    assert all(item.source_request_sha256 != item.v2_request_sha256 for item in schedule)
    assert Counter(item.execution_route for item in schedule) == {
        "model_gateway": 315, "deterministic_local": 45,
    }
    for start in range(0, 360, 24):
        current = schedule[start:start + 24]
        assert set(Counter(item.mechanism for item in current).values()) == {8}
        assert set(Counter(item.evidence_condition for item in current).values()) == {8}
        assert set(Counter(item.variant for item in current).values()) == {3}
        assert set(Counter(item.family_order for item in current).values()) <= {4, 5}


def test_manifest_rebuild_is_byte_stable_and_not_live_authority(manifest):
    assert canonical_json(manifest) == canonical_json(build_v2_runtime_manifest(ROOT))
    readiness = build_v2_runtime_readiness(manifest)
    assert readiness.provider_calls_executed is False
    assert readiness.source_claim_expressiveness_review_required is True
    assert readiness.live_qualification_authorized is False
    assert readiness.claims_materialized is False
    assert readiness.blind_packets_generated is False
    assert readiness.qualification_request_count == 7
    assert all(item.synthetic_only and not item.admitted_to_corpus
               for item in manifest.qualification_probes)
    assert "src/aletheia_lab/model_gateway/runtime.py" in {
        item.relative_path for item in manifest.implementation_sources
    }


def test_manifest_tampering_fails_closed(manifest):
    payload = manifest.model_dump(mode="python")
    payload["minimum_provider_start_interval_ms"] = 0
    with pytest.raises(ValidationError):
        type(manifest).model_validate(payload)
    payload = manifest.model_dump(mode="python")
    payload["diagnosis_schedule"] = tuple(reversed(payload["diagnosis_schedule"]))
    with pytest.raises(ValidationError):
        type(manifest).model_validate(payload)


def _item(content, *, evidence_id="ev-key-measurement", title="Observed metrics"):
    return build_visible_evidence_item(
        evidence_id=evidence_id, kind="metric", title=title,
        content=content, source_content_sha256="a" * 64,
    )


def test_numeric_witness_requires_same_field_not_incidental_digits():
    source = _item('{"observed":{"macro_f1":0.7}}')
    unrelated = _item('{"clean":{"macro_f1":0.9}}')
    counter = _item('{"observed":{"macro_f1":0.9}}')
    parts = (("observed.macro_f1", Decimal("0.7")),)
    assert conflicting_measurement_ids((source,), (unrelated,), parts) == ()
    assert conflicting_measurement_ids((source,), (counter,), parts) == (source.evidence_id,)
    assert covered_parts(parts, (source,)) == {0}
    assert covered_parts(parts, (counter,)) == set()
    assert measurements(_item('{"hash":123}', evidence_id="ev-source-provenance")) == {}
    assert measurements(_item("The value is 0.7")) == {}


@pytest.mark.parametrize("status,category", [
    (429, "rate_limited"), (408, "http_408"), (409, "http_409"),
    (425, "http_425"), (500, "server_error"), (503, "server_error"),
    (400, "request_rejected"), (401, "request_rejected"),
])
def test_provider_failure_taxonomy_does_not_depend_on_exception_message(status, category):
    error = RuntimeError("secret-provider-body-must-not-be-persisted")
    error.status_code = status
    assert _provider_failure_category(error) == category


@pytest.mark.parametrize("value,expected", [
    ("2.5", 2500), ("0", 0), ("-1", 0), ("600", 60000),
    ("1e300", 60000), ("nan", None), ("inf", None), ("invalid", None),
])
def test_retry_after_is_bounded_and_rejects_nonfinite_values(value, expected):
    error = RuntimeError("irrelevant")
    error.headers = {"Retry-After": value}
    assert _retry_after_ms(error) == expected


def test_retry_controller_uses_seconds_and_respects_provider_delay():
    delays = []
    controller = V2RetryController(sleep=delays.append)
    for ordinal, retry_after in ((1, None), (2, 12000), (100, None)):
        controller.wait_before_retry(
            completed_attempt_ordinal=ordinal,
            provider_failure_category="rate_limited", retry_after_ms=retry_after,
        )
    assert delays == [5.0, 12.0, 60.0]
    with pytest.raises(ValueError):
        controller.wait_before_retry(
            completed_attempt_ordinal=1,
            provider_failure_category="request_rejected", retry_after_ms=None,
        )
    assert len(delays) == 3


def _output(text, *, record="b" * 64):
    claim = AtomicClaimV2(
        claim_local_id="claim-1", claim_type="evidence_statement", claim_text=text,
        material_parts=(MaterialClaimPart(part_id="part-1", text=text),),
        visible_evidence_ids=("ev-performance-summary",),
    )
    payload = {
        "schema_version": "diagnosis-output/2", "output_status": "completed",
        "atomic_claims": (claim.model_dump(mode="json"),),
        "abstention_reason": None, "parse_failure_code": None,
        "source_record_sha256": record,
    }
    return DiagnosisOutputV2.model_validate({
        **payload, "atomic_claims": (claim,),
        "output_sha256": canonical_execution_sha256(payload),
    })


def test_free_prose_does_not_become_an_asserted_quantitative_conflict():
    claim = _output("Performance changed.").atomic_claims[0]
    assert material_measurements(claim) == ()
    scoped = claim.model_copy(update={"material_parts": (
        MaterialClaimPart(part_id="part-1", text="observed.macro_f1 = 0.7"),
    )})
    assert material_measurements(scoped) == ()


def test_frame_batch_rejects_unknown_or_duplicate_request_bindings(manifest):
    _, _, evidence = _load_inputs(ROOT)
    output = _output("Performance changed.")
    with pytest.raises(ClaimValidationV2RuntimeError, match="outside"):
        build_v2_relation_frame_batch(
            manifest, evidence, (("0" * 64, output),),
            source_record_sha256_by_request={"0" * 64: output.source_record_sha256},
        )
    key = manifest.diagnosis_schedule[0].v2_request_sha256
    with pytest.raises(ClaimValidationV2RuntimeError, match="duplicated"):
        build_v2_relation_frame_batch(
            manifest, evidence, ((key, output), (key, output)),
            source_record_sha256_by_request={key: output.source_record_sha256},
        )


def test_natural_assignment_preserves_claim_and_records_ineligible_challenges(manifest):
    _, _, evidence = _load_inputs(ROOT)
    key = manifest.diagnosis_schedule[0].v2_request_sha256
    output = _output("Performance changed.")
    batch = build_v2_relation_frame_batch(
        manifest, evidence, ((key, output),),
        source_record_sha256_by_request={key: output.source_record_sha256},
    )
    assert len(batch.assignments) == 1
    assert batch.assignments[0].frame == "natural_context"
    assert batch.assignments[0].request.claim_text == "Performance changed."
    assert len(batch.ineligible_frames) == 3
    assert batch.relation_outcomes_observed is False


def test_qualification_payloads_are_synthetic_but_bind_model_schema_and_context(manifest):
    import json

    for probe in manifest.qualification_probes:
        assert probe.model_snapshot == "gpt-4.1-2025-04-14"
        assert probe.maximum_output_tokens == 2048
        assert "synthetic" in probe.prompt_text
        assert all(item.content == "The synthetic counter is 7." for item in probe.context.items)
        schema = json.loads(probe.response_schema_json)
        assert schema["type"] == "object"


def test_technical_admission_preserves_denominator_and_stratum_failures(manifest):
    all_ids = tuple(item.v2_request_sha256 for item in manifest.diagnosis_schedule)
    complete = evaluate_v2_technical_admission(
        manifest, terminal_request_sha256=all_ids, parsed_request_sha256=all_ids,
    )
    assert complete["technical_admission_passed"] is True
    failed = {item.v2_request_sha256 for item in manifest.diagnosis_schedule
              if item.variant == "A1"}
    five_failed = set(sorted(failed)[:5])
    partial = evaluate_v2_technical_admission(
        manifest, terminal_request_sha256=all_ids,
        parsed_request_sha256=tuple(key for key in all_ids if key not in five_failed),
    )
    assert partial["scheduled_request_count"] == 360
    assert partial["parsed_request_count"] == 355
    assert partial["technical_admission_passed"] is False
    assert "variant:A1:below_90_percent" in partial["blocker_codes"]
    assert partial["missingness_exchangeability_established"] is False
    with pytest.raises(ClaimValidationV2RuntimeError):
        evaluate_v2_technical_admission(
            manifest, terminal_request_sha256=all_ids + (all_ids[0],),
            parsed_request_sha256=all_ids,
        )
