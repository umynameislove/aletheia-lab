"""V3.2 relation qualification contracts and one-attempt execution tests."""

from __future__ import annotations

import copy
import json
import runpy
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.evaluation import claim_support_v3_2_qualification as qualification
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_support_v3_2_qualification import (
    REQUEST_COUNT,
    build_plan,
    build_protocol,
    build_relation_probes,
    outbound,
    prepare_requests,
    rehearse,
    relation_semantic_issue,
    verify_protocol,
)
from aletheia_lab.evaluation.claim_support_v3_2_qualification_execution import (
    execute,
    verify,
)
from aletheia_lab.evaluation.claim_validation_v3_design import FRAMES, build_probes
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


@pytest.fixture(scope="module")
def protocol() -> dict[str, object]:
    return build_protocol(ROOT)


def test_protocol_rebuilds_and_preserves_the_source_role_boundary(
    protocol: dict[str, object],
) -> None:
    assert verify_protocol(ROOT) == protocol
    assert protocol["status"] == "v3_2_relation_qualification_boundary_frozen"
    assert protocol["source_claim_instance_count"] == 720
    assert protocol["source_qualification_provider_request_count"] == 0
    assert protocol["relation_qualification_provider_request_count"] == 12
    assert protocol["prospective_relation_request_count"] == 240
    assert protocol["expected_relation_matrices_evaluator_only"] is True
    assert protocol["qualification_requires_all_parsed_and_exact"] is True
    assert protocol["qualification_success_authorizes_planning_only"] is True
    assert protocol["qualification_execution_authorized"] is False
    assert protocol["relation_planning_unlocked"] is False
    assert protocol["relation_execution_authorized"] is False
    assert protocol["claims_materialized"] is False
    assert protocol["blind_packets_generated"] is False


def test_relation_probe_census_is_exact_new_and_not_outbound() -> None:
    probes = build_relation_probes(ROOT)
    predecessor = [probe for probe in build_probes(ROOT) if probe["kind"] == "relation"]

    assert len(probes) == REQUEST_COUNT
    assert len({probe["probe_sha256"] for probe in probes}) == REQUEST_COUNT
    assert Counter(probe["condition"] for probe in probes) == dict.fromkeys(FRAMES, 3)
    assert all(probe["kind"] == "relation" for probe in probes)
    assert all(probe["variant"] == "FULL" for probe in probes)
    assert [probe["probe_sha256"] for probe in probes] != [
        probe["probe_sha256"] for probe in predecessor
    ]
    assert [
        {key: value for key, value in probe.items() if key != "probe_sha256"}
        for probe in probes
    ] == [
        {key: value for key, value in probe.items() if key != "probe_sha256"}
        for probe in predecessor
    ]
    for probe in probes:
        prompt, context = outbound(probe)
        wire = prompt + canonical_project_json(context.model_payload())
        assert canonical_project_json(probe["expected"]) not in wire
        for forbidden in (
            "Expected answer:",
            "automatic_label",
            "probe_sha256",
            "source_commit_ref",
        ):
            assert forbidden not in wire


def test_relation_semantics_reject_missing_duplicate_foreign_and_wrong_cells() -> None:
    probe = build_relation_probes(ROOT)[0]
    assert relation_semantic_issue(probe, probe["expected"]) is None

    mutations = []
    wrong = copy.deepcopy(probe["expected"])
    wrong["relations"][0]["relation"] = (
        "neutral" if wrong["relations"][0]["relation"] != "neutral" else "supports"
    )
    mutations.append((wrong, "relation_expected_matrix_mismatch"))
    missing = copy.deepcopy(probe["expected"])
    missing["relations"].pop()
    mutations.append((missing, "relation_matrix_invalid"))
    duplicate = copy.deepcopy(probe["expected"])
    duplicate["relations"][-1] = duplicate["relations"][0]
    mutations.append((duplicate, "relation_matrix_invalid"))
    foreign = copy.deepcopy(probe["expected"])
    foreign["relations"][0]["evidence_id"] = "ev-foreign"
    mutations.append((foreign, "relation_matrix_invalid"))

    for payload, expected_issue in mutations:
        assert relation_semantic_issue(probe, payload) == expected_issue


