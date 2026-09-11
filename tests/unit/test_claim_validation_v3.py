"""Regression tests for observed failure boundaries, not live outcomes."""

from __future__ import annotations

import copy
import json
import runpy
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.evaluation import claim_validation_v3_qualification as qualification
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_validation_v3_design import (
    FRAMES,
    LABELS,
    PREDECESSOR_CLOSEOUT,
    accept_source,
    build_design,
    build_probes,
    build_relation_tasks,
    frame_contexts,
    make_synthetic_context,
    numeric_leaves,
    reduce_relations,
    relation_instance_id,
    render_source_payload,
    source_bank,
    source_instance_id,
    source_payload,
    source_payload_issue,
    source_response_schema,
    structural_relations,
)
from aletheia_lab.evaluation.claim_validation_v3_execution import execute, verify
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as digest
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
def design():
    return build_design(ROOT)


def test_capacity_has_disjoint_texts_and_true_family_output_caps(design):
    assert design["source_slot_count"] == 360
    assert design["distinct_target_text_count"] >= 240
    certificate = design["capacity_assignment"]
    rows = [r for frame in certificate["assignments"].values() for r in frame]
    assert certificate["passed"]
    assert len(rows) == len({r["text_sha256"] for r in rows}) == 240
    for group in certificate["assignments"].values():
        families = Counter(r["family_id"] for r in group)
        assert len(families) >= 12
        assert max(families.values()) <= 5
        assert max(Counter(r["slot"] for r in group).values()) <= 2


def test_same_payload_different_requests_are_not_the_same_observation():
    a = source_instance_id("0" * 64, "1" * 64, "3" * 64, 1)
    b = source_instance_id("0" * 64, "2" * 64, "3" * 64, 1)
    assert a != b
    assert relation_instance_id(a, "natural", "4" * 64) != relation_instance_id(
        b, "natural", "4" * 64
    )
    with pytest.raises(ValueError):
        source_instance_id("z" * 64, "1" * 64, "3" * 64, 1)


def test_rfc6901_categorical_and_decimal_precision_not_lost():
    data = numeric_leaves(
        '{"payload":{"a/b~c":{"with space":0.123456789},"flag":true,"zero":0,"negative":-2}}'
    )
    assert data == {
        "/payload/a~1b~0c/with space": "0.123456789",
        "/payload/zero": "0",
        "/payload/negative": "-2",
    }


def test_all_frame_intents_have_independent_numeric_witnesses():
    source = make_synthetic_context(1, "full")
    counter = make_synthetic_context(2, "full")
    claim = source_bank(source)[0]
    contexts = frame_contexts(source, counter)
    for frame, label in zip(FRAMES, LABELS, strict=True):
        context = contexts[frame]
        assert (
            reduce_relations(
                structural_relations(claim, context), 2, [i.evidence_id for i in context.items]
            )
            == label
        )
    # Old withdrawal removed only the key: surviving support cannot be called unsupported.
    assert (
        reduce_relations(
            {
                "relations": [
                    {"part": 1, "evidence_id": "ev-a", "relation": "neutral"},
                    {"part": 2, "evidence_id": "ev-a", "relation": "supports"},
                ]
            },
            2,
            ["ev-a"],
        )
        == "partially_supported"
    )


def test_support_is_unioned_across_items_but_contradiction_wins():
    payload = {
        "relations": [
            {"part": 1, "evidence_id": "a", "relation": "supports"},
            {"part": 1, "evidence_id": "b", "relation": "neutral"},
            {"part": 2, "evidence_id": "a", "relation": "neutral"},
            {"part": 2, "evidence_id": "b", "relation": "supports"},
        ]
    }
    assert reduce_relations(payload, 2, ["a", "b"]) == "fully_supported"
    payload["relations"][1]["relation"] = "contradicts"
    assert reduce_relations(payload, 2, ["a", "b"]) == "contradicted"


@pytest.mark.parametrize(
    "mutation", ["duplicate", "missing", "foreign", "bool", "extra", "bad_polarity", "unhashable"]
)
def test_relation_matrix_fails_closed(mutation):
    payload = {"relations": [{"part": 1, "evidence_id": "a", "relation": "supports"}]}
    if mutation == "duplicate":
        payload["relations"] *= 2
    if mutation == "missing":
        payload["relations"] = []
    if mutation == "foreign":
        payload["relations"][0]["evidence_id"] = "b"
    if mutation == "bool":
        payload["relations"][0]["part"] = True
    if mutation == "extra":
        payload["relations"][0]["secret"] = "untrusted"
    if mutation == "bad_polarity":
        payload["relations"][0]["relation"] = "fully_supported"
    if mutation == "unhashable":
        payload["relations"][0]["relation"] = []
    with pytest.raises(ValueError):
        reduce_relations(payload, 1, ["a"])


