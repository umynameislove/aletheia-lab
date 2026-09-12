"""Offline V3.1 source-cohort safety and identity regression tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import aletheia_lab.evaluation.claim_support_v3_cohort as cohort
import aletheia_lab.evaluation.claim_support_v3_cohort_execution as cohort_execution
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_support_v3_cohort import (
    ClaimSupportV3CohortError,
    build_authorization,
    build_cohort_plan,
    build_preflight,
    checked_cohort_run,
    load_authorization,
    load_verified_qualification,
    qualification_rehearsal_is_stable,
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


def test_receipt_replay_preserves_semantic_failure_and_parsed_payload_hash(
    tmp_path,
    monkeypatch,
    design,
    plan,
    rehearsal,
    authorization,
    prepared,
):
    selected = prepared[:2]
    slots = copy.deepcopy(design["slots"][:2])
    payloads = {
        item.request.initial_attempt.request_identity_sha256: source_payload(slot["expected"])
        for item, slot in zip(selected, slots, strict=True)
    }
    rejected_identity = selected[1].request.initial_attempt.request_identity_sha256
    rejected_payload = payloads[rejected_identity]
    rejected_payload["readings"][0]["value"] += "-tampered"

    class Reader:
        def __init__(self, identity):
            self.identity = identity

        def terminal_inventory(self, _identity):
            return SimpleNamespace(gateway_status="parsed")

        def terminal_parsed_payload(self, _identity):
            return payloads[self.identity]

        def terminal_attempt_records(self, _identity):
            return (SimpleNamespace(provider_failure_category=None),)

    monkeypatch.setattr(cohort_execution, "SOURCE_REQUEST_COUNT", 2)
    monkeypatch.setattr(cohort_execution, "MODEL_REQUEST_COUNT", 2)
    monkeypatch.setattr(cohort_execution, "DETERMINISTIC_REQUEST_COUNT", 0)
    monkeypatch.setattr(cohort_execution, "SOURCE_CLAIM_INSTANCE_COUNT", 4)
    monkeypatch.setattr(cohort_execution, "build_design", lambda _root: {"slots": slots})
    monkeypatch.setattr(
        cohort_execution,
        "verified_complete_claim_corpus_store_sha256",
        lambda _store, _prepared: "f" * 64,
    )
    monkeypatch.setattr(
        cohort_execution,
        "_reader",
        lambda _store, identity: Reader(identity),
    )
    monkeypatch.setattr(
        cohort_execution,
        "_usage_census",
        lambda _store, _prepared: (True, 20, 10, 30),
    )
    publish(tmp_path / "lease.json", cohort_execution._lease(plan, authorization))

    receipt = cohort_execution.rebuild_receipt(
        ROOT,
        tmp_path,
        plan,
        rehearsal,
        authorization,
        selected,
    )

    assert receipt["status"] == "v3_1_source_cohort_complete_with_failures"
    assert receipt["parsed_count"] == 2
    assert receipt["accepted_count"] == 1
    assert receipt["technical_failure_count"] == 0
    assert receipt["semantic_failure_count"] == 1
    assert receipt["source_claim_instance_count"] == 2
    assert receipt["relation_planning_unlocked"] is False
    assert receipt["provider_usage_complete"] is True
    assert receipt["observed_provider_total_token_count"] == 30
    assert receipt["outcomes"][1]["parsed_payload_sha256"] is not None
    assert receipt["outcomes"][1]["source_instance_sha256s"] == []


def test_execution_resumes_sealed_terminals_and_uses_route_exact_adapters(
    tmp_path,
    monkeypatch,
    design,
    plan,
    rehearsal,
    authorization,
    prepared,
):
    local_index = next(
        index for index, item in enumerate(prepared) if item.route == "deterministic_local"
    )
    model_indexes = [index for index, item in enumerate(prepared) if item.route == "model_gateway"][
        :2
    ]
    indexes = [local_index, *model_indexes]
    selected = tuple(prepared[index] for index in indexes)
    slots = [design["slots"][index] for index in indexes]
    identities = [item.request.initial_attempt.request_identity_sha256 for item in selected]
    events: list[tuple[str, str]] = []

    class Shard:
        def __init__(self, identity):
            self.identity = identity

        def __getattr__(self, name):
            def record(*_args):
                events.append((self.identity, name))

            return record

    shards = {identity: Shard(identity) for identity in identities}

    class Store:
        def shards(self, _prepared):
            return shards

    adapters = []
    retries = []
    result = SimpleNamespace(
        attempts=(SimpleNamespace(),),
        raw_response=SimpleNamespace(content=b"{}"),
    )

    def execute(_request, *, adapter, retry_controller, **_kwargs):
        adapters.append(adapter)
        retries.append(retry_controller)
        return result

    provider = SimpleNamespace(name="provider")
    controller = SimpleNamespace(name="retry")
    published = []
    expected_receipt = {"status": "v3_1_source_cohort_passed"}
    states = {identities[0]: None, identities[1]: None, identities[2]: "terminal_published"}
    monkeypatch.setattr(
        cohort_execution,
        "_validated_inputs",
        lambda *_args, **_kwargs: (authorization, selected),
    )
    monkeypatch.setattr(
        cohort_execution,
        "_registered_store",
        lambda *_args, **_kwargs: (Store(), states),
    )
    monkeypatch.setattr(cohort_execution, "build_design", lambda _root: {"slots": slots})
    monkeypatch.setattr(cohort_execution, "execute_gateway_request", execute)
    monkeypatch.setattr(
        cohort_execution,
        "rebuild_receipt",
        lambda *_args, **_kwargs: expected_receipt,
    )
    monkeypatch.setattr(
        cohort_execution,
        "publish",
        lambda path, payload: published.append((path, payload)),
    )

    receipt = cohort_execution.execute_source_cohort(
        ROOT,
        tmp_path,
        plan,
        rehearsal,
        repository_state=RepositoryExecutionState(
            branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
        ),
        confirmation=authorization["authorization_sha256"],
        adapter=provider,
        clock=SystemMonotonicClock(),
        retry=controller,
    )

    assert receipt == expected_receipt
    assert isinstance(adapters[0], V3DeterministicB0Adapter)
    assert adapters[1] is provider
    assert retries == [None, controller]
    assert not any(identity == identities[2] for identity, _event in events)
    assert published == [(tmp_path / "receipt.json", expected_receipt)]
    for identity in identities[:2]:
        assert (identity, "publish_terminal") in events


def test_execution_and_replay_fail_closed_on_authority_or_receipt_mismatch(
    tmp_path,
    monkeypatch,
    plan,
    rehearsal,
    authorization,
    prepared,
):
    selected = prepared[:1]
    monkeypatch.setattr(
        cohort_execution,
        "_validated_inputs",
        lambda *_args, **_kwargs: (authorization, selected),
    )
    with pytest.raises(ClaimSupportV3CohortError, match="confirmation differs"):
        cohort_execution.execute_source_cohort(
            ROOT,
            tmp_path,
            plan,
            rehearsal,
            repository_state=RepositoryExecutionState(
                branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
            ),
            confirmation="0" * 64,
            adapter=SimpleNamespace(),
        )

    monkeypatch.setattr(cohort_execution, "load_authorization", lambda _path: authorization)
    actual = {"receipt_sha256": "a" * 64}
    expected = {"receipt_sha256": "b" * 64}
    monkeypatch.setattr(cohort_execution, "read_document", lambda *_args: actual)
    monkeypatch.setattr(
        cohort_execution,
        "rebuild_receipt",
        lambda *_args, **_kwargs: expected,
    )
    with pytest.raises(ClaimSupportV3CohortError, match="receipt differs"):
        cohort_execution.verify_source_cohort(ROOT, tmp_path, plan, rehearsal)


def test_usage_census_requires_complete_provider_metadata(monkeypatch, prepared):
    selected = tuple(item for item in prepared if item.route == "model_gateway")[:2]
    records = {
        selected[0].request.initial_attempt.request_identity_sha256: SimpleNamespace(
            usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18)
        ),
        selected[1].request.initial_attempt.request_identity_sha256: SimpleNamespace(usage=None),
    }

    class Reader:
        def __init__(self, identity):
            self.identity = identity

        def terminal_attempt_records(self, _identity):
            return (records[self.identity],)

    monkeypatch.setattr(
        cohort_execution,
        "_reader",
        lambda _store, identity: Reader(identity),
    )
    assert cohort_execution._usage_census(Path("unused"), selected) == (
        False,
        None,
        None,
        None,
    )

    records[selected[1].request.initial_attempt.request_identity_sha256] = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=13, output_tokens=5, total_tokens=18)
    )
    assert cohort_execution._usage_census(Path("unused"), selected) == (True, 24, 12, 36)


def test_registered_store_enforces_lease_and_resumable_state_boundaries(
    tmp_path,
    monkeypatch,
    plan,
    authorization,
    prepared,
):
    selected = prepared[:2]
    current_state = None

    class Shard:
        def current_state(self, _identity):
            return current_state

    class Store:
        def __init__(self, path, *, clock):
            self.path = path
            self.clock = clock

        def shards(self, items):
            return {item.request.initial_attempt.request_identity_sha256: Shard() for item in items}

    monkeypatch.setattr(cohort_execution, "ClaimCorpusAttemptStore", Store)
    store, states = cohort_execution._registered_store(
        tmp_path / "fresh",
        plan,
        authorization,
        selected,
        SystemMonotonicClock(),
    )
    assert store.path == tmp_path / "fresh" / "attempt-store"
    assert set(states.values()) == {None}
    assert cohort_execution.read_document(
        tmp_path / "fresh" / "lease.json", "lease_sha256"
    ) == cohort_execution._lease(plan, authorization)

    orphaned = tmp_path / "orphaned"
    (orphaned / "attempt-store").mkdir(parents=True)
    with pytest.raises(ClaimSupportV3CohortError, match="without registered lease"):
        cohort_execution._registered_store(
            orphaned,
            plan,
            authorization,
            selected,
            SystemMonotonicClock(),
        )

    wrong = tmp_path / "wrong-lease"
    cohort_execution.publish(
        wrong / "lease.json",
        cohort_execution._lease(plan, {**authorization, "authorization_sha256": "0" * 64}),
    )
    with pytest.raises(ClaimSupportV3CohortError, match="lease differs"):
        cohort_execution._registered_store(
            wrong,
            plan,
            authorization,
            selected,
            SystemMonotonicClock(),
        )

    current_state = "started"
    with pytest.raises(ClaimSupportV3CohortError, match="forbids continuation"):
        cohort_execution._registered_store(
            tmp_path / "partial",
            plan,
            authorization,
            selected,
            SystemMonotonicClock(),
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


def test_run_destination_and_qualification_inputs_fail_closed_on_links_or_unknown_files(
    tmp_path,
    monkeypatch,
):
    repository = tmp_path / "repository"
    qualification = tmp_path / "qualification"
    predecessor = tmp_path / "archive" / "closeout.json"
    destination = tmp_path / "private-run"
    destination.mkdir()
    (destination / "unknown.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ClaimSupportV3CohortError, match="unknown files"):
        checked_cohort_run(repository, destination, qualification, predecessor)

    linked_parent = tmp_path / "linked-parent"
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked_parent or original_is_symlink(path),
    )
    with pytest.raises(ClaimSupportV3CohortError, match="symlink"):
        checked_cohort_run(
            repository,
            linked_parent / "child",
            qualification,
            predecessor,
        )

    with pytest.raises(ClaimSupportV3CohortError, match="unavailable"):
        load_verified_qualification(
            ROOT,
            predecessor,
            tmp_path / "missing-qualification",
        )


def test_invalid_authorization_timestamp_and_pending_preflight_are_explicit(
    plan,
    rehearsal,
    authorization,
):
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    tampered = {**authorization, "authorized_at": "not-a-timestamp"}
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

    unsynchronized = RepositoryExecutionState(
        branch="feature", head_commit=COMMIT, origin_main_commit="b" * 40, clean=False
    )
    preflight = build_preflight(
        ROOT,
        Path("/private/tmp/aletheia-v3-source-cohort-unit"),
        plan,
        rehearsal,
        None,
        repository_state=unsynchronized,
        credential_present=True,
    )
    assert preflight["live_blockers"] == [
        "authorization_pending",
        "repository_not_clean_synchronized_main",
    ]


def test_auxiliary_freeze_checks_reject_tokenizer_drift_and_replay_qualification(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(cohort.importlib.metadata, "version", lambda _name: "wrong")
    with pytest.raises(ClaimSupportV3CohortError, match="tokenizer differs"):
        source_request_projections(ROOT)

    authority = {"source_commit_ref": COMMIT}
    monkeypatch.setattr(
        "aletheia_lab.evaluation.claim_support_v3_cohort.read_document",
        lambda *_args: authority,
    )
    monkeypatch.setattr(
        "aletheia_lab.evaluation.claim_support_v3_cohort.build_qualification_plan",
        lambda *_args, **_kwargs: {"plan_sha256": "1" * 64},
    )
    monkeypatch.setattr(
        "aletheia_lab.evaluation.claim_support_v3_cohort.rehearse_qualification",
        lambda *_args: {"source_and_relation_boundaries_exercised": True},
    )
    assert qualification_rehearsal_is_stable(
        ROOT,
        tmp_path / "predecessor.json",
        tmp_path / "qualification",
    )

    invalid = tmp_path / "authorization.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(ClaimSupportV3CohortError, match="authorization is invalid"):
        load_authorization(invalid)
