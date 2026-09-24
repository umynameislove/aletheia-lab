"""Offline guards for the forward-only recovery scoring boundary."""

from __future__ import annotations

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