@pytest.mark.parametrize(
    "mutation", ["value", "target", "order", "missing", "extra", "bool", "schema"]
)
def test_source_readings_fail_closed_without_text_repair(mutation):
    expected = source_bank(make_synthetic_context(0, "full"))[:2]
    payload = source_payload(copy.deepcopy(expected))
    assert accept_source(payload, expected)
    if mutation == "value":
        payload["readings"][0]["value"] += "0"
    if mutation == "target":
        payload["readings"][0]["target"] = 2
    if mutation == "order":
        payload["readings"].reverse()
    if mutation == "missing":
        payload["readings"].pop()
    if mutation == "extra":
        payload["readings"][0]["explanation"] = "untrusted"
    if mutation == "bool":
        payload["readings"][0]["target"] = True
    if mutation == "schema":
        payload["schema_version"] = "foreign"
    assert not accept_source(payload, expected)
    assert source_payload_issue(payload, expected) is not None


def test_source_contract_renders_scope_parts_and_citations_deterministically():
    expected = source_bank(make_synthetic_context(0, "full"))[:2]
    provider_payload = source_payload(expected)
    schema = source_response_schema(expected)
    rendered = render_source_payload(provider_payload, expected)
    assert set(provider_payload) == {"schema_version", "readings"}
    assert "claim_text" not in json.dumps(provider_payload)
    assert schema["properties"]["readings"]["minItems"] == 4
    assert all(
        part["text"].startswith("In the displayed measurement report, ")
        for claim in rendered["result"]["atomic_claims"]
        for part in claim["material_parts"]
    )
    assert all(
        claim["claim_text"] == "; ".join(part["text"] for part in claim["material_parts"])
        for claim in rendered["result"]["atomic_claims"]
    )
    assert source_payload_issue(provider_payload, expected) is None


def test_provider_authored_v3_prose_cannot_enter_the_v3_1_source_boundary():
    expected = source_bank(make_synthetic_context(0, "full"))[:2]
    legacy = {
        "schema_version": "diagnosis-provider-output/2",
        "result": {"output_status": "completed", "atomic_claims": expected},
    }
    assert source_payload_issue(legacy, expected) == "source_envelope_invalid"
    assert not accept_source(legacy, expected)


def test_qualification_has_each_profile_condition_and_four_relation_frames():
    probes = build_probes(ROOT)
    assert len(probes) == 33
    assert len({p["probe_sha256"] for p in probes}) == 33
    assert Counter(p["condition"] for p in probes if p["kind"] == "source") == {
        "full": 7,
        "missing_key": 7,
        "noisy": 7,
    }
    assert Counter(p["condition"] for p in probes if p["kind"] == "relation") == dict.fromkeys(
        FRAMES, 3
    )
    for p in probes:
        prompt, context = qualification.outbound(p)
        for marker in (
            "expected",
            "family_id",
            "automatic_label",
            "source_commit",
            "condition",
            "probe_sha256",
        ):
            assert marker not in context.model_payload()
        assert "Expected answer:" not in prompt


def test_all_authentic_slots_normalize_and_preserve_two_claims(design):
    for slot in design["slots"]:
        probe = {**slot, "variant": slot["source_schedule"]["variant"]}
        assert qualification.source_roundtrip(ROOT, probe, source_payload(slot["expected"]))


