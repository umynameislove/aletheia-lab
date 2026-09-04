from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_execution import (
    RepositoryExecutionState,
    build_execution_authorization,
    load_execution_evidence_census,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    ClaimCorpusAttemptStore,
    build_execution_lease,
    build_live_requests,
    run_live_execution,
)
from aletheia_lab.evaluation.claim_corpus_reconciliation import (
    ClaimCorpusReconciliationError,
    reconcile_claim_corpus_execution,
)
from aletheia_lab.evaluation.observed_evidence_receipt import ObservedEvidenceReceipt
from aletheia_lab.model_gateway import (
    DeterministicFakeAdapter,
    FakeFixture,
    FakeStep,
    ProviderBinding,
    UsageMetadata,
)
from aletheia_lab.project.identity import canonical_project_json

ROOT = Path(__file__).resolve().parents[2]


class _Clock:
    def __init__(self) -> None:
        self.value = 0

    def now_ns(self) -> int:
        self.value += 1
        return self.value


def _execution_inputs():  # type: ignore[no-untyped-def]
    state = RepositoryExecutionState(
        branch="main",
        head_commit="1" * 40,
        origin_main_commit="1" * 40,
        clean=True,
    )
    evidence = load_execution_evidence_census(
        ROOT,
        ROOT / "configs/evaluation/claim_support_observed_evidence_census.json",
    )
    evidence_receipt = ObservedEvidenceReceipt.model_validate_json(
        (
            ROOT / "configs/evaluation/claim_support_observed_evidence_receipt.json"
        ).read_bytes()
    )
    authorization = build_execution_authorization(
        ROOT,
        repository_state=state,
        evidence_census=evidence,
        evidence_receipt=evidence_receipt,
        authorized_at="2026-09-03T00:00:00Z",
    )
    prepared = build_live_requests(
        ROOT,
        repository_state=state,
        authorization=authorization,
        evidence_census=evidence,
        evidence_receipt=evidence_receipt,
    )
    return evidence, authorization, prepared


def _mixed_adapter(prepared):  # type: ignore[no-untyped-def]
    model_requests = tuple(item for item in prepared if item.route == "model_gateway")
    raw = canonical_project_json(
        {
            "schema_version": "diagnosis-provider-output/1",
            "output_status": "abstained",
            "atomic_claims": [],
            "abstention_reason": "Insufficient visible evidence.",
        }
    ).encode()
    usage = UsageMetadata(
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        cost_amount=Decimal("0"),
        cost_currency_ref=f"ev-{'2' * 64}",
    )
    fixtures = []
    for index, item in enumerate(model_requests):
        step = (
            FakeStep(kind="permanent_error", raw_content=None, usage=None)
            if index % 4 == 0
            else FakeStep(kind="abstention", raw_content=raw, usage=usage)
        )
        fixtures.append(
            FakeFixture(
                request_identity_sha256=(
                    item.request.initial_attempt.request_identity_sha256
                ),
                steps=(step,),
            )
        )
    return DeterministicFakeAdapter(
        binding=ProviderBinding.from_model_policy(
            model_requests[0].request.initial_attempt.model_policy
        ),
        fixtures=tuple(fixtures),
    )


def test_reconciliation_closes_census_without_reserve_or_downstream_outputs(
    tmp_path: Path,
) -> None:
    evidence, authorization, prepared = _execution_inputs()
    store_path = tmp_path / "attempt-store"
    clock = _Clock()
    store = ClaimCorpusAttemptStore(store_path, clock=clock)
    live_receipt = run_live_execution(
        prepared,
        authorization=authorization,
        evidence_census=evidence,
        store=store,
        model_adapter=_mixed_adapter(prepared),
        clock=clock,
    )
    lease = build_execution_lease(authorization, store_path)

    reserve, reconciliation = reconcile_claim_corpus_execution(
        ROOT,
        store_root=store_path,
        authorization=authorization,
        lease=lease,
        live_receipt=live_receipt,
        evidence_census=evidence,
    )

    expected_failures = 79
    assert live_receipt.gateway_status_counts == {
        "parsed": 360 - expected_failures,
        "provider_failed": expected_failures,
    }
    assert reserve.status == "claim_corpus_no_reserve_activation_required"
    assert reserve.provider_failure_count == expected_failures
    assert not reserve.provider_failures_eligible_for_reserve
    assert not reserve.reserve_activation_performed
    assert reserve.reserve_requests_executed == 0
    assert reconciliation.status == "claim_corpus_request_reconciliation_passed"
    assert reconciliation.terminal_request_count == 360
    assert reconciliation.parsed_terminal_count == 360 - expected_failures
    assert reconciliation.technical_failure_terminal_count == expected_failures
    assert reconciliation.provider_attempt_count == 315
    assert reconciliation.technical_attempt_count == 360
    assert reconciliation.store_integrity_verified
    assert reconciliation.exact_request_coverage_verified
    assert reconciliation.ready_for_output_normalization
    assert not reconciliation.outputs_normalized
    assert not reconciliation.claims_materialized
    assert not reconciliation.automatic_labels_generated
    assert not reconciliation.blind_packets_generated
    assert not reconciliation.human_annotations_collected
    assert not reconciliation.main_or_sealed_outcomes_opened
    assert len(reconciliation.slices) == 16
    for dimension in {item.dimension for item in reconciliation.slices}:
        items = tuple(item for item in reconciliation.slices if item.dimension == dimension)
        assert sum(item.expected_count for item in items) == 360
        assert sum(item.technical_failure_count for item in items) == expected_failures

    authority_path = next((store_path / "authorities").iterdir())
    authority_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ClaimCorpusReconciliationError, match="authority"):
        reconcile_claim_corpus_execution(
            ROOT,
            store_root=store_path,
            authorization=authorization,
            lease=lease,
            live_receipt=live_receipt,
            evidence_census=evidence,
        )


def test_reconciliation_boundary_cannot_call_provider_or_create_human_packets() -> None:
    paths = (
        ROOT / "src/aletheia_lab/evaluation/claim_corpus_reconciliation.py",
        ROOT / "scripts/claim_support_execution_reconciliation.py",
    )
    forbidden_modules = {
        "aletheia_lab.evaluation.claim_corpus_materializer",
        "aletheia_lab.evaluation.human_workflow",
        "aletheia_lab.model_gateway.openai",
        "openai",
    }
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
    assert imported.isdisjoint(forbidden_modules)
