from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import tiktoken

from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionError,
    RepositoryExecutionState,
    build_execution_authorization,
    build_execution_preflight,
    load_execution_evidence_census,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    ClaimCorpusAttemptStore,
    build_execution_lease,
    build_live_requests,
    run_live_execution,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_json
from aletheia_lab.evaluation.observed_evidence_receipt import ObservedEvidenceReceipt
from aletheia_lab.filesystem import ImmutablePublicationConflictError
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


def _inputs():  # type: ignore[no-untyped-def]
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
    receipt = ObservedEvidenceReceipt.model_validate_json(
        (ROOT / "configs/evaluation/claim_support_observed_evidence_receipt.json").read_bytes()
    )
    authorization = build_execution_authorization(
        ROOT,
        repository_state=state,
        evidence_census=evidence,
        evidence_receipt=receipt,
        authorized_at="2026-09-03T00:00:00Z",
    )
    prepared = build_live_requests(
        ROOT,
        repository_state=state,
        authorization=authorization,
        evidence_census=evidence,
        evidence_receipt=receipt,
    )
    return state, evidence, receipt, authorization, prepared


def _model_adapter(prepared):  # type: ignore[no-untyped-def]
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
    return DeterministicFakeAdapter(
        binding=ProviderBinding.from_model_policy(
            model_requests[0].request.initial_attempt.model_policy
        ),
        fixtures=tuple(
            FakeFixture(
                request_identity_sha256=item.request.initial_attempt.request_identity_sha256,
                steps=(FakeStep(kind="abstention", raw_content=raw, usage=usage),),
            )
            for item in model_requests
        ),
    )


def test_live_requests_preserve_exact_frozen_schedule_and_token_accounting() -> None:
    state, evidence, receipt, authorization, prepared = _inputs()

    preflight = build_execution_preflight(
        ROOT,
        repository_state=state,
        credential_present=True,
        evidence_census=evidence,
        evidence_receipt=receipt,
        authorization=authorization,
    )
    assert preflight.status == "claim_corpus_live_execution_ready"
    assert preflight.live_blockers == ()

    assert len(prepared) == 360
    assert sum(item.route == "model_gateway" for item in prepared) == 315
    assert sum(item.route == "deterministic_local" for item in prepared) == 45
    assert len({item.request_sha256 for item in prepared}) == 360
    assert len(
        {item.request.initial_attempt.request_identity_sha256 for item in prepared}
    ) == 360
    by_context = {binding.visible_context.context_sha256 for binding in evidence.bindings}
    assert {item.request.context.context_sha256 for item in prepared} == by_context
    ledgers = tuple(item.authority.tool_ledger for item in prepared if item.authority.tool_ledger)
    assert len(ledgers) == 135
    assert {ledger.variant for ledger in ledgers} == {"B2", "CodeGraph", "FULL"}
    assert all(
        item.authority.variant_binding.context_sha256
        == item.request.context.context_sha256
        for item in prepared
    )
    assert all(
        item.authority.variant_binding.tool_ledger_sha256
        == (item.authority.tool_ledger.ledger_sha256 if item.authority.tool_ledger else None)
        for item in prepared
    )

    encoding = tiktoken.get_encoding("o200k_base")
    input_tokens = 0
    for item in prepared:
        if item.route != "model_gateway":
            continue
        context_json = canonical_execution_json(item.request.context.model_payload())
        input_tokens += 3
        for role, content in (
            ("system", item.request.prompt_text),
            ("user", context_json),
        ):
            input_tokens += 3 + len(encoding.encode(role)) + len(encoding.encode(content))
        assert "family_id" not in context_json
        assert "evidence_condition" not in context_json
        assert "mechanism" not in context_json
    assert input_tokens == receipt.diagnosis_input_token_count_exact == 296_071


def test_execution_lease_binds_store_without_disclosing_path(tmp_path: Path) -> None:
    _, _, _, authorization, _ = _inputs()
    first = build_execution_lease(authorization, tmp_path / "first")
    second = build_execution_lease(authorization, tmp_path / "second")

    assert first.authorization_sha256 == authorization.authorization_sha256
    assert first.store_location_sha256 != second.store_location_sha256
    assert str(tmp_path) not in first.model_dump_json()


def test_partial_request_blocks_before_any_additional_execution(tmp_path: Path) -> None:
    _, evidence, _, authorization, prepared = _inputs()
    clock = _Clock()
    store = ClaimCorpusAttemptStore(tmp_path / "store", clock=clock)
    shards = store.shards(prepared)
    first = prepared[0].request
    first_identity = first.initial_attempt.request_identity_sha256
    shards[first_identity].prepare(first)

    with pytest.raises(ClaimCorpusExecutionError, match="partial request"):
        run_live_execution(
            prepared,
            authorization=authorization,
            evidence_census=evidence,
            store=store,
            model_adapter=_model_adapter(prepared),
            clock=clock,
        )
    assert all(not shard.list_terminal_requests() for shard in shards.values())


def test_wrong_route_census_and_authority_tampering_fail_before_execution(
    tmp_path: Path,
) -> None:
    _, evidence, _, authorization, prepared = _inputs()
    clock = _Clock()
    store = ClaimCorpusAttemptStore(tmp_path / "store", clock=clock)

    with pytest.raises(ClaimCorpusExecutionError, match="exact frozen route census"):
        run_live_execution(
            prepared[:-1],
            authorization=authorization,
            evidence_census=evidence,
            store=store,
            model_adapter=_model_adapter(prepared),
            clock=clock,
        )

    store.shards(prepared)
    identity = prepared[0].request.initial_attempt.request_identity_sha256
    (store.authority_root / f"{identity}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ImmutablePublicationConflictError):
        store.shards(prepared)


def test_complete_fake_execution_is_terminal_and_idempotent(tmp_path: Path) -> None:
    _, evidence, _, authorization, prepared = _inputs()
    clock = _Clock()
    store = ClaimCorpusAttemptStore(tmp_path / "store", clock=clock)
    adapter = _model_adapter(prepared)

    first = run_live_execution(
        prepared,
        authorization=authorization,
        evidence_census=evidence,
        store=store,
        model_adapter=adapter,
        clock=clock,
    )
    replay = run_live_execution(
        prepared,
        authorization=authorization,
        evidence_census=evidence,
        store=store,
        model_adapter=adapter,
        clock=clock,
    )

    assert first.gateway_status_counts == {"parsed": 360}
    assert first.provider_backed_requests_started == 315
    assert first.deterministic_requests_started == 45
    assert first.provider_attempt_count == 315
    assert first.technical_attempt_count == 360
    assert not first.claims_materialized
    assert replay.newly_executed_request_count == 0
    assert replay.terminal_replay_skip_count == 360
    assert replay.terminal_store_sha256 == first.terminal_store_sha256