def test_relation_handoff_only_emits_prespecified_frames(design):
    assert design["relation_request_count"] == 240
    row = design["relation_census"][0]
    first = next(s for s in design["slots"] if s["slot_sha256"] == row["slot"])
    tasks = build_relation_tasks("0" * 64, design, first, source_payload(first["expected"]))
    expected = [r for r in design["relation_census"] if r["slot"] == first["slot_sha256"]]
    assert len(tasks) == len(expected) > 0
    assert len({t["relation_instance_sha256"] for t in tasks}) == len(tasks)
    for task in tasks:
        assert set(task["provider_payload"]) == {"claim", "visible_evidence"}
        assert task["provider_payload"]["claim"] in first["expected"]
    changed = copy.deepcopy(first)
    changed["source_schedule"]["family_id"] = "foreign"
    with pytest.raises(ValueError, match="binding"):
        build_relation_tasks("0" * 64, design, changed, source_payload(first["expected"]))
    changed = copy.deepcopy(first)
    changed["source_schedule"]["evidence_condition"] = "foreign"
    changed["slot_sha256"] = digest(
        {
            "version": design["schema_version"],
            **{k: v for k, v in changed.items() if k != "slot_sha256"},
        }
    )
    with pytest.raises(ValueError, match="binding"):
        build_relation_tasks("0" * 64, design, changed, source_payload(first["expected"]))


