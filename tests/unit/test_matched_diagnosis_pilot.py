"""Matched request, persistence and retry-boundary regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.diagnosis.adapters import AdapterError, DeterministicMockAdapter
from aletheia_lab.diagnosis.pilot import (
    DEFAULT_SETTINGS,
    build_matched_requests,
    run_p1_matched_pilot,
    validate_matched_requests,
    validate_p1_matched_pilot,
    validate_source_binding,
)
from aletheia_lab.diagnosis.prompts import (
    PROMPT_VERSION,
    RESPONSE_FORMAT,
    render_evidence_for,
    rendering_version_for,
    system_prompt_for,
)
from aletheia_lab.diagnosis.schema import (
    DiagnosisRequest,
    DiagnosisRunRecord,
    GenerationSettings,
    PilotVariant,
    ProviderIdentity,
    ProviderResponse,
    UsageRecord,
    parse_diagnosis_output,
)
from aletheia_lab.evidence.p1 import generate_p1_evidence_store
from aletheia_lab.evidence.schema import project_diagnosis_evidence
from aletheia_lab.evidence.store import load_bundle_store


@pytest.fixture
def p1_store(p1_generator_config: Path, tmp_path: Path) -> Path:
    cases = tmp_path / "cases"
    store = tmp_path / "evidence-store"
    generate_p1(p1_generator_config, cases)
    generate_p1_evidence_store(cases, store)
    return store


def _views(store: Path):
    return tuple(project_diagnosis_evidence(bundle) for bundle in load_bundle_store(store).bundles)


def test_matched_requests_have_identical_facts_budget_and_identity(p1_store: Path) -> None:
    requests = build_matched_requests(
        _views(p1_store),
        provider_identity=DeterministicMockAdapter().identity,
    )

    assert len(requests) == 30
    assert len({request.diagnosis_view.diagnosis_context_id for request in requests}) == 15
    for context_id in {request.diagnosis_view.diagnosis_context_id for request in requests}:
        siblings = [
            request
            for request in requests
            if request.diagnosis_view.diagnosis_context_id == context_id
        ]
        assert {request.variant for request in siblings} == set(PilotVariant)
        assert len({request.facts_sha256 for request in siblings}) == 1
        assert len({request.provider_identity for request in siblings}) == 1
        assert len({request.settings for request in siblings}) == 1
        assert len({request.response_format for request in siblings}) == 1
        assert len({request.prompt_sha256 for request in siblings}) == 2
        b1 = next(request for request in siblings if request.variant == PilotVariant.B1_PLAIN)
        a3 = next(
            request for request in siblings if request.variant == PilotVariant.A3_EVIDENCE_CONTRACT
        )
        assert b1.rendered_evidence != a3.rendered_evidence
        assert b1.rendered_evidence.startswith("Observation 1\n")
        assert a3.rendered_evidence.startswith("{")
        assert b1.diagnosis_view == a3.diagnosis_view


def test_request_projection_contains_no_evaluator_metadata_or_condition_labels(
    p1_store: Path,
) -> None:
    request = build_matched_requests(
        _views(p1_store)[:1], provider_identity=DeterministicMockAdapter().identity
    )[0]
    payload = request.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True).casefold()

    for forbidden in (
        "case_id",
        "case_family_id",
        "evidence_bundle_id",
        "evidence_condition",
        "expected_diagnosis_behavior",
        "distractor",
        "missing_key",
    ):
        assert forbidden not in serialized


def test_request_hashes_and_extra_fields_fail_closed(p1_store: Path) -> None:
    request = build_matched_requests(
        _views(p1_store)[:1], provider_identity=DeterministicMockAdapter().identity
    )[0]
    payload = request.model_dump(mode="json")
    payload["facts_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="facts_sha256"):
        DiagnosisRequest.model_validate_json(json.dumps(payload))

    payload = request.model_dump(mode="json")
    payload["hidden_truth"] = "answer"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DiagnosisRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_numeric_contracts_reject_non_finite_values(invalid: float) -> None:
    with pytest.raises(ValidationError):
        GenerationSettings(
            temperature=invalid,
            top_p=1.0,
            max_output_tokens=600,
            seed=17,
            timeout_seconds=60.0,
            max_attempts=2,
        )
    with pytest.raises(ValidationError):
        UsageRecord(input_tokens=1, output_tokens=1, estimated_cost_usd=invalid)


@pytest.mark.parametrize(
    "mismatch", ["facts", "budget", "identity", "response_format", "prompt", "renderer"]
)
def test_matched_validator_rejects_each_comparison_bypass(p1_store: Path, mismatch: str) -> None:
    view = _views(p1_store)[0]
    identity = DeterministicMockAdapter().identity
    first = DiagnosisRequest.build(
        variant=PilotVariant.B1_PLAIN,
        provider_identity=identity,
        settings=DEFAULT_SETTINGS,
        prompt_version=PROMPT_VERSION,
        rendering_version=rendering_version_for(PilotVariant.B1_PLAIN),
        system_prompt=system_prompt_for(PilotVariant.B1_PLAIN),
        response_format=RESPONSE_FORMAT,
        diagnosis_view=view,
        rendered_evidence=render_evidence_for(PilotVariant.B1_PLAIN, view),
    )
    changed_view = _views(p1_store)[1] if mismatch == "facts" else view
    changed_settings = (
        GenerationSettings(
            temperature=0.0,
            top_p=1.0,
            max_output_tokens=601,
            seed=17,
            timeout_seconds=60.0,
            max_attempts=2,
        )
        if mismatch == "budget"
        else DEFAULT_SETTINGS
    )
    changed_identity = (
        ProviderIdentity(provider="deterministic-mock", model="other", version="1")
        if mismatch == "identity"
        else identity
    )
    changed_format = (
        RESPONSE_FORMAT + "\nextra" if mismatch == "response_format" else RESPONSE_FORMAT
    )
    changed_prompt = (
        system_prompt_for(PilotVariant.A3_EVIDENCE_CONTRACT) + "\nInjected change"
        if mismatch == "prompt"
        else system_prompt_for(PilotVariant.A3_EVIDENCE_CONTRACT)
    )
    rendered = render_evidence_for(PilotVariant.A3_EVIDENCE_CONTRACT, changed_view)
    if mismatch == "renderer":
        rendered += "\nInjected extra observation"
    second = DiagnosisRequest.build(
        variant=PilotVariant.A3_EVIDENCE_CONTRACT,
        provider_identity=changed_identity,
        settings=changed_settings,
        prompt_version=PROMPT_VERSION,
        rendering_version=rendering_version_for(PilotVariant.A3_EVIDENCE_CONTRACT),
        system_prompt=changed_prompt,
        response_format=changed_format,
        diagnosis_view=changed_view,
        rendered_evidence=rendered,
    )

    with pytest.raises(ValueError):
        validate_matched_requests((first, second))


def test_fully_rehashed_fabricated_view_cannot_break_source_binding(p1_store: Path) -> None:
    source = _views(p1_store)[0]
    forged_item = source.items[0].model_copy(update={"title": "Fabricated observation"})
    forged = source.model_copy(update={"items": (forged_item, *source.items[1:])})
    requests = build_matched_requests(
        (forged,), provider_identity=DeterministicMockAdapter().identity
    )

    with pytest.raises(ValueError, match="differs from its source"):
        validate_source_binding(requests, (source,))


def test_parser_rejects_unknown_citation_and_strong_causal_label(p1_store: Path) -> None:
    visible = {item.evidence_id for item in _views(p1_store)[0].items}
    payload = {
        "schema_version": "diagnosis-output/1",
        "root_cause_hypothesis": "Bounded hypothesis",
        "claim_strength": "bounded_causal_hypothesis",
        "supporting_evidence_ids": ["hidden-answer"],
        "counterevidence_ids": [],
        "missing_evidence": [],
        "confidence": 0.5,
        "abstain": False,
    }
    with pytest.raises(ValueError, match="non-visible"):
        parse_diagnosis_output(json.dumps(payload), visible)

    payload["supporting_evidence_ids"] = []
    payload["claim_strength"] = "strong_causal_conclusion"
    with pytest.raises(ValidationError):
        parse_diagnosis_output(json.dumps(payload), visible)

    duplicate = (
        '{"schema_version":"diagnosis-output/1",'
        '"root_cause_hypothesis":"x","claim_strength":"observation",'
        '"supporting_evidence_ids":[],"counterevidence_ids":[],"missing_evidence":[],'
        '"confidence":0.2,"confidence":0.9,"abstain":false}'
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_diagnosis_output(duplicate, visible)


def test_mock_pilot_persists_complete_raw_and_parsed_matrix(p1_store: Path, tmp_path: Path) -> None:
    output = tmp_path / "pilot"
    manifest = run_p1_matched_pilot(
        p1_store,
        output,
        adapter=DeterministicMockAdapter(),
    )

    assert manifest.context_count == 15
    assert manifest.variant_count == 2
    assert manifest.run_count == manifest.success_count == 30
    assert manifest.unresolved_count == 0
    validated = validate_p1_matched_pilot(output, p1_store)
    assert validated == manifest
    for entry in manifest.entries:
        record = DiagnosisRunRecord.model_validate_json(
            (output / entry.relative_path).read_text("utf-8")
        )
        assert record.final_status == "success"
        assert len(record.attempts) == 1
        attempt = record.attempts[0]
        assert attempt.raw_relative_path is not None
        assert attempt.parsed_relative_path is not None
        assert (output / attempt.raw_relative_path).is_file()
        assert (output / attempt.parsed_relative_path).is_file()


class _InvalidJsonAdapter:
    def __init__(self) -> None:
        self._identity = DeterministicMockAdapter().identity

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        return ProviderResponse(
            response_id=f"invalid-{request.request_id}",
            provider_identity=self.identity,
            raw_text="not-json",
            usage=UsageRecord(input_tokens=1, output_tokens=1, estimated_cost_usd=0.0),
            latency_ms=0.0,
        )


def test_parse_failures_preserve_raw_retry_records_and_remain_in_denominator(
    p1_store: Path, tmp_path: Path
) -> None:
    output = tmp_path / "pilot-invalid"
    manifest = run_p1_matched_pilot(p1_store, output, adapter=_InvalidJsonAdapter())

    assert manifest.run_count == manifest.unresolved_count == 30
    assert manifest.success_count == 0
    for entry in manifest.entries:
        record = DiagnosisRunRecord.model_validate_json(
            (output / entry.relative_path).read_text("utf-8")
        )
        assert record.final_status == "unresolved"
        assert [attempt.status for attempt in record.attempts] == [
            "parse_failure",
            "parse_failure",
        ]
        for attempt in record.attempts:
            assert attempt.raw_relative_path is not None
            assert (output / attempt.raw_relative_path).read_text("utf-8") == "not-json"
            assert attempt.parsed_relative_path is None
    validate_p1_matched_pilot(output, p1_store)


class _SilentSwitchAdapter(_InvalidJsonAdapter):
    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        response = DeterministicMockAdapter().complete(request)
        data = response.model_dump(mode="json")
        data["provider_identity"]["model"] = "silently-switched-model"
        return ProviderResponse.model_validate(data)


def test_silent_model_switch_is_recorded_and_never_parsed(p1_store: Path, tmp_path: Path) -> None:
    output = tmp_path / "pilot-switched"
    manifest = run_p1_matched_pilot(p1_store, output, adapter=_SilentSwitchAdapter())

    assert manifest.unresolved_count == 30
    first = DiagnosisRunRecord.model_validate_json(
        (output / manifest.entries[0].relative_path).read_text("utf-8")
    )
    assert [attempt.status for attempt in first.attempts] == [
        "identity_mismatch",
        "identity_mismatch",
    ]
    assert all(attempt.raw_relative_path for attempt in first.attempts)
    assert all(attempt.parsed_relative_path is None for attempt in first.attempts)


class _ProviderFailureAdapter(_InvalidJsonAdapter):
    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        raise AdapterError("provider_timeout", f"timeout for {request.request_id}")


def test_provider_errors_are_immutable_attempts_and_remain_unresolved(
    p1_store: Path, tmp_path: Path
) -> None:
    output = tmp_path / "pilot-provider-errors"
    manifest = run_p1_matched_pilot(p1_store, output, adapter=_ProviderFailureAdapter())

    assert manifest.unresolved_count == 30
    record = DiagnosisRunRecord.model_validate_json(
        (output / manifest.entries[0].relative_path).read_text("utf-8")
    )
    assert [attempt.status for attempt in record.attempts] == [
        "adapter_error",
        "adapter_error",
    ]
    assert all(attempt.error_type == "provider_timeout" for attempt in record.attempts)
    assert all(attempt.raw_relative_path is None for attempt in record.attempts)
    validate_p1_matched_pilot(output, p1_store)


def test_raw_or_run_tamper_fails_validation(p1_store: Path, tmp_path: Path) -> None:
    output = tmp_path / "pilot-tamper"
    manifest = run_p1_matched_pilot(p1_store, output, adapter=DeterministicMockAdapter())
    record = DiagnosisRunRecord.model_validate_json(
        (output / manifest.entries[0].relative_path).read_text("utf-8")
    )
    raw_path = output / record.attempts[0].raw_relative_path  # type: ignore[arg-type]
    raw_path.write_text(raw_path.read_text("utf-8") + " ", encoding="utf-8")

    with pytest.raises(ValueError, match="raw response hash mismatch"):
        validate_p1_matched_pilot(output, p1_store)


def test_mock_pilot_is_byte_identical_across_runs(p1_store: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    run_p1_matched_pilot(p1_store, first, adapter=DeterministicMockAdapter())
    run_p1_matched_pilot(p1_store, second, adapter=DeterministicMockAdapter())

    first_files = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files


class _ExternalAdapter(_InvalidJsonAdapter):
    def __init__(self) -> None:
        self._identity = ProviderIdentity(provider="external", model="paid-model", version="1")
        self.called = False

    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        self.called = True
        return super().complete(request)


def test_offline_runner_refuses_external_adapter(
    p1_store: Path, tmp_path: Path
) -> None:
    adapter = _ExternalAdapter()
    with pytest.raises(ValueError, match="only authorizes the deterministic mock"):
        run_p1_matched_pilot(p1_store, tmp_path / "external", adapter=adapter)
    assert adapter.called is False


def test_offline_runner_refuses_unfrozen_settings(p1_store: Path, tmp_path: Path) -> None:
    settings = GenerationSettings(
        temperature=0.1,
        top_p=1.0,
        max_output_tokens=600,
        seed=17,
        timeout_seconds=60.0,
        max_attempts=2,
    )
    with pytest.raises(ValueError, match="frozen mock-pilot contract"):
        run_p1_matched_pilot(
            p1_store,
            tmp_path / "unfrozen",
            adapter=_InvalidJsonAdapter(),
            settings=settings,
        )
