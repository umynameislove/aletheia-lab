"""Offline V3.1 source-cohort safety and identity regression tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_support_v3_cohort import (
    ClaimSupportV3CohortError,
    build_authorization,
    build_cohort_plan,
    build_preflight,
    checked_cohort_run,
    load_authorization,
    rehearse_cohort,
    source_request_projections,
    validate_authorization,
)
from aletheia_lab.evaluation.claim_support_v3_cohort_execution import (
    V3DeterministicB0Adapter,
    prepare_source_requests,
)
from aletheia_lab.evaluation.claim_validation_v3_design import (
    build_design,
    source_payload,
    source_prompt,
    source_response_schema,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    publish,
    verify_protocol,
)
from aletheia_lab.model_gateway import execute_gateway_request
from aletheia_lab.project.identity import canonical_project_json, content_sha256

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
QUALIFICATION = {
    "schema_version": "claim-support-v3-qualification-receipt/2",
    "status": "v3_1_qualification_passed",
    "authorization_sha256": "1" * 64,
    "receipt_sha256": "2" * 64,
    "terminal_store_sha256": "3" * 64,
    "source_commit_ref": "4" * 40,
    "terminal_request_count": 33,
    "parsed_count": 33,
    "accepted_count": 33,
    "technical_failure_count": 0,
    "semantic_failure_count": 0,
    "cohort_planning_unlocked": True,
}


@pytest.fixture(scope="module")
def design() -> dict[str, object]:
    return build_design(ROOT)


@pytest.fixture(scope="module")
def plan() -> dict[str, object]:
    return build_cohort_plan(ROOT, QUALIFICATION, source_commit_ref=COMMIT)


@pytest.fixture(scope="module")
def rehearsal(plan: dict[str, object]) -> dict[str, object]:
    return rehearse_cohort(ROOT, plan, QUALIFICATION)


@pytest.fixture(scope="module")
def authorization(plan: dict[str, object], rehearsal: dict[str, object]) -> dict[str, object]:
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    return build_authorization(
        ROOT,
        Path("/private/tmp/aletheia-v3-source-cohort-unit"),
        plan,
        rehearsal,
        repository_state=state,
        operator_cost_ceiling_usd=20.0,
        authorized_at="2026-09-11T00:00:00Z",
    )


@pytest.fixture(scope="module")
def prepared(plan: dict[str, object], authorization: dict[str, object]):
    return prepare_source_requests(ROOT, plan, authorization)


def test_qualification_protocol_hash_is_unchanged_by_separate_cohort_modules():
    protocol = verify_protocol(ROOT)
    assert protocol["protocol_sha256"] == (
        "26ee09f82c92a6a822e296827bcb3946eb01b0889171ca080dcea5389ab2b141"
    )
    assert not any(
        "claim_support_v3_cohort" in path for path in protocol["implementation_bindings"]
    )


def test_plan_freezes_exact_source_census_cost_and_separate_relation_gate(plan):
    assert plan["source_request_count"] == 360
    assert plan["model_request_count"] == 315
    assert plan["deterministic_request_count"] == 45
    assert plan["source_claim_instance_count"] == 720
    assert plan["prospective_relation_request_count"] == 240
    assert len(plan["request_projection_sha256s"]) == 360
    assert len(set(plan["request_projection_sha256s"])) == 360
    assert plan["estimated_upper_cost_usd"] > 0
    assert plan["relation_execution_authorized"] is False
    assert plan["failures_preserved_without_adaptive_replacement"] is True
    assert plan["provider_calls_executed"] is False


def test_request_projection_census_is_stable_complete_and_route_exact():
    first = source_request_projections(ROOT)
    second = source_request_projections(ROOT)
    assert first == second
    assert [row["sequence"] for row in first] == list(range(1, 361))
    assert len({row["source_request_sha256"] for row in first}) == 360
    assert sum(row["execution_route"] == "model_gateway" for row in first) == 315
    assert sum(row["execution_route"] == "deterministic_local" for row in first) == 45
    assert all(
        (row["variant"] == "B0") == (row["execution_route"] == "deterministic_local")
        for row in first
    )


def test_rehearsal_roundtrips_every_source_without_constructing_relations(rehearsal):
    assert rehearsal["status"] == "v3_1_source_cohort_rehearsal_passed"
    assert rehearsal["all_deterministic_payloads_roundtrip"] is True
    assert rehearsal["exact_request_projections_rebuilt"] is True
    assert rehearsal["relation_requests_not_constructed"] is True
    assert rehearsal["provider_calls_executed"] is False


def test_nonpassing_qualification_cannot_freeze_a_cohort():
    failed = copy.deepcopy(QUALIFICATION)
    failed["semantic_failure_count"] = 1
    failed["accepted_count"] = 32
    with pytest.raises(ClaimSupportV3CohortError, match="exact pass"):
        build_cohort_plan(ROOT, failed, source_commit_ref=COMMIT)


def test_provider_source_contract_exposes_targets_but_not_expected_values(design):
    slot = next(
        item
        for item in design["slots"]
        if item["source_schedule"]["execution_route"] == "model_gateway"
    )
    prompt = source_prompt(slot["expected"], synthetic=False)
    schema = source_response_schema(slot["expected"])
    assert '"evidence_id"' in prompt
    assert '"json_pointer"' in prompt
    assert '"value"' not in prompt
    assert "expected_value" not in canonical_project_json(schema)
    assert schema["properties"]["readings"]["items"]["properties"]["value"] == {"type": "string"}


def test_prepared_gateway_census_has_unique_identities_and_no_relation_request(
    prepared,
):
    assert len(prepared) == 360
    assert sum(item.route == "model_gateway" for item in prepared) == 315
    assert sum(item.route == "deterministic_local" for item in prepared) == 45
    assert len({item.request.initial_attempt.request_identity_sha256 for item in prepared}) == 360
    assert all("relations" not in item.request.response_schema_json for item in prepared)


def test_b0_adapter_returns_exact_registered_payload_without_provider(design, prepared):
    index = next(
        index
        for index, slot in enumerate(design["slots"])
        if slot["source_schedule"]["execution_route"] == "deterministic_local"
    )
    item = prepared[index]
    adapter = V3DeterministicB0Adapter(item, design["slots"][index]["expected"])
    result = execute_gateway_request(
        item.request,
        adapter=adapter,
        clock=SystemMonotonicClock(),
        cancellation=NeverCancelled(),
        retry_controller=None,
    )
    assert result.status == "parsed"
    assert result.attempts[0].usage is not None
    assert result.attempts[0].usage.total_tokens == 0
    assert result.raw_response is not None
    assert json.loads(result.raw_response.content) == source_payload(
        design["slots"][index]["expected"]
    )


def test_authorization_is_exact_one_attempt_and_tamper_evident(plan, rehearsal, authorization):
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    run = Path("/private/tmp/aletheia-v3-source-cohort-unit")
    validate_authorization(
        ROOT,
        run,
        plan,
        rehearsal,
        authorization,
        repository_state=state,
    )
    tampered = copy.deepcopy(authorization)
    tampered["relation_execution_authorized"] = True
    with pytest.raises(ClaimSupportV3CohortError):
        validate_authorization(
            ROOT,
            run,
            plan,
            rehearsal,
            tampered,
            repository_state=state,
        )


def test_rehashed_type_confused_authorization_is_rejected(plan, rehearsal, authorization):
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    tampered = copy.deepcopy(authorization)
    tampered["registered_attempts"] = True
    unsigned = {
        key: value
        for key, value in tampered.items()
        if key not in {"authorization_ref", "authorization_sha256"}
    }
    digest = content_sha256(canonical_project_json(unsigned).encode("utf-8"))
    tampered["authorization_ref"] = f"ev-{digest}"
    tampered["authorization_sha256"] = digest
    with pytest.raises(ClaimSupportV3CohortError, match="authorization differs"):
        validate_authorization(
            ROOT,
            Path("/private/tmp/aletheia-v3-source-cohort-unit"),
            plan,
            rehearsal,
            tampered,
            repository_state=state,
        )


def test_authorization_loader_accepts_exact_document_and_rejects_extra_field(
    tmp_path, authorization
):
    path = tmp_path / "authorization.json"
    publish(path, authorization)
    assert load_authorization(path) == authorization
    foreign = {**authorization, "unregistered": True}
    foreign_path = tmp_path / "foreign.json"
    foreign_path.write_text(json.dumps(foreign), encoding="utf-8")
    with pytest.raises(ClaimSupportV3CohortError):
        load_authorization(foreign_path)


def test_preflight_blocks_missing_credential_without_exposing_secret(
    plan, rehearsal, authorization
):
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    result = build_preflight(
        ROOT,
        Path("/private/tmp/aletheia-v3-source-cohort-unit"),
        plan,
        rehearsal,
        authorization,
        repository_state=state,
        credential_present=False,
    )
    assert result["status"] == "v3_1_source_cohort_live_blocked"
    assert result["live_blockers"] == ["credential_missing"]
    assert "OPENAI_API_KEY" not in json.dumps(result)


def test_run_destination_rejects_repository_and_qualification_overlap(tmp_path):
    predecessor = tmp_path / "predecessor" / "closeout.json"
    qualification = tmp_path / "qualification"
    with pytest.raises(ClaimSupportV3CohortError, match="overlaps"):
        checked_cohort_run(ROOT, ROOT / "private-run", qualification, predecessor)
    with pytest.raises(ClaimSupportV3CohortError, match="overlaps"):
        checked_cohort_run(ROOT, qualification / "child", qualification, predecessor)
