"""V3.2 relation cohort design, authority, and execution tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.evaluation import claim_support_v3_2_relation_cohort as cohort
from aletheia_lab.evaluation import claim_support_v3_2_relation_cohort_execution as execution
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort import (
    REQUEST_COUNT,
    build_plan,
    build_protocol,
    build_relation_tasks,
    outbound,
    prepare_requests,
    rehearse,
    relation_semantic_issue,
    verify_protocol,
)
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort_execution import (
    execute,
    verify,
)
from aletheia_lab.evaluation.claim_validation_v3_design import FRAMES, structural_relations
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_sha256 as digest,
)
from aletheia_lab.model_gateway import (
    AdapterInvocationError,
    OpenAIValidationV2Adapter,
    ProviderBinding,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
    V2RetryController,
    execute_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
QUALIFICATION = {
    "qualification_authorization_sha256": "1" * 64,
    "qualification_receipt_sha256": "2" * 64,
    "qualification_terminal_store_sha256": "3" * 64,
    "qualification_source_commit_ref": "4" * 40,
}


def _expected(task):
    _, context = outbound(task)
    return structural_relations(task["claim"], context)


def test_protocol_and_task_schedule_preserve_the_prospective_design() -> None:
    protocol = build_protocol(ROOT)
    tasks = build_relation_tasks(ROOT)

    assert verify_protocol(ROOT) == protocol
    assert protocol["status"] == "v3_2_relation_cohort_boundary_frozen"
    assert protocol["relation_provider_request_count"] == len(tasks) == REQUEST_COUNT
    assert protocol["source_provider_request_count"] == 0
    assert protocol["qualification_requires_passed_12_of_12_receipt"] is True
    assert protocol["qualification_outcomes_may_not_adapt_cohort"] is True
    assert protocol["cohort_semantic_gate_is_structural_only"] is True
    assert protocol["evaluator_structural_capacity_counts"] == {
        "contradicted": 60,
        "fully_supported": 60,
        "partially_supported": 60,
        "unsupported": 60,
    }
    assert len({task["source_instance_sha256"] for task in tasks}) == 240
    assert len({task["claim"]["claim_text"] for task in tasks}) == 240
    for index in range(0, 240, 4):
        assert {task["frame"] for task in tasks[index : index + 4]} == set(FRAMES)


def test_provider_surface_excludes_design_strata_and_evaluator_matrix() -> None:
    task = build_relation_tasks(ROOT)[0]
    prompt, context = outbound(task)
    expected = _expected(task)
    wire = canonical_project_json(
        {"prompt": prompt, "visible_evidence": context.model_payload()}
    )

    assert canonical_project_json(expected) not in wire
    for forbidden in (
        "assignment_ordinal",
        "execution_ordinal",
        "source_instance_sha256",
        "source_variant",
        "family_id",
        "evidence_condition",
        "task_sha256",
        "automatic_label",
    ):
        assert forbidden not in wire


def test_structural_errors_rejected_but_substantive_judgment_preserved() -> None:
    task = build_relation_tasks(ROOT)[0]
    expected = _expected(task)
    assert relation_semantic_issue(task, expected) is None

    missing = copy.deepcopy(expected)
    missing["relations"].pop()
    assert relation_semantic_issue(task, missing) == "relation_matrix_invalid"

    duplicate = copy.deepcopy(expected)
    duplicate["relations"][-1] = duplicate["relations"][0]
    assert relation_semantic_issue(task, duplicate) == "relation_matrix_invalid"

    changed = copy.deepcopy(expected)
    changed["relations"][0]["relation"] = (
        "neutral"
        if changed["relations"][0]["relation"] != "neutral"
        else "supports"
    )
    assert relation_semantic_issue(task, changed) is None


def test_qualification_gate_requires_independent_exact_12_of_12_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = {"source_commit_ref": COMMIT, "authorization_sha256": "1" * 64}
    receipt = {
        "status": "v3_2_relation_qualification_passed",
        "terminal_request_count": 12,
        "parsed_count": 12,
        "accepted_count": 12,
        "technical_failure_count": 0,
        "semantic_failure_count": 0,
        "relation_planning_unlocked": True,
        "rerun_forbidden": True,
        "receipt_sha256": "2" * 64,
        "terminal_store_sha256": "3" * 64,
        "source_commit_ref": COMMIT,
    }
    monkeypatch.setattr(cohort, "checked_qualification_run", lambda *_args: tmp_path)
    monkeypatch.setattr(cohort, "read_document", lambda *_args: auth)
    monkeypatch.setattr(cohort, "build_qualification_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        cohort, "verify_qualification_execution", lambda *_args: receipt
    )

    evidence = cohort.qualification_evidence(ROOT, tmp_path)
    assert evidence["qualification_receipt_sha256"] == "2" * 64
    receipt["accepted_count"] = 11
    with pytest.raises(ValueError, match="12 of 12"):
        cohort.qualification_evidence(ROOT, tmp_path)


@pytest.fixture
def authorized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    monkeypatch.setattr(cohort, "inspect_repository_state", lambda _root: state)
    monkeypatch.setattr(
        cohort, "qualification_evidence", lambda _root, _run: QUALIFICATION
    )
    qualification_run = tmp_path / "qualification"
    run = cohort.checked_run(ROOT, tmp_path / "cohort", qualification_run)
    plan = build_plan(ROOT, qualification_run)
    auth = cohort.authorize(ROOT, run, qualification_run, plan, 10.0)
    run.mkdir()
    cohort.publish(run / "authorization.json", auth)
    return run, qualification_run, plan, auth, prepare_requests(ROOT, plan, auth), build_relation_tasks(ROOT)


def test_plan_rehearsal_and_requests_are_exact_and_cost_bounded(authorized) -> None:
    _, _, plan, auth, prepared, tasks = authorized
    rehearsal = rehearse(plan, ROOT)

    assert plan["request_count"] == plan["relation_request_count"] == 240
    assert plan["source_request_count"] == 0
    assert plan["qualification_receipt_sha256"] == "2" * 64
    assert 0 < plan["estimated_upper_cost_usd"] < 10.0
    assert rehearsal["structural_negative_mutations_rejected"] == 240
    assert rehearsal["substantive_judgment_mutations_preserved"] == 240
    assert len(prepared) == len(tasks) == 240
    assert len({item.request.initial_attempt.request_identity_sha256 for item in prepared}) == 240
    assert auth["relation_execution_authorized"] is True
    assert auth["relation_closeout_unlocked"] is False
    assert auth["claims_materialized"] is False
    assert auth["blind_packets_generated"] is False


def test_openai_wire_uses_qualified_relation_transport(authorized) -> None:
    _, _, _, _, prepared, tasks = authorized
    request = prepared[0].request
    expected = _expected(tasks[0])
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            id="cohort-mocked-response",
            model=cohort.MODEL_SNAPSHOT,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content=canonical_project_json(expected), refusal=None
            ))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

    adapter = OpenAIValidationV2Adapter(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model_policy=request.initial_attempt.model_policy,
        policy=cohort._openai_policy(ROOT),
    )
    result = execute_gateway_request(
        request,
        adapter=adapter,
        clock=SystemMonotonicClock(),
        cancellation=NeverCancelled(),
        retry_controller=V2RetryController(sleep=lambda _: None),
    )

    assert result.status == "parsed"
    assert len(calls) == 1
    wire = calls[0]
    assert wire["model"] == cohort.MODEL_SNAPSHOT
    assert wire["max_tokens"] == 2048
    assert wire["response_format"]["json_schema"]["strict"] is True
    messages = json.dumps(wire["messages"])
    for forbidden in ("source_variant", "frame", "automatic_label", "expected"):
        assert forbidden not in messages


class FakeAdapter:
    def __init__(self, prepared, payloads, fail=None):
        self.binding = ProviderBinding.from_model_policy(
            prepared[0].request.initial_attempt.model_policy
        )
        self.payloads = payloads
        self.fail = fail
        self.calls = 0

    def invoke(self, call):
        self.calls += 1
        if call.request_identity_sha256 == self.fail:
            raise AdapterInvocationError(
                code="permanent_provider_error",
                retryable=False,
                provider_attempt_ref="ev-" + "f" * 64,
                provider_failure_category="request_rejected",
            )
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref="ev-" + digest(call.attempt_id),
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(
                canonical_project_json(self.payloads[call.request_identity_sha256]).encode()
            ),
            usage=UsageMetadata(
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def test_single_use_execution_preserves_failures_without_interpreting_outcomes(
    authorized, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, qualification_run, plan, auth, prepared, tasks = authorized
    prepared = prepared[:4]
    tasks = tasks[:4]
    monkeypatch.setattr(execution, "REQUEST_COUNT", 4)
    monkeypatch.setattr(execution, "prepare_requests", lambda *_args: prepared)
    monkeypatch.setattr(execution, "build_relation_tasks", lambda _root: tasks)
    payloads = {
        item.request.initial_attempt.request_identity_sha256: _expected(task)
        for item, task in zip(prepared, tasks, strict=True)
    }
    failed_identity = prepared[0].request.initial_attempt.request_identity_sha256
    malformed_identity = prepared[1].request.initial_attempt.request_identity_sha256
    payloads[malformed_identity]["relations"][-1] = payloads[malformed_identity][
        "relations"
    ][0]
    adapter = FakeAdapter(prepared, payloads, fail=failed_identity)
    receipt = execute(
        ROOT,
        run,
        qualification_run,
        plan,
        auth,
        confirmation=auth["authorization_sha256"],
        adapter=adapter,
        retry=V2RetryController(sleep=lambda _: None),
    )

    assert adapter.calls == 4
    assert receipt["status"] == "v3_2_relation_cohort_execution_complete_with_failures"
    assert receipt["terminal_request_count"] == 4
    assert receipt["parsed_count"] == 3
    assert receipt["structurally_accepted_count"] == 2
    assert receipt["technical_failure_count"] == 1
    assert receipt["semantic_failure_count"] == 1
    assert receipt["semantic_issue_counts"] == {"relation_matrix_invalid": 1}
    assert receipt["failures_preserved_in_denominator"] is True
    assert receipt["relation_closeout_unlocked"] is True
    assert receipt["relation_outcomes_interpreted"] is False
    assert receipt["automatic_labels_generated"] is False
    assert receipt["claims_materialized"] is False
    assert receipt["blind_packets_generated"] is False
    assert verify(ROOT, run, qualification_run, plan, auth) == receipt
    with pytest.raises(ValueError, match="already registered"):
        execute(
            ROOT,
            run,
            qualification_run,
            plan,
            auth,
            confirmation=auth["authorization_sha256"],
            adapter=adapter,
        )
    assert adapter.calls == 4


def test_authority_and_confirmation_tamper_fail_before_provider(authorized) -> None:
    run, qualification_run, plan, auth, prepared, _ = authorized
    adapter = FakeAdapter(prepared, {})
    changed = cohort.seal(
        {
            **{key: value for key, value in auth.items() if key != "authorization_sha256"},
            "qualification_receipt_sha256": "9" * 64,
        },
        "authorization_sha256",
    )
    with pytest.raises(ValueError, match="authority"):
        execute(
            ROOT,
            run,
            qualification_run,
            plan,
            changed,
            confirmation=changed["authorization_sha256"],
            adapter=adapter,
        )
    with pytest.raises(ValueError, match="confirmation"):
        execute(
            ROOT,
            run,
            qualification_run,
            plan,
            auth,
            confirmation="0" * 64,
            adapter=adapter,
        )
    assert adapter.calls == 0
    assert not (run / "lease.json").exists()


def test_run_destination_rejects_overlap_and_unknown_entries(tmp_path: Path) -> None:
    qualification_run = tmp_path / "qualification"
    for target in (ROOT, ROOT / "artifact", ROOT.parent, qualification_run):
        with pytest.raises(ValueError, match="overlaps"):
            cohort.checked_run(ROOT, target, qualification_run)
    run = tmp_path / "cohort"
    run.mkdir()
    (run / "foreign.txt").write_text("not allowed")
    with pytest.raises(ValueError, match="unknown"):
        cohort.checked_run(ROOT, run, qualification_run)
