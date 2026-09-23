"""Offline tests for the separate main-study technical recovery boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import aletheia_lab.diagnosis.main_technical_recovery as recovery_module
import aletheia_lab.diagnosis.main_technical_recovery_verify as verify_module
from aletheia_lab.diagnosis._main_runtime_contexts import selection_schema
from aletheia_lab.diagnosis._main_runtime_store import MainRuntimeStore
from aletheia_lab.diagnosis.main_schema_smoke import (
    SYNTHETIC_EVIDENCE_ID,
    OpenAIMainRecoveryAdapter,
    build_synthetic_main_schema_request,
    provider_call,
)
from aletheia_lab.diagnosis.main_technical_recovery import (
    FormatOnlyMainAdapter,
    MainTechnicalRecoveryError,
    _pilot_requests,
)
from aletheia_lab.evaluation.claim_corpus_live import SystemMonotonicClock
from aletheia_lab.evaluation.diagnosis_main_census import DiagnosisMainPrivateCensusPacket
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import (
    GatewayRequest,
    OpenAIGatewayPolicy,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
    execute_gateway_request,
)
from aletheia_lab.model_gateway.openai import OpenAIGatewayClient
from aletheia_lab.project.identity import canonical_project_json, content_sha256

ROOT = Path(__file__).resolve().parents[2]


class _FakeDelegate:
    def __init__(self, binding: ProviderBinding, raw: bytes) -> None:
        self._binding = binding
        self._raw = raw
        self.calls = 0

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        self.calls += 1
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref="ev-" + "2" * 64,
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(self._raw),
            usage=UsageMetadata(
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


class _NeverCancel:
    def is_cancelled(self) -> bool:
        return False


def _request_and_policy() -> tuple[GatewayRequest, OpenAIGatewayPolicy]:
    return build_synthetic_main_schema_request(ROOT, source_commit_ref="1" * 40)


def _raw_response(
    *, claim_id: str = "claim-1", claim_text: str = "A measurement is present."
) -> bytes:
    return canonical_project_json(
        {
            "schema_version": "diagnosis-main-provider-output/1",
            "output_status": "completed",
            "atomic_claims": [
                {
                    "claim_local_id": claim_id,
                    "claim_type": "evidence_statement",
                    "claim_text": claim_text,
                    "material_parts": [{"part_id": "part-1", "text": "A measurement is present."}],
                    "visible_evidence_ids": [SYNTHETIC_EVIDENCE_ID],
                }
            ],
            "abstention_reason": None,
        }
    ).encode("utf-8")


def test_selection_turn_retains_its_original_provider_schema() -> None:
    request, policy = _request_and_policy()
    call = provider_call(request)
    selection = canonical_project_json(selection_schema((SYNTHETIC_EVIDENCE_ID,)))
    selection_call = call.model_copy(update={"response_schema_json": selection})
    adapter = OpenAIMainRecoveryAdapter(
        client=cast(OpenAIGatewayClient, object()),
        model_policy=request.initial_attempt.model_policy,
        policy=policy,
    )
    outbound: dict[str, object] = {"response_format": {"frozen_selection_schema": selection}}
    assert adapter._prepare_payload(selection_call, outbound) == outbound


def test_pilot_is_one_fixed_request_per_provider_route() -> None:
    variants = ("B0", "FULL", "B2", "A1", "CodeGraph", "B1", "A3", "A2")
    requests = tuple(
        SimpleNamespace(request_id=f"dmr-{index}", variant=variant)
        for index, variant in enumerate(variants)
    )
    packet = cast(
        DiagnosisMainPrivateCensusPacket,
        SimpleNamespace(analysis_census=SimpleNamespace(requests=requests)),
    )

    selected = _pilot_requests(packet)

    assert [item.variant for item in selected] == [
        "A1",
        "A2",
        "A3",
        "B1",
        "B2",
        "CodeGraph",
        "FULL",
    ]
    assert "B0" not in [item.variant for item in selected]


def test_plan_binds_exact_failed_census_and_provider_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = tuple(
        SimpleNamespace(request_id=f"dmr-{index}", variant=variant)
        for index, variant in enumerate(("B0", "A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"))
    )
    packet = SimpleNamespace(
        packet_sha256="a" * 64,
        analysis_census=SimpleNamespace(census_sha256="b" * 64, requests=requests),
    )
    runtime = SimpleNamespace(
        analysis_census_sha256="b" * 64,
        response_contract_sha256="c" * 64,
        runtime_contract_sha256="d" * 64,
    )

    class FakeFairness:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"freeze": "unaltered"}

    monkeypatch.setattr(recovery_module, "load_private_main_packet", lambda _root, _path: packet)
    monkeypatch.setattr(
        recovery_module,
        "load_main_runtime_inputs",
        lambda _root: (runtime, FakeFairness(), {"contract_sha256": "c" * 64}),
    )
    monkeypatch.setattr(
        recovery_module,
        "_predecessor_facts",
        lambda _path, _packet: ("e" * 64, "f" * 64, "1" * 64, "2" * 64),
    )
    monkeypatch.setattr(recovery_module, "_smoke_facts", lambda *_args: "3" * 64)
    plan = recovery_module.build_recovery_plan(
        root=ROOT,
        packet_path=tmp_path / "packet",
        predecessor=tmp_path / "predecessor",
        smoke=tmp_path / "smoke",
        recovery=tmp_path / "recovery",
        source_commit_ref="4" * 40,
        created_at="2026-09-23T00:00:00Z",
        cost_ceiling_usd=25.0,
    )
    assert plan["recovery_provider_request_count"] == 896
    assert plan["deterministic_request_count"] == 128
    assert plan["maximum_provider_turn_count"] == 1408
    assert plan["maximum_provider_attempt_count"] == 2816
    assert plan["pilot_provider_request_count"] == 7
    assert plan["predecessor_tree_sha256"] == "f" * 64
    assert plan["passing_smoke_receipt_sha256"] == "3" * 64
    assert plan["plan_sha256"] == canonical_execution_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )


def test_boundary_trim_preserves_provider_bytes_in_private_sidecar(tmp_path: Path) -> None:
    request, _ = _request_and_policy()
    call = provider_call(request)
    raw = _raw_response(claim_text=" A measurement is present. ")
    delegate = _FakeDelegate(
        ProviderBinding.from_model_policy(request.initial_attempt.model_policy), raw
    )
    repair_root = tmp_path / "repairs"
    adapter = FormatOnlyMainAdapter(delegate, repair_root)

    envelope = adapter.invoke(call)

    assert delegate.calls == 1
    assert (
        json.loads(envelope.raw_response.content)["atomic_claims"][0]["claim_text"]
        == "A measurement is present."
    )
    assert (repair_root / f"{call.attempt_id}.raw").read_bytes() == raw
    record = json.loads((repair_root / f"{call.attempt_id}.json").read_bytes())
    assert record["original_raw_sha256"] == content_sha256(raw)
    assert record["accepted_raw_sha256"] == envelope.raw_response.content_sha256
    assert record["repaired_field_paths"] == ["atomic_claims[0].claim_text"]


def test_non_format_error_is_not_repaired_or_silently_accepted(tmp_path: Path) -> None:
    request, _ = _request_and_policy()
    call = provider_call(request)
    raw = _raw_response(claim_id="claim-99")
    delegate = _FakeDelegate(
        ProviderBinding.from_model_policy(request.initial_attempt.model_policy), raw
    )
    adapter = FormatOnlyMainAdapter(delegate, tmp_path / "repairs")

    envelope = adapter.invoke(call)

    assert envelope.raw_response.content == raw
    assert not (tmp_path / "repairs").exists()


def test_prepare_is_single_use_and_checking_rebinds_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet_path = tmp_path / "packet.json"
    predecessor = tmp_path / "predecessor"
    smoke = tmp_path / "smoke"
    recovery = tmp_path / "recovery"
    commit = "1" * 40

    def fake_plan(**kwargs: object) -> dict[str, object]:
        assert kwargs["source_commit_ref"] == commit
        assert kwargs["cost_ceiling_usd"] == 25.0
        payload: dict[str, object] = {
            "source_commit_ref": commit,
            "created_at": kwargs["created_at"],
            "operator_cost_ceiling_usd": kwargs["cost_ceiling_usd"],
        }
        return {**payload, "plan_sha256": canonical_execution_sha256(payload)}

    monkeypatch.setattr(recovery_module, "_clean_main_commit", lambda _root: commit)
    monkeypatch.setattr(recovery_module, "build_recovery_plan", fake_plan)
    monkeypatch.setattr(recovery_module, "load_private_main_packet", lambda _root, _path: object())
    plan = recovery_module.prepare_recovery(
        root=ROOT,
        packet_path=packet_path,
        predecessor=predecessor,
        smoke=smoke,
        recovery=recovery,
        cost_ceiling_usd=25.0,
    )
    assert recovery_module._read_object(recovery / "plan.json") == plan
    assert (
        recovery_module._checked_plan(
            root=ROOT,
            packet_path=packet_path,
            predecessor=predecessor,
            smoke=smoke,
            recovery=recovery,
        )[0]
        == plan
    )
    with pytest.raises(MainTechnicalRecoveryError, match="absent or empty"):
        recovery_module.prepare_recovery(
            root=ROOT,
            packet_path=packet_path,
            predecessor=predecessor,
            smoke=smoke,
            recovery=recovery,
            cost_ceiling_usd=25.0,
        )
    altered = {**plan, "operator_cost_ceiling_usd": 24.0}
    (recovery / "plan.json").write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match canonical content"):
        recovery_module._checked_plan(
            root=ROOT,
            packet_path=packet_path,
            predecessor=predecessor,
            smoke=smoke,
            recovery=recovery,
        )


def test_repair_evidence_detects_original_byte_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, _ = _request_and_policy()
    call = provider_call(request)
    raw = _raw_response(claim_text=" A measurement is present. ")
    delegate = _FakeDelegate(
        ProviderBinding.from_model_policy(request.initial_attempt.model_policy), raw
    )
    repair_root = tmp_path / "repair-records"
    accepted = FormatOnlyMainAdapter(delegate, repair_root).invoke(call)
    monkeypatch.setattr(
        verify_module,
        "_stored_provider_responses",
        lambda _path: {call.attempt_id: (accepted.raw_response.content, call.response_schema_json)},
    )
    record_path = repair_root / f"{call.attempt_id}.json"
    receipt = {
        "format_repaired_attempt_count": 1,
        "format_repair_records_sha256": canonical_execution_sha256(
            ({"name": record_path.name, "sha256": content_sha256(record_path.read_bytes())},)
        ),
    }
    verify_module._verify_repair_records(tmp_path, receipt)
    (repair_root / f"{call.attempt_id}.raw").write_bytes(b"tampered")
    with pytest.raises(MainTechnicalRecoveryError, match="format repair evidence differs"):
        verify_module._verify_repair_records(tmp_path, receipt)


def test_repair_evidence_rejects_an_unrelated_accepted_payload(tmp_path: Path) -> None:
    request, _ = _request_and_policy()
    call = provider_call(request)
    original = _raw_response(claim_text=" A measurement is present. ")
    delegate = _FakeDelegate(
        ProviderBinding.from_model_policy(request.initial_attempt.model_policy), original
    )
    accepted = FormatOnlyMainAdapter(delegate, tmp_path).invoke(call)
    record = json.loads((tmp_path / f"{call.attempt_id}.json").read_bytes())
    verify_module._verify_repair_content(
        original=original,
        accepted=accepted.raw_response.content,
        schema_json=call.response_schema_json,
        record=record,
    )
    with pytest.raises(MainTechnicalRecoveryError, match="differs from stored provider result"):
        verify_module._verify_repair_content(
            original=original,
            accepted=_raw_response(claim_text="A different measurement is present."),
            schema_json=call.response_schema_json,
            record=record,
        )


def test_repair_record_is_bound_to_the_persisted_turn_result(tmp_path: Path) -> None:
    request, _ = _request_and_policy()
    delegate = _FakeDelegate(
        ProviderBinding.from_model_policy(request.initial_attempt.model_policy),
        _raw_response(claim_text=" A measurement is present. "),
    )
    adapter = FormatOnlyMainAdapter(delegate, tmp_path / "repair-records")
    result = execute_gateway_request(
        request,
        adapter=adapter,
        clock=SystemMonotonicClock(),
        cancellation=_NeverCancel(),
    )
    assert result.status == "parsed"
    assert result.raw_response is not None
    store = MainRuntimeStore(tmp_path / "main-store")
    store.begin_or_resume_turn("dmr-test", 1, request)
    store.complete_turn("dmr-test", 1, result)
    stored = verify_module._stored_provider_responses(tmp_path)
    assert stored[result.attempts[-1].attempt.attempt_id] == (
        result.raw_response.content,
        request.response_schema_json,
    )


def test_pilot_stop_is_verifiable_and_cannot_be_presented_as_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan: dict[str, object] = {
        "plan_sha256": "a" * 64,
        "pilot_request_ids_sha256": "b" * 64,
        "destination_sha256": "c" * 64,
    }
    lease = {
        "schema_version": "diagnosis-main-technical-recovery-lease/v1",
        "plan_sha256": plan["plan_sha256"],
        "authority_sha256": "d" * 64,
        "destination_sha256": plan["destination_sha256"],
        "one_execution_only": True,
    }
    lease_sha256 = canonical_execution_sha256(lease)
    stopped = {
        "status": "pilot_failed_closed",
        "plan_sha256": plan["plan_sha256"],
        "lease_sha256": lease_sha256,
        "pilot_request_ids_sha256": plan["pilot_request_ids_sha256"],
        "completed_pilot_request_count": 2,
        "terminal_status_counts": {"completed": 1, "technical_failure": 1},
        "original_registered_attempt_mutated": False,
    }
    recovery_module._publish(tmp_path / "lease.json", {**lease, "lease_sha256": lease_sha256})
    recovery_module._publish(
        tmp_path / "pilot-stop.json",
        {**stopped, "pilot_stop_sha256": canonical_execution_sha256(stopped)},
    )
    monkeypatch.setattr(
        verify_module,
        "_pilot_statuses",
        lambda _path, _packet, _count: {"completed": 1, "technical_failure": 1},
    )
    monkeypatch.setattr(
        verify_module, "load_main_runtime_inputs", lambda _root: (object(), None, None)
    )
    monkeypatch.setattr(
        verify_module,
        "authority_from_plan",
        lambda _plan, _runtime: SimpleNamespace(authority_sha256="d" * 64),
    )
    packet = cast(DiagnosisMainPrivateCensusPacket, object())
    assert (
        verify_module._verify_pilot(ROOT, tmp_path, plan, packet)["status"] == "pilot_failed_closed"
    )
    weakened_lease = {**lease, "one_execution_only": False}
    with pytest.raises(MainTechnicalRecoveryError, match="lease differs"):
        verify_module._verify_lease(
            {**weakened_lease, "lease_sha256": canonical_execution_sha256(weakened_lease)},
            plan,
            "d" * 64,
        )
    (tmp_path / "receipt.json").write_bytes(b"{}")
    with pytest.raises(MainTechnicalRecoveryError, match="cannot have a complete recovery"):
        verify_module._verify_pilot(ROOT, tmp_path, plan, packet)


def test_failed_first_pilot_never_dispatches_the_full_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery = tmp_path / "recovery"
    recovery.mkdir(mode=0o700)
    variants = ("A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")
    requests = tuple(
        SimpleNamespace(request_id=f"dmr-{index}", variant=variant, context_id="context-1")
        for index, variant in enumerate(variants)
    )
    packet = SimpleNamespace(
        analysis_census=SimpleNamespace(
            requests=requests,
            families=(SimpleNamespace(family_id="family-1"),),
            contexts=(SimpleNamespace(context_id="context-1", case_family_id="family-1"),),
        ),
        visible_contexts=(SimpleNamespace(context_id="context-1"),),
    )
    plan: dict[str, object] = {
        "plan_sha256": "a" * 64,
        "destination_sha256": "b" * 64,
        "pilot_request_ids_sha256": "c" * 64,
        "operator_cost_ceiling_usd": 25.0,
    }
    recovery_module._publish(recovery / "plan.json", plan)
    monkeypatch.setattr(recovery_module, "_checked_plan", lambda **_kwargs: (plan, packet))
    monkeypatch.setattr(
        recovery_module,
        "load_main_runtime_inputs",
        lambda _root: (object(), SimpleNamespace(model_policies={"main_llm_v1": object()}), {}),
    )
    monkeypatch.setattr(
        recovery_module,
        "authority_from_plan",
        lambda _plan, _runtime: SimpleNamespace(authority_sha256="d" * 64),
    )
    monkeypatch.setattr(OpenAIGatewayPolicy, "from_fairness_policy", lambda _policy: object())
    monkeypatch.setattr(recovery_module, "build_main_adapter_model_policy", lambda *_args: object())
    monkeypatch.setattr(
        OpenAIMainRecoveryAdapter,
        "from_environment",
        lambda **_kwargs: SimpleNamespace(binding=object()),
    )
    monkeypatch.setattr(recovery_module, "_batch_binding", lambda **_kwargs: object())
    monkeypatch.setattr(recovery_module, "_bind_store", lambda *_args: None)
    calls: list[str] = []

    def fail_first_pilot(**kwargs: object) -> SimpleNamespace:
        logical = cast(SimpleNamespace, kwargs["logical"])
        calls.append(str(logical.request_id))
        return SimpleNamespace(status="technical_failure")

    monkeypatch.setattr(recovery_module, "run_main_logical_request", fail_first_pilot)
    monkeypatch.setattr(
        recovery_module,
        "run_authorized_main_execution",
        lambda **_kwargs: pytest.fail("full census dispatched after pilot failure"),
    )
    with pytest.raises(MainTechnicalRecoveryError, match="confirmation differs"):
        recovery_module.execute_recovery(
            root=ROOT,
            packet_path=tmp_path / "packet",
            predecessor=tmp_path / "predecessor",
            smoke=tmp_path / "smoke",
            recovery=recovery,
            confirmed_plan_sha256="f" * 64,
        )
    assert not (recovery / "lease.json").exists()
    stopped = recovery_module.execute_recovery(
        root=ROOT,
        packet_path=tmp_path / "packet",
        predecessor=tmp_path / "predecessor",
        smoke=tmp_path / "smoke",
        recovery=recovery,
        confirmed_plan_sha256="a" * 64,
    )
    assert calls == ["dmr-0"]
    assert stopped["status"] == "pilot_failed_closed"
    assert stopped["completed_pilot_request_count"] == 1
    assert (recovery / "lease.json").is_file()
    assert not (recovery / "receipt.json").exists()
    with pytest.raises(MainTechnicalRecoveryError, match="already started"):
        recovery_module.execute_recovery(
            root=ROOT,
            packet_path=tmp_path / "packet",
            predecessor=tmp_path / "predecessor",
            smoke=tmp_path / "smoke",
            recovery=recovery,
            confirmed_plan_sha256="a" * 64,
        )