def test_plan_and_rehearsal_are_relation_only_and_cost_bounded() -> None:
    plan = build_plan(ROOT, source_commit=COMMIT)
    rehearsal = rehearse(plan, ROOT)

    assert plan["request_count"] == plan["relation_request_count"] == 12
    assert plan["source_request_count"] == 0
    assert plan["exact_message_input_token_count"] > 0
    assert plan["exact_response_schema_token_count"] > 0
    assert 0 < plan["estimated_upper_cost_usd"] < 0.5
    assert plan["provider_calls_executed"] is False
    assert rehearsal["probe_count"] == 12
    assert rehearsal["relation_frame_count"] == 4
    assert rehearsal["negative_semantic_mutations_rejected"] == 12
    assert rehearsal["expected_matrices_excluded_from_provider_messages"] is True


@pytest.fixture
def authorized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    monkeypatch.setattr(qualification, "inspect_repository_state", lambda _root: state)
    plan = build_plan(ROOT)
    run = qualification.checked_run(ROOT, tmp_path / "qualification")
    auth = qualification.authorize(ROOT, run, plan, 0.5)
    run.mkdir()
    qualification.publish(run / "authorization.json", auth)
    prepared = prepare_requests(ROOT, plan, auth)
    probes = build_relation_probes(ROOT)
    payloads = {
        request.request.initial_attempt.request_identity_sha256: copy.deepcopy(probe["expected"])
        for request, probe in zip(prepared, probes, strict=True)
    }
    return run, plan, auth, prepared, payloads


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


