"""Reuse charged responses and continue uncalled requests without network access."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evidence_bounded_pilot import FixtureCaller, cases
from test_evidence_bounded_policy import context, decision

from aletheia_lab.evaluation import evidence_bounded_continuation as continuation
from aletheia_lab.evaluation.evidence_bounded_decoding import decode_policy_call
from aletheia_lab.evaluation.evidence_bounded_pilot import (
    _requests,
    execute_policy_pilot,
    prepare_case_frame,
    verify_policy_pilot,
)
from aletheia_lab.evaluation.evidence_bounded_policy import CAUSES, PROMPTS, decision_schema
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD
from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller
from aletheia_lab.evidence.schema import sha256_text


def bounded_payload(action="abstain", candidates=None, measurement="column_and_target_provenance"):
    return {
        "action": action,
        "candidates": list(CAUSES) if candidates is None else candidates,
        "causal_claims": [],
        "reason": "insufficient",
        "measurement": measurement,
        "cited_fields": ["performance-comparison", "score-source-controls"],
    }


def call(payload):
    return DevelopmentCall(
        status="completed",
        payload_json=json.dumps(payload),
        input_tokens=400,
        output_tokens=30,
        estimated_cost_usd=0.00104,
        latency_seconds=0.5,
    )


@pytest.mark.parametrize("action", ["abstain", "next_measurement"])
@pytest.mark.parametrize("reverse", [False, True])
def test_both_observed_patterns_preserve_action_candidate_set_and_proposed_check(action, reverse):
    raw = bounded_payload(action, list(reversed(CAUSES)) if reverse else list(CAUSES))
    saved_call = call(raw)
    original = saved_call.model_dump_json()
    decoded = decode_policy_call(saved_call)
    assert decoded.decision is not None and not decoded.strict_valid
    assert decoded.decision.action == action
    assert decoded.original_candidates == tuple(raw["candidates"])
    assert decoded.original_measurement == "column_and_target_provenance"
    assessed = decoded.assessment(context("missing_key"))
    assert assessed["bounded_ambiguous_response"] and not assessed["commitments"]
    assert assessed["claimed_candidates"] == raw["candidates"]
    assert assessed["proposed_measurement"] and not assessed["valid_resolution"]
    assert decoded.audit(saved_call)["response_sha256"] == sha256_text(saved_call.payload_json)
    assert saved_call.model_dump_json() == original
    # The response-only rule cannot use the hidden answer or missing-key condition.
    full = decoded.assessment(context("full"))
    assert not full["valid_resolution"] and not full["bounded_ambiguous_response"]
    assert full["redundant_measurement"]


def test_already_valid_decisions_are_not_projected_or_semantically_repaired():
    for output in (decision(), decision("abstain", (), reason="insufficient", citations=())):
        decoded = decode_policy_call(call(output.model_dump(mode="json")))
        assert decoded.strict_valid and decoded.decision == output and not decoded.projected_fields
    proposed = decode_policy_call(call(bounded_payload(candidates=[])))
    assert proposed.decision is not None and proposed.projected_fields == ("measurement",)


@pytest.mark.parametrize(
    "change",
    [
        {"candidates": ["score_mapping"]},
        {"candidates": ["score_mapping", "score_mapping"]},
        {"candidates": ["new_fault", "target_binding"]},
        {"causal_claims": ["score_mapping"]},
        {"reason": "conflict"},
        {"action": "singleton"},
        {"action": "candidate_set"},
        {"action": "next_measurement", "measurement": "none"},
        {"cited_fields": ["private-source-hash"]},
        {"cited_fields": ["performance-comparison", "performance-comparison"]},
        {"rationale": "hidden definitive claim"},
        {"candidates": "score_mapping"},
        {"measurement": "performed_check"},
    ],
)
def test_narrow_decoder_does_not_erase_assertions_or_hide_malformed_fields(change):
    decoded = decode_policy_call(call({**bounded_payload(), **change}))
    # A candidate_set with both causes is valid under the original grammar if
    # measurement is none; the proposed-check form in this fixture is not.
    assert decoded.decision is None and not decoded.strict_valid


def test_local_decoding_never_removes_an_unsafe_but_strictly_valid_claim():
    unsafe = decision("abstain", (), claims=("score_mapping",), reason="insufficient", citations=())
    decoded = decode_policy_call(call(unsafe.model_dump(mode="json")))
    assessed = decoded.assessment(context("missing_key"))
    assert decoded.strict_valid and assessed["unsupported_commitment"]
    assert not assessed["bounded_ambiguous_response"]
    assert assessed["commitments"] == ["score_mapping"]


@pytest.mark.parametrize(
    "payload", ["{", "[]", "null", "{}", '{"action":"abstain","action":"singleton"}']
)
def test_malformed_and_duplicate_json_is_not_safe_abstention(payload):
    saved_call = call({}).model_copy(update={"payload_json": payload})
    assert decode_policy_call(saved_call).decision is None
    assert not decode_policy_call(saved_call).assessment(context("missing_key"))["assessable"]


class StoppedCaller(FixtureCaller):
    def invoke(self, *, prompt, payload, schema):
        valid = super().invoke(prompt=prompt, payload=payload, schema=schema)
        return valid if len(self.calls) <= 2 else call(bounded_payload())


def stopped_pilot(tmp_path):
    predecessor = tmp_path / "predecessor"
    prepared = prepare_case_frame(cases=cases(), source_hashes={}, directory=predecessor)
    receipt = execute_policy_pilot(
        directory=predecessor, confirm_sha256=prepared["plan_sha256"], caller=StoppedCaller()
    )
    assert receipt["completed_unique_requests"] == 5 and receipt["parsed_unique_requests"] == 2
    return predecessor, tmp_path / "continuation"


def prepare(tmp_path):
    predecessor, directory = stopped_pilot(tmp_path)
    report = continuation.prepare_policy_continuation(predecessor=predecessor, directory=directory)
    assert report["reused_unique_requests"] == 5 and report["maximum_provider_calls"] == 9
    assert report["semantic_assessable_unique_requests"] == 5
    assert report["decoded_noncanonical_unique_requests"] == 3
    assert report["maximum_additional_reserved_cost_usd"] == round(9 * MAX_CALL_USD, 6)
    return predecessor, directory, report


def test_continuation_reuses_original_results_and_only_calls_remaining_exact_wire(tmp_path):
    predecessor, directory, report = prepare(tmp_path)
    original = continuation._manifest(predecessor)
    caller = FixtureCaller()
    progress = []
    result = continuation.execute_policy_continuation(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=caller,
        progress=progress.append,
    )
    assert len(caller.calls) == 9 and len(progress) == 9
    assert result["completed_unique_requests"] == 14 and result["new_unique_requests"] == 9
    assert result["reused_unique_requests"] == 5 and result["provider_attempt_count"] == 14
    assert result["semantic_assessable_unique_requests"] == 14
    assert result["strict_parsed_unique_requests"] == 11
    assert result["combined_committed_cost_usd_at_frozen_rates"] == round(14 * 0.00104, 6)
    assert continuation._manifest(predecessor) == original
    assert verify_policy_pilot(directory=predecessor)["status"] == "pilot_stopped_technical"
    for request, actual in zip(_requests(cases())[5:], caller.calls, strict=True):
        assert actual[0] == PROMPTS[request["policy"]]
        assert json.dumps(actual[1], sort_keys=True) == json.dumps(
            request["payload"], sort_keys=True
        )
        assert actual[2] == decision_schema()
    assert (
        continuation.verify_policy_continuation(predecessor=predecessor, directory=directory)[
            "verification"
        ]
        == "pass"
    )
    with pytest.raises(ValueError, match="already exists"):
        continuation.execute_policy_continuation(
            predecessor=predecessor,
            directory=directory,
            confirm_sha256=report["plan_sha256"],
            caller=caller,
        )
    assert len(caller.calls) == 9


def test_recoverable_nonanswers_do_not_trigger_the_failure_stop(tmp_path):
    predecessor, directory, report = prepare(tmp_path)

    class BoundedCaller(FixtureCaller):
        def invoke(self, **kwargs):
            super().invoke(**kwargs)
            return call(bounded_payload())

    caller = BoundedCaller()
    receipt = continuation.execute_policy_continuation(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=caller,
    )
    assert receipt["status"] == "continuation_terminalized" and len(caller.calls) == 9
    assert receipt["strict_parsed_unique_requests"] == 2
    assert receipt["decoded_noncanonical_unique_requests"] == 12
    assert receipt["semantic_assessable_unique_requests"] == 14
    analysis = json.loads((directory / "analysis.json").read_bytes())
    assert not analysis["a4_superiority_established"]
    assert analysis["strict_contract"] != analysis["supplementary_semantic_decoding"]


def test_three_unrecoverable_failures_stop_and_unknown_usage_remains_reserved(tmp_path):
    predecessor, directory, report = prepare(tmp_path)

    class FailedCaller(FixtureCaller):
        def invoke(self, **kwargs):
            super().invoke(**kwargs)
            return DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=8192,
                output_tokens=1024,
                estimated_cost_usd=MAX_CALL_USD,
                latency_seconds=1,
                usage_observed=False,
            )

    caller = FailedCaller()
    receipt = continuation.execute_policy_continuation(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=caller,
    )
    assert len(caller.calls) == 3 and receipt["status"] == "continuation_stopped_technical"
    assert receipt["not_executed_unique_requests"] == 6
    assert receipt["combined_committed_cost_usd_at_frozen_rates"] == round(
        5 * 0.00104 + 3 * MAX_CALL_USD, 6
    )
    assert (
        continuation.verify_policy_continuation(predecessor=predecessor, directory=directory)[
            "verification"
        ]
        == "pass"
    )


@pytest.mark.parametrize(
    "target", ["plan.json", "preflight-analysis.json", "preflight-decoding-audit.json"]
)
def test_preflight_tampering_blocks_before_a_call(tmp_path, target):
    predecessor, directory, report = prepare(tmp_path)
    (directory / target).write_text("{}")
    caller = FixtureCaller()
    with pytest.raises((ValueError, KeyError)):
        continuation.execute_policy_continuation(
            predecessor=predecessor,
            directory=directory,
            confirm_sha256=report["plan_sha256"],
            caller=caller,
        )
    assert not caller.calls and not (directory / "lease.json").exists()


def test_wrong_confirmation_and_code_drift_block_before_a_call(tmp_path, monkeypatch):
    predecessor, directory, _ = prepare(tmp_path)
    caller = FixtureCaller()
    with pytest.raises(ValueError, match="digest"):
        continuation.execute_policy_continuation(
            predecessor=predecessor, directory=directory, confirm_sha256="f" * 64, caller=caller
        )
    monkeypatch.setattr(continuation, "DECODING_POLICY", {"changed": True})
    with pytest.raises(ValueError, match="decoder"):
        continuation.checked_policy_continuation(predecessor=predecessor, directory=directory)
    assert not caller.calls


def test_nested_output_is_rejected_without_any_predecessor_mutation(tmp_path):
    predecessor, _ = stopped_pilot(tmp_path)
    original = continuation._manifest(predecessor)
    with pytest.raises(ValueError, match="disjoint"):
        continuation.prepare_policy_continuation(
            predecessor=predecessor, directory=predecessor / "nested"
        )
    assert continuation._manifest(predecessor) == original


def test_prepared_continuation_cli_verify_does_not_need_a_key_or_provider(tmp_path):
    predecessor, directory, _ = prepare(tmp_path)
    root = Path(__file__).resolve().parents[2]
    outcome = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/evidence_bounded_policy_pilot.py"),
            "verify-continuation",
            "--predecessor",
            str(predecessor),
            "--directory",
            str(directory),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(outcome.stdout)["provider_calls_executed"] == 0
    assert not (directory / "lease.json").exists()


def test_saved_output_and_predecessor_tampering_are_detected(tmp_path):
    predecessor, directory, report = prepare(tmp_path)
    continuation.execute_policy_continuation(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=FixtureCaller(),
    )
    (directory / "decoding-audit.json").write_text("{}")
    with pytest.raises(ValueError, match="replay differs"):
        continuation.verify_policy_continuation(predecessor=predecessor, directory=directory)
    (predecessor / "offline-baselines.json").write_text("{}")
    with pytest.raises(ValueError, match="predecessor"):
        continuation.checked_policy_continuation(predecessor=predecessor, directory=directory)


def test_actual_sdk_continuation_keeps_unchanged_snapshot_schema_and_payload(tmp_path):
    predecessor, directory, report = prepare(tmp_path)
    captured = []

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=400, completion_tokens=30),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            refusal=None, content=json.dumps(bounded_payload())
                        ),
                    )
                ],
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    caller = OpenAIDevelopmentCaller(maximum_calls=9, client=client)
    continuation.execute_policy_continuation(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=caller,
    )
    assert len(captured) == 9
    for request, wire in zip(_requests(cases())[5:], captured, strict=True):
        assert json.loads(wire["messages"][1]["content"]) == json.loads(
            json.dumps(request["payload"])
        )
        assert wire["messages"][0]["content"] == PROMPTS[request["policy"]]
        assert wire["model"] == "gpt-4.1-2025-04-14"
        assert wire["temperature"] == 0 and wire["seed"] == 731
        assert wire["store"] is False and wire["response_format"]["json_schema"]["strict"]
    with pytest.raises(ValueError, match="ceiling"):
        caller.invoke(prompt="extra", payload={}, schema=decision_schema())


def test_missing_record_or_replayed_request_cannot_be_verified_as_complete(tmp_path):
    predecessor, directory, report = prepare(tmp_path)
    continuation.execute_policy_continuation(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=FixtureCaller(),
    )
    requests = _requests(cases())
    (directory / "results" / f"{requests[-1]['request_id']}.json").unlink()
    with pytest.raises(ValueError, match="incomplete continuation"):
        continuation.verify_policy_continuation(predecessor=predecessor, directory=directory)
    replayed = requests[0]["request_id"]
    (directory / "results" / f"{replayed}.json").write_bytes(
        (predecessor / "results" / f"{replayed}.json").read_bytes()
    )
    with pytest.raises(ValueError, match="approved remaining requests"):
        continuation.verify_policy_continuation(predecessor=predecessor, directory=directory)


def test_verifier_rejects_requests_after_three_unassessable_responses(tmp_path):
    predecessor, directory, report = prepare(tmp_path)
    continuation.execute_policy_continuation(
        predecessor=predecessor,
        directory=directory,
        confirm_sha256=report["plan_sha256"],
        caller=FixtureCaller(malformed=True),
    )
    extra = _requests(cases())[8]
    (directory / "results" / f"{extra['request_id']}.json").write_text(
        json.dumps(
            {
                **{k: extra[k] for k in ("request_id", "policy", "context_sha256")},
                "status": "invalid_or_provider_failure",
                "call": call({}).model_dump(mode="json"),
            }
        )
    )
    with pytest.raises(ValueError, match="bypassed"):
        continuation.verify_policy_continuation(predecessor=predecessor, directory=directory)