@pytest.fixture
def authorized(tmp_path, monkeypatch):
    state = RepositoryExecutionState(
        branch="main", head_commit=COMMIT, origin_main_commit=COMMIT, clean=True
    )
    monkeypatch.setattr(qualification, "inspect_repository_state", lambda root: state)
    monkeypatch.setattr(
        qualification,
        "load_v2_extraction_closeout",
        lambda path: SimpleNamespace(closeout_sha256=PREDECESSOR_CLOSEOUT),
    )
    plan = qualification.build_plan(ROOT, tmp_path / "archive/closeout.json")
    run = qualification.checked_run(
        ROOT, tmp_path / "qualification", tmp_path / "archive/closeout.json"
    )
    auth = qualification.authorize(ROOT, run, plan, 2.0)
    run.mkdir()
    qualification.publish(run / "authorization.json", auth)
    prepared = qualification.prepare_requests(ROOT, plan, auth)
    probes = build_probes(ROOT)
    payloads = {
        r.request.initial_attempt.request_identity_sha256: source_payload(p["expected"])
        if p["kind"] == "source"
        else p["expected"]
        for r, p in zip(prepared, probes, strict=True)
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


def test_end_to_end_store_replay_and_failure_denominators(authorized):
    run, plan, auth, prepared, payloads = authorized
    target = prepared[0].request.initial_attempt.request_identity_sha256
    semantic_target = prepared[1].request.initial_attempt.request_identity_sha256
    payloads[semantic_target]["readings"][0]["value"] = "999"
    adapter = FakeAdapter(prepared, payloads, target)
    receipt = execute(
        ROOT,
        run,
        plan,
        auth,
        confirmation=auth["authorization_sha256"],
        adapter=adapter,
        retry=V2RetryController(sleep=lambda _: None),
    )
    assert receipt["terminal_request_count"] == 33
    assert receipt["accepted_count"] == 31
    assert receipt["technical_failure_count"] == 1
    assert receipt["semantic_failure_count"] == 1
    assert receipt["semantic_issue_counts"] == {"source_value_mismatch": 1}
    assert receipt["outcomes"][1]["semantic_issue_code"] == "source_value_mismatch"
    assert receipt["outcomes"][0]["failure_categories"] == {"request_rejected": 1}
    assert receipt["provider_attempt_count"] == 33
    assert receipt["cohort_planning_unlocked"] is False
    assert receipt["cohort_execution_authorized"] is False
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    assert verify(ROOT, run, plan, auth) == receipt
    assert before == {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="already registered"):
        execute(ROOT, run, plan, auth, confirmation=auth["authorization_sha256"], adapter=adapter)
    assert adapter.calls == 33
    receipt["accepted_count"] = 0
    altered = qualification.seal(
        {k: v for k, v in receipt.items() if k != "receipt_sha256"}, "receipt_sha256"
    )
    (run / "receipt.json").write_text(canonical_project_json(altered))
    with pytest.raises(ValueError, match="independent"):
        verify(ROOT, run, plan, auth)


def test_authority_tamper_and_bad_budget_fail_before_calls(authorized):
    run, plan, auth, prepared, payloads = authorized
    adapter = FakeAdapter(prepared, payloads)
    for value in (-1.0, float("nan"), float("inf")):
        changed = {**auth, "operator_cost_ceiling_usd": value}
        with pytest.raises(ValueError):
            execute(
                ROOT,
                run,
                plan,
                changed,
                confirmation=changed["authorization_sha256"],
                adapter=adapter,
            )
    with pytest.raises(ValueError, match="confirmation"):
        execute(ROOT, run, plan, auth, confirmation="0" * 64, adapter=adapter)
    assert adapter.calls == 0
    assert not (run / "lease.json").exists()


def test_real_transport_serializes_both_probe_kinds_without_evaluator_metadata(authorized):
    _, plan, _, prepared, payloads = authorized
    for item in (prepared[0], prepared[21]):
        request = item.request
        expected = payloads[request.initial_attempt.request_identity_sha256]
        calls = []

        def create(_calls=calls, _expected=expected, **kwargs):
            _calls.append(kwargs)
            return SimpleNamespace(
                id="qualification-mocked-response",
                model=qualification.MODEL_SNAPSHOT,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=canonical_project_json(_expected), refusal=None
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
            "family_id",
            "automatic_label",
            plan["protocol_sha256"],
        ):
            assert forbidden not in messages


def test_rehashed_plan_change_is_rejected_before_provider_call(authorized):
    run, plan, auth, prepared, payloads = authorized
    changed = qualification.seal(
        {k: (1 if k == "request_count" else v) for k, v in plan.items() if k != "plan_sha256"},
        "plan_sha256",
    )
    adapter = FakeAdapter(prepared, payloads)
    with pytest.raises(ValueError, match="frozen inputs"):
        execute(
            ROOT, run, changed, auth, confirmation=auth["authorization_sha256"], adapter=adapter
        )
    assert adapter.calls == 0


def test_rehashed_authority_rejects_type_confusion_or_extra_fields(authorized):
    run, plan, auth, prepared, payloads = authorized
    adapter = FakeAdapter(prepared, payloads)
    for key, value in (
        ("registered_attempts", True),
        ("claims_materialized", 0),
        ("credential_stored", True),
        ("unexpected", "untrusted"),
    ):
        altered = qualification.seal(
            {**{k: v for k, v in auth.items() if k != "authorization_sha256"}, key: value},
            "authorization_sha256",
        )
        with pytest.raises(ValueError, match="authority"):
            execute(
                ROOT,
                run,
                plan,
                altered,
                confirmation=altered["authorization_sha256"],
                adapter=adapter,
            )
    assert adapter.calls == 0


def test_operator_cli_authorize_preflight_execute_verify(authorized, monkeypatch, capsys):
    run, plan, _, _, _ = authorized
    destination = run.parent / "cli-qualification"
    namespace = runpy.run_path(str(ROOT / "scripts/claim_support_validation_v3.py"))
    main = namespace["main"]
    adapters = []

    def adapter_factory(root, current_plan, auth):
        prepared = qualification.prepare_requests(root, current_plan, auth)
        probes = build_probes(root)
        payloads = {
            r.request.initial_attempt.request_identity_sha256: source_payload(p["expected"])
            if p["kind"] == "source"
            else p["expected"]
            for r, p in zip(prepared, probes, strict=True)
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
                "--predecessor-closeout",
                str(run.parent / "archive/closeout.json"),
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
        "2.0",
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
    monkeypatch.setenv("OPENAI_API_KEY", "sk-qualification-test-never-render")
    code, ready = invoke("require-live-ready")
    assert code == 0 and ready["request_count"] == 33
    assert "sk-qualification-test" not in json.dumps(ready)
    code, receipt = invoke(
        "execute", "--confirm-authorization-sha256", auth["authorization_sha256"]
    )
    assert code == 0 and receipt["accepted_count"] == 33
    code, rebuilt = invoke("verify")
    assert code == 0 and rebuilt == receipt
    assert sum(a.calls for a in adapters) == 33


def test_run_cannot_overlap_archive_or_repository(tmp_path):
    for target in (ROOT, ROOT / "artifact", tmp_path, tmp_path / "archive"):
        with pytest.raises(ValueError, match="overlap"):
            qualification.checked_run(ROOT, target, tmp_path / "archive/closeout.json")


def test_protocol_file_rebuild_matches_local_code_and_qual_cost_is_bounded():
    p = qualification.verify_protocol(ROOT)
    failure = qualification.verify_failure_closeout(ROOT)
    assert failure == qualification.build_failure_closeout()
    assert p["failed_qualification_closeout_sha256"] == failure["closeout_sha256"]
    assert p["capacity_passed"]
    assert p["global_canonical_text_uniqueness_required"]
    assert not p["variant_superiority_claims_permitted"]
    assert p["maximum_output_tokens"] == 2048
    assert p["source_provider_schema_version"] == "claim-source-measurement-output/1"
