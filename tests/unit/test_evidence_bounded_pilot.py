"""The private paid path is rehearsed with fake SDK calls and no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_evidence_bounded_policy import context, decision

from aletheia_lab.evaluation.evidence_bounded_pilot import (
    _check_call_resources,
    _requests,
    checked_policy_plan,
    execute_policy_pilot,
    prepare_case_frame,
    verify_policy_pilot,
)
from aletheia_lab.evaluation.evidence_bounded_policy import (
    PROMPTS,
    compatibility_reference,
    deterministic_decision,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD
from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller


def cases():
    return [
        {
            "pair_id": "synthetic-pair",
            "source_cluster": "synthetic-source",
            "model_kind": "fixture",
            "dose": 1,
            "truth": "target_binding" if rival else "score_mapping",
            "condition": condition,
            "context": visible,
            "reference": compatibility_reference(visible),
        }
        for rival in (False, True)
        for condition in ("full", "missing_key", "noisy", "misleading")
        for visible in [context(condition, rival=rival)]
    ]


class FixtureCaller:
    def __init__(self, malformed=False):
        self.calls = []
        self.malformed = malformed

    def invoke(self, *, prompt, payload, schema):
        self.calls.append((prompt, payload, schema))
        output = deterministic_decision(payload["visible_context"])
        return DevelopmentCall(
            status="completed",
            payload_json="{}" if self.malformed else output.model_dump_json(),
            input_tokens=400,
            output_tokens=30,
            estimated_cost_usd=0.00104,
            latency_seconds=0.5,
        )


def test_complete_offline_lifecycle_and_wire_deduplication(tmp_path):
    directory = tmp_path / "pilot"
    report = prepare_case_frame(
        cases=cases(), source_hashes={"synthetic": "a" * 64}, directory=directory
    )
    assert report["provider_calls_executed"] == 0
    assert report["maximum_provider_calls"] == 14  # one ambiguous context, not two hidden worlds
    caller = FixtureCaller()
    receipt = execute_policy_pilot(
        directory=directory, confirm_sha256=report["plan_sha256"], caller=caller
    )
    assert receipt["parsed_unique_requests"] == 14
    assert verify_policy_pilot(directory=directory)["verification"] == "pass"
    assert set(payload for payload in [tuple(call[1]) for call in caller.calls]) == {
        ("visible_context",)
    }
    requests = _requests(cases())
    for digest in {request["context_sha256"] for request in requests}:
        pair = [r for r in requests if r["context_sha256"] == digest]
        assert len(pair) == 2 and pair[0]["payload"] == pair[1]["payload"]
    with pytest.raises(ValueError, match="already exists"):
        execute_policy_pilot(
            directory=directory, confirm_sha256=report["plan_sha256"], caller=caller
        )
    assert len(caller.calls) == 14


def test_wrong_plan_and_tampered_reference_fail_before_a_call(tmp_path):
    directory = tmp_path / "pilot"
    report = prepare_case_frame(cases=cases(), source_hashes={}, directory=directory)
    caller = FixtureCaller()
    with pytest.raises(ValueError, match="digest"):
        execute_policy_pilot(directory=directory, confirm_sha256="f" * 64, caller=caller)
    assert not caller.calls and not (directory / "lease.json").exists()
    bad = cases()
    bad[0]["reference"] = {"status": "ambiguous", "compatible": []}
    with pytest.raises(ValueError, match="reference"):
        prepare_case_frame(cases=bad, source_hashes={}, directory=tmp_path / "bad")
    assert checked_policy_plan(directory)[0]["case_frame_sha256"] == canonical_execution_sha256(
        cases()
    )
    assert report["status"] == "offline_policy_preflight_pass"


def test_three_invalid_responses_stop_without_replay_or_safe_abstention(tmp_path):
    directory = tmp_path / "pilot"
    report = prepare_case_frame(cases=cases(), source_hashes={}, directory=directory)
    caller = FixtureCaller(malformed=True)
    receipt = execute_policy_pilot(
        directory=directory, confirm_sha256=report["plan_sha256"], caller=caller
    )
    assert receipt["status"] == "pilot_stopped_technical" and len(caller.calls) == 3
    assert verify_policy_pilot(directory=directory)["parsed_unique_requests"] == 0
    analysis = json.loads((directory / "analysis.json").read_text())
    assert analysis["arms"]["a4_bounded"]["unsupported_ambiguous_commitment_rate"] is None
    assert analysis["completed_unique_requests"] == 3
    assert analysis["not_executed_unique_requests"] == 11
    requests = _requests(cases())
    called = {r["request_id"] for r in requests[:3]}
    for policy in ("a3_matched", "a4_bounded"):
        arm = analysis["arms"][policy]
        executed_views = sum(
            canonical_execution_sha256([policy, canonical_execution_sha256(c["context"])]) in called
            for c in cases()
        )
        assert arm["scheduled_views"] == 8
        assert arm["executed_views"] == executed_views
        assert arm["provider_attempted_views"] == executed_views
        assert arm["not_executed_views"] == 8 - executed_views
        assert arm["technical_or_schema_failed_views"] == executed_views
        assert arm["full_all_planned_resolution_rate"] == 0
        assert arm["ambiguous_all_planned_bounded_response_rate"] == 0


def test_actual_existing_sdk_caller_keeps_both_policies_matched_and_bounded():
    captured = []

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=400, completion_tokens=30),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(refusal=None, content=decision().model_dump_json()),
                    )
                ],
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    caller = OpenAIDevelopmentCaller(maximum_calls=2, client=client)
    request_pair = _requests(cases())[:2]
    for request in request_pair:
        caller.invoke(
            prompt=PROMPTS[request["policy"]], payload=request["payload"], schema=request_schema()
        )
    assert len(captured) == 2
    left, right = captured
    assert left["messages"][1] == right["messages"][1]
    assert left["messages"][0] != right["messages"][0]
    assert {k: v for k, v in left.items() if k != "messages"} == {
        k: v for k, v in right.items() if k != "messages"
    }
    assert left["model"] == "gpt-4.1-2025-04-14" and left["store"] is False
    assert left["max_tokens"] == 1024 and left["temperature"] == 0
    with pytest.raises(ValueError, match="ceiling"):
        caller.invoke(prompt="extra", payload={}, schema=request_schema())


def request_schema():
    from aletheia_lab.evaluation.evidence_bounded_policy import decision_schema

    return decision_schema()


def test_result_tampering_is_detected_without_network(tmp_path):
    directory = tmp_path / "pilot"
    report = prepare_case_frame(cases=cases(), source_hashes={}, directory=directory)
    execute_policy_pilot(
        directory=directory, confirm_sha256=report["plan_sha256"], caller=FixtureCaller()
    )
    path = directory / "analysis.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="replay differs"):
        verify_policy_pilot(directory=directory)


@pytest.mark.parametrize("provider_attempted", [True, False])
def test_unknown_usage_is_reserved_and_local_rejection_is_not_charged(tmp_path, provider_attempted):
    class FailedCaller:
        def invoke(self, **kwargs):
            return DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=8192 if provider_attempted else 0,
                output_tokens=1024 if provider_attempted else 0,
                estimated_cost_usd=MAX_CALL_USD if provider_attempted else 0,
                latency_seconds=1,
                usage_observed=False,
                provider_attempted=provider_attempted,
            )

    directory = tmp_path / "pilot"
    prepared = prepare_case_frame(cases=cases(), source_hashes={}, directory=directory)
    receipt = execute_policy_pilot(
        directory=directory, confirm_sha256=prepared["plan_sha256"], caller=FailedCaller()
    )
    assert receipt["status"] == "pilot_stopped_technical"
    assert receipt["provider_attempt_count"] == (3 if provider_attempted else 0)
    assert receipt["committed_cost_usd_at_frozen_rates"] == (
        round(3 * MAX_CALL_USD, 6) if provider_attempted else 0
    )
    assert verify_policy_pilot(directory=directory)["verification"] == "pass"
    analysis = json.loads((directory / "analysis.json").read_text())
    assert sum(v["executed_views"] for v in analysis["arms"].values()) > 16


@pytest.mark.parametrize("changed", [{"input_tokens": 8193}, {"estimated_cost_usd": 0}])
def test_invalid_resource_accounting_cannot_silently_pass(changed):
    call = DevelopmentCall(
        status="completed",
        payload_json="{}",
        input_tokens=400,
        output_tokens=30,
        estimated_cost_usd=0.00104,
        latency_seconds=0.5,
    )
    with pytest.raises(ValueError, match="reservation|cost"):
        _check_call_resources(call.model_copy(update=changed))


def test_incomplete_pair_and_plan_drift_block_before_provider(tmp_path, monkeypatch):
    from aletheia_lab.evaluation import evidence_bounded_pilot as pilot

    with pytest.raises(ValueError, match="complete pair"):
        prepare_case_frame(cases=cases()[:-1], source_hashes={}, directory=tmp_path / "partial")
    with pytest.raises(ValueError, match="duplicated"):
        prepare_case_frame(
            cases=cases() + cases()[:1], source_hashes={}, directory=tmp_path / "dup"
        )
    directory = tmp_path / "pilot"
    report = prepare_case_frame(cases=cases(), source_hashes={}, directory=directory)
    monkeypatch.setattr(pilot, "_code_identity", lambda: {"changed.py": "f" * 64})
    caller = FixtureCaller()
    with pytest.raises(ValueError, match="runtime code changed"):
        execute_policy_pilot(
            directory=directory, confirm_sha256=report["plan_sha256"], caller=caller
        )
    assert not caller.calls and not (directory / "lease.json").exists()
