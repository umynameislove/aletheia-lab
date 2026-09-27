"""Offline guards for the forward-only recovery scoring boundary."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import aletheia_lab.diagnosis.main_recovery_scoring as scoring
import aletheia_lab.diagnosis.main_technical_recovery_verify as recovery_verify
from aletheia_lab.diagnosis._main_pipeline_contracts import DiagnosisMainPipelineError
from aletheia_lab.diagnosis._main_pipeline_relations import (
    RehearsalRelationAuthorization,
    execute_pipeline_relation_stage,
    offline_relation_adapter,
)
from aletheia_lab.diagnosis.main_technical_recovery import MainTechnicalRecoveryError
from aletheia_lab.evaluation.claim_corpus_live import SystemMonotonicClock
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.claim_relation_provider import (
    build_relation_gateway_requests_for_assignments,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import ClaimRelationProviderContext

read_pipeline_relation_stage = scoring.read_pipeline_relation_stage


def _sources(tmp_path: Path) -> scoring._Sources:
    return scoring._Sources(
        tmp_path / "repo",
        tmp_path / "packet.json",
        tmp_path / "predecessor",
        tmp_path / "smoke",
        tmp_path / "recovery",
        tmp_path / "scoring",
    )


def _facts() -> scoring._Facts:
    return scoring._Facts(
        preparation=cast(
            scoring.DiagnosisMainScoringPreparation,
            SimpleNamespace(preparation_sha256=scoring.SCORING_PREPARATION_SHA256),
        ),
        packet_sha256="1" * 64,
        census_sha256="2" * 64,
        recovery_plan_sha256="3" * 64,
        assignment_ids_sha256="4" * 64,
        provider_payloads_sha256="5" * 64,
        policy_sha256="6" * 64,
        worst_case_reservation_usd=Decimal("86.862556"),
        max_attempts=2,
        max_output_tokens=600,
        max_visible_evidence_items=5,
    )


def test_scoring_plan_binds_private_source_and_forbids_diagnosis(tmp_path: Path) -> None:
    plan = scoring._plan(
        _sources(tmp_path),
        _facts(),
        source_commit_ref="a" * 40,
        created_at="2026-09-24T00:00:00Z",
    )
    assert plan["recovery_receipt_sha256"] == scoring.RECOVERY_RECEIPT_SHA256
    assert plan["relation_request_count"] == 3181
    assert plan["operator_cost_ceiling_usd"] == 90.0
    assert plan["worst_case_reservation_usd_at_frozen_rates"] == "86.862556"
    assert plan["diagnosis_provider_calls_permitted"] is False
    assert plan["provider_input_fields"] == ["claim_text", "claim_type", "visible_evidence"]
    assert plan["maximum_visible_evidence_items"] == 5


def test_scoring_destination_cannot_overlap_or_contain_unknown_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    monkeypatch.setattr(scoring, "checked_recovery_paths", lambda _root, *paths: paths)
    monkeypatch.setattr(scoring, "checked_private_path", lambda path, _root: path.resolve())
    with pytest.raises(scoring.MainRecoveryScoringError, match="overlaps"):
        scoring._checked_sources(
            root=source.root,
            packet_path=source.packet_path,
            predecessor=source.predecessor,
            smoke=source.smoke,
            recovery=source.recovery,
            scoring=source.recovery / "nested",
        )

    source.scoring.mkdir(mode=0o700)
    (source.scoring / "untracked.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(scoring.MainRecoveryScoringError, match="unsafe artifact"):
        scoring._checked_sources(
            root=source.root,
            packet_path=source.packet_path,
            predecessor=source.predecessor,
            smoke=source.smoke,
            recovery=source.recovery,
            scoring=source.scoring,
        )


def test_inspect_scoring_is_read_only_and_reports_only_safe_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    monkeypatch.setattr(scoring, "_checked_sources", lambda **_kwargs: source)
    monkeypatch.setattr(scoring, "_facts", lambda _source: _facts())
    monkeypatch.setattr(
        scoring,
        "_publish",
        lambda *_args: pytest.fail("offline inspection must not publish an artifact"),
    )
    result = scoring.inspect_scoring(root=tmp_path)
    assert result["status"] == "offline_scoring_preflight_pass"
    assert result["provider_calls_executed"] is False
    assert result["maximum_provider_attempt_count"] == 6362
    assert result["worst_case_reservation_usd_at_frozen_rates"] == "86.862556"
    assert not source.scoring.exists()


def test_execute_checks_confirmation_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    monkeypatch.setattr(scoring, "_checked_sources", lambda **_kwargs: source)
    monkeypatch.setattr(scoring, "_clean_main_commit", lambda _root: "a" * 40)
    monkeypatch.setattr(scoring, "_facts", lambda _source: _facts())
    monkeypatch.setattr(
        scoring,
        "_checked_plan",
        lambda _source, _facts: {"plan_sha256": "b" * 64, "source_commit_ref": "a" * 40},
    )
    monkeypatch.setattr(
        scoring.OpenAIChatCompletionsGatewayAdapter,
        "from_environment",
        lambda **_kwargs: pytest.fail("provider constructed before action-time confirmation"),
    )
    with pytest.raises(scoring.MainRecoveryScoringError, match="confirmation"):
        scoring.execute_scoring(confirmed_plan_sha256="c" * 64, root=tmp_path)
    assert not source.scoring.exists()


def test_execute_dispatches_only_relation_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    source.scoring.mkdir()
    (source.scoring / "plan.json").touch()
    plan = {
        "plan_sha256": "b" * 64,
        "source_commit_ref": "a" * 40,
        "destination_sha256": "c" * 64,
    }
    prepared = (
        SimpleNamespace(
            request=SimpleNamespace(initial_attempt=SimpleNamespace(model_policy=object()))
        ),
    )
    relations = SimpleNamespace(
        results_sha256="d" * 64, model_dump=lambda **_kwargs: {"result": "sealed"}
    )
    analysis_input = SimpleNamespace(
        input_sha256="e" * 64, model_dump=lambda **_kwargs: {"input": "sealed"}
    )
    published: list[str] = []
    stages: list[Path] = []
    monkeypatch.setattr(scoring, "_checked_sources", lambda **_kwargs: source)
    monkeypatch.setattr(scoring, "_clean_main_commit", lambda _root: "a" * 40)
    monkeypatch.setattr(scoring, "_facts", lambda _source: _facts())
    monkeypatch.setattr(scoring, "_checked_plan", lambda _source, _facts: plan)
    monkeypatch.setattr(scoring, "_prepared", lambda *_args: prepared)
    monkeypatch.setattr(
        scoring,
        "load_main_runtime_inputs",
        lambda _root: (None, SimpleNamespace(model_policies={"main_llm_v1": object()}), None),
    )
    monkeypatch.setattr(
        scoring,
        "OpenAIGatewayPolicy",
        SimpleNamespace(from_fairness_policy=lambda _policy: object()),
    )
    monkeypatch.setattr(
        scoring,
        "OpenAIChatCompletionsGatewayAdapter",
        SimpleNamespace(from_environment=lambda **_kwargs: object()),
    )
    monkeypatch.setattr(scoring, "restore_relation_budget", lambda *_args: None)
    monkeypatch.setattr(scoring, "PacedProviderAdapter", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(scoring, "BudgetedProviderAdapter", lambda *_args: object())
    monkeypatch.setattr(scoring, "_publish", lambda path, _payload: published.append(path.name))

    def relation_stage(**kwargs: object):
        stages.append(cast(Path, kwargs["store_root"]))
        return relations, {"parsed": 1}

    monkeypatch.setattr(scoring, "execute_pipeline_relation_stage", relation_stage)
    monkeypatch.setattr(
        scoring, "materialize_main_analysis_input", lambda **_kwargs: analysis_input
    )
    receipt = scoring.execute_scoring(confirmed_plan_sha256="b" * 64, root=tmp_path)
    assert stages == [source.scoring / "relation-store"]
    assert published == [
        "lease.json",
        "relation-results.json",
        "analysis-input.json",
        "receipt.json",
    ]
    assert receipt["diagnosis_provider_calls_executed"] is False

    with monkeypatch.context() as bounded:
        bounded.setattr(
            scoring, "SharedProviderBudget", lambda _ceiling: SimpleNamespace(exhausted=True)
        )
        with pytest.raises(scoring.MainRecoveryScoringError, match="restored relation cost"):
            scoring.execute_scoring(confirmed_plan_sha256="b" * 64, root=tmp_path)

    (source.scoring / "lease.json").write_text("{}", encoding="utf-8")
    with monkeypatch.context() as changed_lease:
        changed_lease.setattr(scoring, "_read_object", lambda _path: {"unexpected": True})
        changed_lease.setattr(scoring, "validate_self_hash", lambda *_args: None)
        with pytest.raises(scoring.MainRecoveryScoringError, match="lease differs"):
            scoring.execute_scoring(confirmed_plan_sha256="b" * 64, root=tmp_path)

    (source.scoring / "unexpected").touch()
    with pytest.raises(scoring.MainRecoveryScoringError, match="unknown artifacts"):
        scoring.execute_scoring(confirmed_plan_sha256="b" * 64, root=tmp_path)


def test_read_only_relation_verifier_never_creates_missing_store(tmp_path: Path) -> None:
    store = tmp_path / "relation-store"
    with pytest.raises(DiagnosisMainPipelineError, match="incomplete"):
        read_pipeline_relation_stage(
            prepared=cast(tuple, (object(),)),
            preparation=cast(
                scoring.DiagnosisMainScoringPreparation,
                SimpleNamespace(relation_request_count=1),
            ),
            store_root=store,
        )
    assert not store.exists()


def test_read_only_relation_verifier_rebuilds_a_real_terminal_without_writes(
    tmp_path: Path,
) -> None:
    evidence = build_visible_evidence_item(
        evidence_id="evidence-1",
        kind="metric",
        title="Synthetic metric",
        content="The metric is 0.75.",
        source_content_sha256="a" * 64,
    )
    payload = {
        "claim_text": "The metric is 0.75.",
        "claim_type": "evidence_statement",
        "visible_evidence": (evidence.model_dump(mode="json"),),
    }
    identity = {
        "schema_version": "claim-relation-assignment-request/v1",
        "source_output_sha256": "b" * 64,
        "claim_local_id": "claim-1",
        "provider_payload": payload,
        "visible_context_sha256": "c" * 64,
    }
    digest = canonical_execution_sha256(identity)
    assignment = ClaimRelationAssignmentRequest.model_validate(
        {
            "assignment_request_id": f"ccrel-{digest}",
            "source_output_sha256": "b" * 64,
            "claim_local_id": "claim-1",
            **payload,
            "visible_context_sha256": "c" * 64,
            "assignment_request_sha256": digest,
        }
    )
    prepared = build_relation_gateway_requests_for_assignments(
        Path(__file__).resolve().parents[2],
        (assignment,),
        preparation_sha256="d" * 64,
        plan_sha256="e" * 64,
        source_commit_ref="f" * 40,
        authorization=RehearsalRelationAuthorization("ev-" + "1" * 64),
        expected_request_count=1,
        execution_boundary="diagnosis-main-relation-scoring/v1",
        dataset_scope="diagnosis-main-controlled-census",
    )
    preparation = cast(
        scoring.DiagnosisMainScoringPreparation,
        SimpleNamespace(
            relation_request_count=1,
            execution_mode="offline_rehearsal",
            preparation_sha256="d" * 64,
        ),
    )
    store = tmp_path / "relation-store"
    executed, _ = execute_pipeline_relation_stage(
        prepared=prepared,
        preparation=preparation,
        store_root=store,
        adapter=offline_relation_adapter(prepared),
        clock=SystemMonotonicClock(),
    )
    before = tuple(
        (item.relative_to(store), item.read_bytes())
        for item in sorted(store.rglob("*"))
        if item.is_file()
    )
    rebuilt, counts = read_pipeline_relation_stage(
        prepared=prepared, preparation=preparation, store_root=store
    )
    after = tuple(
        (item.relative_to(store), item.read_bytes())
        for item in sorted(store.rglob("*"))
        if item.is_file()
    )
    assert rebuilt.results_sha256 == executed.results_sha256
    assert counts == {"parsed": 1}
    assert before == after


def test_sealed_recovery_verifier_uses_recorded_commit_not_current_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    plan = {
        "plan_sha256": "1" * 64,
        "source_commit_ref": "2" * 40,
        "created_at": "2026-09-24T00:00:00Z",
        "operator_cost_ceiling_usd": 175.0,
    }
    monkeypatch.setattr(recovery_verify, "checked_recovery_paths", lambda *args: args[1:])
    monkeypatch.setattr(recovery_verify, "_read_object", lambda _path: plan)
    monkeypatch.setattr(recovery_verify, "validate_self_hash", lambda *_args: None)
    monkeypatch.setattr(
        recovery_verify,
        "build_recovery_plan",
        lambda **kwargs: plan if kwargs["source_commit_ref"] == "2" * 40 else {},
    )
    monkeypatch.setattr(recovery_verify, "load_private_main_packet", lambda *_args: object())
    monkeypatch.setattr(recovery_verify, "_verify_complete", lambda *_args: {"status": "verified"})
    source.recovery.mkdir()
    (source.recovery / "receipt.json").touch()
    assert (
        recovery_verify.verify_sealed_recovery_from_pinned_source(
            root=source.root,
            packet_path=source.packet_path,
            predecessor=source.predecessor,
            smoke=source.smoke,
            recovery=source.recovery,
        )["status"]
        == "verified"
    )
    monkeypatch.setattr(recovery_verify, "build_recovery_plan", lambda **_kwargs: {})
    with pytest.raises(MainTechnicalRecoveryError, match="differs"):
        recovery_verify.verify_sealed_recovery_from_pinned_source(
            root=source.root,
            packet_path=source.packet_path,
            predecessor=source.predecessor,
            smoke=source.smoke,
            recovery=source.recovery,
        )


def test_scoring_facts_reconcile_blind_requests_and_bounded_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    source.recovery.mkdir()
    (source.recovery / "main-batch-result.json").write_bytes(b"{}")
    evidence = tuple(
        build_visible_evidence_item(
            evidence_id=f"evidence-{index}",
            kind="metric",
            title=f"Metric {index}",
            content=f"The metric is {index}.",
            source_content_sha256=f"{index + 1:064x}",
        )
        for index in range(5)
    )
    context = ClaimRelationProviderContext.from_provider_payload(
        {
            "claim_text": "The metric is supported by the visible evidence.",
            "claim_type": "evidence_statement",
            "visible_evidence": tuple(item.model_dump(mode="json") for item in evidence),
        }
    )
    assignments = tuple(
        SimpleNamespace(
            assignment_request_sha256=f"{index + 1:064x}",
            visible_evidence=context.visible_evidence,
            provider_payload=context.model_payload,
        )
        for index in range(scoring.RELATION_REQUEST_COUNT)
    )
    records = []
    offset = 0
    for index in range(1024):
        count = 4 if index < 109 else 3
        records.append(
            SimpleNamespace(
                claims=tuple(
                    SimpleNamespace(relation_request=item)
                    for item in assignments[offset : offset + count]
                )
            )
        )
        offset += count
    assert offset == scoring.RELATION_REQUEST_COUNT
    preparation = SimpleNamespace(
        preparation_sha256=scoring.SCORING_PREPARATION_SHA256,
        relation_request_count=scoring.RELATION_REQUEST_COUNT,
        record_count=1024,
        records=tuple(records),
    )
    packet = SimpleNamespace(
        packet_sha256="1" * 64,
        analysis_census=SimpleNamespace(census_sha256="2" * 64),
    )
    policy = SimpleNamespace(
        model_snapshot="gpt-4.1-2025-04-14",
        maximum_output_tokens=600,
        policy_sha256="3" * 64,
    )
    prepared = SimpleNamespace(
        request=SimpleNamespace(
            context=context,
            runtime_policy=SimpleNamespace(max_attempts=2),
        )
    )
    sealed_receipt = {
        "receipt_sha256": scoring.RECOVERY_RECEIPT_SHA256,
        "relation_scoring_executed": False,
        "terminal_status_counts": {
            "completed": 829,
            "deterministic_completed": 128,
            "technical_failure": 67,
        },
    }
    monkeypatch.setattr(
        scoring,
        "verify_sealed_recovery_from_pinned_source",
        lambda **_kwargs: sealed_receipt,
    )
    monkeypatch.setattr(
        scoring,
        "_read_object",
        lambda _path: {"source_commit_ref": "a" * 40, "plan_sha256": "b" * 64},
    )
    monkeypatch.setattr(scoring, "load_private_main_packet", lambda *_args: packet)
    monkeypatch.setattr(
        scoring, "load_main_runtime_inputs", lambda _root: (object(), object(), object())
    )
    monkeypatch.setattr(scoring, "load_main_scoring_contract", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        scoring,
        "MainBatchResult",
        SimpleNamespace(model_validate_json=lambda _bytes: object()),
    )
    monkeypatch.setattr(scoring, "prepare_main_scoring", lambda **_kwargs: preparation)
    monkeypatch.setattr(scoring, "load_evidence_semantics_policy", lambda _root: policy)
    monkeypatch.setattr(
        scoring,
        "build_pipeline_relation_requests",
        lambda *_args, **_kwargs: (prepared,) * scoring.RELATION_REQUEST_COUNT,
    )
    monkeypatch.setattr(
        scoring.SharedProviderBudget,
        "_request_reservation",
        staticmethod(lambda _request: Decimal("0.01")),
    )

    facts = scoring._facts(source)
    assert facts.preparation is preparation
    assert facts.max_visible_evidence_items == 5
    assert facts.max_attempts == 2
    assert facts.max_output_tokens == 600
    assert facts.worst_case_reservation_usd == Decimal("63.62")
    assert facts.assignment_ids_sha256 == canonical_execution_sha256(
        tuple(item.assignment_request_sha256 for item in assignments)
    )
    sealed_receipt["relation_scoring_executed"] = True
    with pytest.raises(scoring.MainRecoveryScoringError, match="sealed scoring source"):
        scoring._facts(source)
    sealed_receipt["relation_scoring_executed"] = False

    preparation.preparation_sha256 = "0" * 64
    with pytest.raises(scoring.MainRecoveryScoringError, match="locked facts"):
        scoring._facts(source)
    preparation.preparation_sha256 = scoring.SCORING_PREPARATION_SHA256

    for assignment in assignments:
        assignment.visible_evidence = ()
    with pytest.raises(scoring.MainRecoveryScoringError, match="evidence request ceiling"):
        scoring._facts(source)
    for assignment in assignments:
        assignment.visible_evidence = context.visible_evidence

    prepared.request.context = object()
    with pytest.raises(scoring.MainRecoveryScoringError, match="provider-visible"):
        scoring._facts(source)
    prepared.request.context = context
    prepared.request.runtime_policy.max_attempts = 1
    with pytest.raises(scoring.MainRecoveryScoringError, match="retry or output"):
        scoring._facts(source)
    prepared.request.runtime_policy.max_attempts = 2

    monkeypatch.setattr(
        scoring.SharedProviderBudget,
        "_request_reservation",
        staticmethod(lambda _request: Decimal("0.02")),
    )
    with pytest.raises(scoring.MainRecoveryScoringError, match="cost ceiling"):
        scoring._facts(source)
    monkeypatch.setattr(
        scoring,
        "load_evidence_semantics_policy",
        lambda _root: SimpleNamespace(model_snapshot="other"),
    )
    with pytest.raises(scoring.MainRecoveryScoringError, match="model snapshot"):
        scoring._facts(source)


def test_private_plan_is_created_once_and_rebuilt_from_locked_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    facts = _facts()
    published: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(scoring, "_checked_sources", lambda **_kwargs: source)
    monkeypatch.setattr(scoring, "_clean_main_commit", lambda _root: "a" * 40)
    monkeypatch.setattr(scoring, "_facts", lambda _source: facts)
    monkeypatch.setattr(
        scoring, "_publish", lambda path, payload: published.append((path, payload))
    )
    result = scoring.prepare_scoring(root=source.root)
    assert result["status"] == "offline_scoring_preflight_pass"
    assert source.scoring.is_dir()
    assert len(published) == 1
    path, plan = published[0]
    assert path == source.scoring / "plan.json"
    assert plan["diagnosis_provider_calls_permitted"] is False
    assert plan["plan_sha256"] == result["plan_sha256"]

    monkeypatch.setattr(scoring, "_read_object", lambda _path: plan)
    assert scoring._checked_plan(source, facts) == plan
    changed = {**plan, "relation_request_count": 1}
    changed["plan_sha256"] = canonical_execution_sha256(
        {key: value for key, value in changed.items() if key != "plan_sha256"}
    )
    monkeypatch.setattr(scoring, "_read_object", lambda _path: changed)
    with pytest.raises(scoring.MainRecoveryScoringError, match="sealed evidence"):
        scoring._checked_plan(source, facts)


def test_authorized_relation_census_rechecks_cost_without_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    facts = replace(_facts(), worst_case_reservation_usd=Decimal("63.62"))
    plan = scoring._plan(
        source, facts, source_commit_ref="a" * 40, created_at="2026-09-24T00:00:00Z"
    )
    request = SimpleNamespace(
        request=SimpleNamespace(runtime_policy=SimpleNamespace(max_attempts=2))
    )
    prepared = (request,) * scoring.RELATION_REQUEST_COUNT
    monkeypatch.setattr(
        scoring, "build_pipeline_relation_requests", lambda *_args, **_kwargs: prepared
    )
    monkeypatch.setattr(
        scoring.SharedProviderBudget,
        "_request_reservation",
        staticmethod(lambda _request: Decimal("0.01")),
    )
    assert scoring._prepared(source, facts, plan) == prepared
    with pytest.raises(scoring.MainRecoveryScoringError, match="census or cost"):
        scoring._prepared(source, replace(facts, worst_case_reservation_usd=Decimal("0")), plan)


def test_read_only_verification_rebuilds_receipt_and_rejects_saved_input_tamper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _sources(tmp_path)
    source.scoring.mkdir()
    facts = _facts()
    plan = scoring._plan(
        source, facts, source_commit_ref="a" * 40, created_at="2026-09-24T00:00:00Z"
    )
    lease = scoring._lease(plan)
    relations = SimpleNamespace(
        results_sha256="d" * 64,
        model_dump=lambda **_kwargs: {"results": "synthetic"},
    )
    analysis_input = SimpleNamespace(
        input_sha256="e" * 64,
        model_dump=lambda **_kwargs: {"input": "synthetic"},
    )
    counts = {"parsed": scoring.RELATION_REQUEST_COUNT}
    receipt = scoring._receipt(
        plan=plan,
        lease=lease,
        facts=facts,
        relations=relations,
        counts=counts,
        analysis_input=analysis_input,
        budget=scoring.SharedProviderBudget(scoring.OPERATOR_COST_CEILING_USD),
    )
    for name, payload in (
        ("lease.json", lease),
        ("receipt.json", receipt),
        ("relation-results.json", relations.model_dump(mode="json")),
        ("analysis-input.json", analysis_input.model_dump(mode="json")),
    ):
        (source.scoring / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(scoring, "_checked_sources", lambda **_kwargs: source)
    monkeypatch.setattr(scoring, "_facts", lambda _source: facts)
    monkeypatch.setattr(scoring, "_checked_plan", lambda _source, _facts: plan)
    monkeypatch.setattr(scoring, "_prepared", lambda *_args: (object(),))
    monkeypatch.setattr(scoring, "restore_relation_budget", lambda *_args: None)
    monkeypatch.setattr(
        scoring, "read_pipeline_relation_stage", lambda **_kwargs: (relations, counts)
    )
    monkeypatch.setattr(
        scoring, "materialize_main_analysis_input", lambda **_kwargs: analysis_input
    )
    monkeypatch.setattr(
        scoring.OpenAIChatCompletionsGatewayAdapter,
        "from_environment",
        lambda **_kwargs: pytest.fail("read-only verification must never call a provider"),
    )
    assert scoring.verify_scoring(root=source.root) == receipt
    (source.scoring / "analysis-input.json").write_text('{"input":"tampered"}', encoding="utf-8")
    with pytest.raises(scoring.MainRecoveryScoringError, match="do not reconcile"):
        scoring.verify_scoring(root=source.root)