def test_prepared_requests_are_twelve_new_relation_only_wire_messages(authorized) -> None:
    _, plan, _, prepared, payloads = authorized
    assert len(prepared) == 12
    assert len(
        {request.request.initial_attempt.request_identity_sha256 for request in prepared}
    ) == 12

    request = prepared[0].request
    expected = payloads[request.initial_attempt.request_identity_sha256]
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            id="qualification-mocked-response",
            model=qualification.MODEL_SNAPSHOT,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=canonical_project_json(expected), refusal=None
                    ),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

    adapter = OpenAIValidationV2Adapter(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        model_policy=request.initial_attempt.model_policy,
        policy=qualification._openai_policy(ROOT),
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
    assert wire["model"] == qualification.MODEL_SNAPSHOT
    assert wire["max_tokens"] == 2048
    assert wire["response_format"]["json_schema"]["strict"] is True
    messages = json.dumps(wire["messages"])
    for forbidden in (
        "expected",
        "probe_sha256",
        "automatic_label",
        plan["protocol_sha256"],
    ):
        assert forbidden not in messages


def test_all_exact_results_unlock_planning_but_not_relation_execution(authorized) -> None:
    run, plan, auth, prepared, payloads = authorized
    adapter = FakeAdapter(prepared, payloads)
    receipt = execute(
        ROOT,
        run,
        plan,
        auth,
        confirmation=auth["authorization_sha256"],
        adapter=adapter,
        retry=V2RetryController(sleep=lambda _: None),
    )

    assert adapter.calls == 12
    assert receipt["terminal_request_count"] == 12
    assert receipt["parsed_count"] == receipt["accepted_count"] == 12
    assert receipt["technical_failure_count"] == 0
    assert receipt["semantic_failure_count"] == 0
    assert receipt["relation_planning_unlocked"] is True
    assert receipt["relation_execution_authorized"] is False
    assert receipt["claims_materialized"] is False
    assert receipt["blind_packets_generated"] is False
    before = {path.relative_to(run): path.read_bytes() for path in run.rglob("*") if path.is_file()}
    assert verify(ROOT, run, plan, auth) == receipt
    assert before == {
        path.relative_to(run): path.read_bytes() for path in run.rglob("*") if path.is_file()
    }
    with pytest.raises(ValueError, match="already registered"):
        execute(
            ROOT,
            run,
            plan,
            auth,
            confirmation=auth["authorization_sha256"],
            adapter=adapter,
        )
    assert adapter.calls == 12


def test_any_technical_or_semantic_failure_blocks_planning(authorized) -> None:
    run, plan, auth, prepared, payloads = authorized
    failed = prepared[0].request.initial_attempt.request_identity_sha256
    semantic = prepared[1].request.initial_attempt.request_identity_sha256
    payloads[semantic]["relations"][0]["relation"] = (
        "neutral"
        if payloads[semantic]["relations"][0]["relation"] != "neutral"
        else "supports"
    )
    adapter = FakeAdapter(prepared, payloads, fail=failed)
    receipt = execute(
        ROOT,
        run,
        plan,
        auth,
        confirmation=auth["authorization_sha256"],
        adapter=adapter,
        retry=V2RetryController(sleep=lambda _: None),
    )

    assert receipt["parsed_count"] == 11
    assert receipt["accepted_count"] == 10
    assert receipt["technical_failure_count"] == 1
    assert receipt["semantic_failure_count"] == 1
    assert receipt["semantic_issue_counts"] == {
        "relation_expected_matrix_mismatch": 1
    }
    assert receipt["relation_planning_unlocked"] is False
    assert receipt["relation_execution_authorized"] is False
    assert verify(ROOT, run, plan, auth) == receipt


def test_plan_authority_tamper_and_confirmation_fail_before_provider(authorized) -> None:
    run, plan, auth, prepared, payloads = authorized
    adapter = FakeAdapter(prepared, payloads)
    for value in (True, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="budget"):
            qualification.authorize(ROOT, run.parent / "invalid-budget", plan, value)
    changed_plan = qualification.seal(
        {
            **{key: value for key, value in plan.items() if key != "plan_sha256"},
            "request_count": 11,
        },
        "plan_sha256",
    )
    with pytest.raises(ValueError, match="frozen inputs"):
        execute(
            ROOT,
            run,
            changed_plan,
            auth,
            confirmation=auth["authorization_sha256"],
            adapter=adapter,
        )
    altered_auth = qualification.seal(
        {
            **{
                key: value
                for key, value in auth.items()
                if key != "authorization_sha256"
            },
            "relation_execution_authorized": True,
        },
        "authorization_sha256",
    )
    with pytest.raises(ValueError, match="authority"):
        execute(
            ROOT,
            run,
            plan,
            altered_auth,
            confirmation=altered_auth["authorization_sha256"],
            adapter=adapter,
        )
    with pytest.raises(ValueError, match="confirmation"):
        execute(ROOT, run, plan, auth, confirmation="0" * 64, adapter=adapter)
    assert adapter.calls == 0
    assert not (run / "lease.json").exists()


def test_run_destination_rejects_repository_overlap_and_unknown_entries(
    tmp_path: Path,
) -> None:
    for target in (ROOT, ROOT / "artifact", ROOT.parent):
        with pytest.raises(ValueError, match="overlap"):
            qualification.checked_run(ROOT, target)
    run = tmp_path / "qualification"
    run.mkdir()
    (run / "foreign.txt").write_text("not allowed")
    with pytest.raises(ValueError, match="unknown"):
        qualification.checked_run(ROOT, run)


def test_operator_cli_preflight_execute_and_verify(authorized, monkeypatch, capsys) -> None:
    run, plan, _, _, _ = authorized
    destination = run.parent / "cli-qualification"
    namespace = runpy.run_path(
        str(ROOT / "scripts/claim_support_validation_v3_2_qualification.py")
    )
    main = namespace["main"]
    adapters = []

    def adapter_factory(root, current_plan, auth):
        prepared = prepare_requests(root, current_plan, auth)
        probes = build_relation_probes(root)
        payloads = {
            request.request.initial_attempt.request_identity_sha256: copy.deepcopy(
                probe["expected"]
            )
            for request, probe in zip(prepared, probes, strict=True)
        }
        adapter = FakeAdapter(prepared, payloads)
        adapters.append(adapter)
        return adapter

    monkeypatch.setitem(main.__globals__, "adapter_for", adapter_factory)

    def invoke(command, *extra):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "qualification",
                command,
                "--root",
                str(ROOT),
                "--run-dir",
                str(destination),
                *extra,
            ],
        )
        result = main()
        return result, json.loads(capsys.readouterr().out)

    rehearsal = qualification.rehearse(plan, ROOT)
    code, auth = invoke(
        "authorize",
        "--cost-ceiling-usd",
        "0.5",
        "--confirm-plan-sha256",
        plan["plan_sha256"],
        "--confirm-rehearsal-sha256",
        rehearsal["rehearsal_sha256"],
    )
    assert code == 0
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code, error = invoke("require-live-ready")
    assert code == 2 and error["blocker_code"] == "credential_absent"
    assert not (destination / "lease.json").exists()
    monkeypatch.setenv("OPENAI_API_KEY", "test-credential-never-render")
    code, ready = invoke("require-live-ready")
    assert code == 0 and ready["request_count"] == 12
    assert "test-credential" not in json.dumps(ready)
    code, receipt = invoke(
        "execute", "--confirm-authorization-sha256", auth["authorization_sha256"]
    )
    assert code == 0 and receipt["accepted_count"] == 12
    code, rebuilt = invoke("verify")
    assert code == 0 and rebuilt == receipt
    assert sum(adapter.calls for adapter in adapters) == 12
