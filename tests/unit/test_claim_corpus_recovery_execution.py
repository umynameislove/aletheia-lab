import json
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequestCensus
from aletheia_lab.evaluation.claim_corpus_execution import (
    RepositoryExecutionState,
    build_execution_plan,
    load_execution_evidence_census,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    NeverCancelled,
    PreparedClaimCorpusRequest,
    SystemMonotonicClock,
    _DeterministicB0Adapter,
)
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    PREDECESSOR_TERMINAL_STORE_SHA256,
    normalize_provider_output_v2,
)
from aletheia_lab.evaluation.claim_corpus_recovery_audit import audit_recovery_store
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import (
    RecoveryAuthorization,
    acquire_recovery_lease,
    checked_run_directory,
    destination_sha256,
    publish_recovery_json,
)
from aletheia_lab.evaluation.claim_corpus_recovery_budget import (
    AMENDED_MAX_OUTPUT_TOKENS,
    RecoveryOutputBudgetAmendment,
    load_recovery_output_budget_amendment,
)
from aletheia_lab.evaluation.claim_corpus_recovery_execution import prepare_recovery_rehearsal
from aletheia_lab.evaluation.claim_corpus_recovery_probe import build_compatibility_requests
from aletheia_lab.evaluation.claim_corpus_recovery_run import (
    _execute_compatibility,
    estimate_schedule_cost,
    execute_recovery,
    make_recovery_authorization,
    verify_completed_recovery,
)
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.observed_evidence_receipt import ObservedEvidenceReceipt
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import (
    OpenAIGatewayPolicy,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
    execute_gateway_request,
)

ROOT = Path(__file__).resolve().parents[2]
Rehearsal = tuple[tuple[PreparedClaimCorpusRequest, ...], dict[str, object]]


@pytest.fixture(scope="module")
def rehearsal() -> Rehearsal:
    return prepare_recovery_rehearsal(ROOT)


def test_recovery_schedule_is_isolated_and_non_authorizing(rehearsal: Rehearsal) -> None:
    prepared, receipt = rehearsal
    assert len(prepared) == 360
    assert receipt["model_request_count"] == 315
    assert receipt["deterministic_request_count"] == 45
    assert receipt["provider_calls_executed"] is False
    assert receipt["estimate_is_authorized_cost_ceiling"] is False
    assert receipt["provider_billed_input_tokens_known"] is False
    assert receipt["maximum_output_tokens_per_model_request"] == 2048
    assert receipt["output_budget_uniform_across_model_variants"] is True
    assert receipt["model_policy_count"] == 1
    assert receipt["live_blockers"]
    assert receipt["diagnosis_maximum_attempt_cost_estimate_usd"] == (
        estimate_schedule_cost(prepared)
    )
    assert all('"diagnosis-provider-output/2"' in p.request.response_schema_json for p in prepared)
    freeze = load_diagnosis_variant_freeze(
        ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
    )
    amended = OpenAIGatewayPolicy.from_fairness_policy(
        freeze.model_policies["main_llm_v1"]
    ).with_recovery_output_budget()
    assert {
        item.request.initial_attempt.model_policy.policy_content_sha256
        for item in prepared
        if item.route == "model_gateway"
    } == {amended.model_policy_sha256()}


def test_output_budget_amendment_is_self_authenticating_and_outcome_free() -> None:
    amendment = load_recovery_output_budget_amendment(ROOT)
    assert amendment.amended_maximum_output_tokens == 2048
    assert amendment.prior_maximum_output_tokens == 600
    assert amendment.failed_probe_count == 3
    assert amendment.failed_probe_issue_code == "provider_output_truncated"
    assert amendment.exact_truncated_output_content_known is False
    assert amendment.output_budget_uniform_across_model_variants is True
    assert amendment.provider_calls_executed is False
    assert amendment.main_or_sealed_outcomes_opened is False
    payload = amendment.model_dump(mode="python")
    payload["amended_maximum_output_tokens"] = 1200
    with pytest.raises(ValueError):
        RecoveryOutputBudgetAmendment.model_validate(payload)


class Clock:
    def __init__(self) -> None:
        self.value = 0

    def now_ns(self) -> int:
        self.value += 1
        return self.value


def test_every_b0_recovery_output_normalizes_without_changing_claim_content(
    rehearsal: Rehearsal,
) -> None:
    prepared, _ = rehearsal
    census = ClaimCorpusRequestCensus.model_validate_json(
        (ROOT / "configs/evaluation/claim_support_request_census.json").read_bytes()
    )
    frozen = {r.request_sha256: r for r in census.primary_requests}
    checked = 0
    for item in prepared:
        if item.route != "deterministic_local":
            continue
        result = execute_gateway_request(
            item.request,
            adapter=_DeterministicB0Adapter(item.request),
            clock=Clock(),
            cancellation=NeverCancelled(),
        )
        assert result.parsed_response is not None
        context = item.request.context
        assert isinstance(context, ModelVisibleEvidenceContext)
        output = normalize_provider_output_v2(
            frozen[item.request_sha256],
            result.parsed_response.payload,
            source_record_sha256="a" * 64,
            visible_evidence_ids=tuple(e.evidence_id for e in context.items),
        )
        assert len(output.atomic_claims) == len(context.items)
        assert tuple(c.claim_text for c in output.atomic_claims) == tuple(
            f"The observed evidence includes the measured item titled '{e.title}'."
            for e in context.items
        )
        checked += 1
    assert checked == 45


def test_compatibility_covers_each_schema_without_scientific_content(
    rehearsal: Rehearsal,
) -> None:
    prepared, _ = rehearsal
    probes = build_compatibility_requests(prepared)
    assert len(probes) == 3
    assert len({p.request.response_schema_json for p in probes}) == 3
    assert all(p.request.prompt_text.startswith("This is a synthetic") for p in probes)
    assert all(
        isinstance(p.request.context, ModelVisibleEvidenceContext)
        and {item.content for item in p.request.context.items} == {"The synthetic counter is 7."}
        for p in probes
    )
    assert all(
        p.authority.request_sha256 != p.request_sha256
        and p.authority.variant_binding.context_sha256 == p.request.context.context_sha256
        for p in probes
    )
    scientific = {
        p.request.initial_attempt.request_identity_sha256 for p in prepared
    }
    assert not scientific & {
        p.request.initial_attempt.request_identity_sha256 for p in probes
    }


class _SchemaValidAdapter:
    def __init__(self, binding: ProviderBinding) -> None:
        self.binding = binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        schema = json.loads(call.response_schema_json)
        evidence_id = schema["properties"]["result"]["anyOf"][0]["properties"][
            "atomic_claims"
        ]["items"]["properties"]["visible_evidence_ids"]["items"]["enum"][0]
        payload = {
            "schema_version": "diagnosis-provider-output/2",
            "result": {
                "output_status": "completed",
                "atomic_claims": [
                    {
                        "claim_type": "evidence_statement",
                        "claim_text": "The synthetic counter is 7.",
                        "material_parts": [{"text": "counter is 7"}],
                        "visible_evidence_ids": [evidence_id],
                    }
                ],
            },
        }
        raw = RawResponseArtifact.from_bytes(
            json.dumps(payload, separators=(",", ":")).encode()
        )
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=f"ev-{canonical_execution_sha256(payload)}",
            response_mode="structured",
            raw_response=raw,
            usage=UsageMetadata(
                input_tokens=None, output_tokens=None, total_tokens=None,
                cost_amount=None, cost_currency_ref=None,
            ),
        )


def test_synthetic_compatibility_store_is_independently_normalizable(
    rehearsal: Rehearsal, tmp_path: Path
) -> None:
    probes = build_compatibility_requests(rehearsal[0])
    clock = SystemMonotonicClock()
    store = ClaimCorpusAttemptStore(tmp_path / "store", clock=clock)
    adapter = _SchemaValidAdapter(
        ProviderBinding.from_model_policy(probes[0].request.initial_attempt.model_policy)
    )
    _execute_compatibility(probes, store, adapter, clock)
    audit = audit_recovery_store(ROOT, tmp_path / "store", probes)
    assert audit["terminal_request_count"] == 3
    assert audit["gateway_status_counts"] == {"parsed": 3}
    assert audit["normalized_output_count"] == 3
    assert audit["claim_candidate_count"] == 3


def _authorization(run_dir: Path) -> RecoveryAuthorization:
    state = RepositoryExecutionState(
        branch="main", head_commit="0" * 40, origin_main_commit="0" * 40, clean=True
    )
    evidence = load_execution_evidence_census(
        ROOT, ROOT / "configs/evaluation/claim_support_observed_evidence_census.json"
    )
    receipt = ObservedEvidenceReceipt.model_validate_json(
        (ROOT / "configs/evaluation/claim_support_observed_evidence_receipt.json").read_bytes()
    )
    plan = build_execution_plan(ROOT)
    amendment = load_recovery_output_budget_amendment(ROOT)
    _, rehearsal = prepare_recovery_rehearsal(ROOT)
    payload = {
        "schema_version": "claim-corpus-recovery-authorization/v2",
        "phase": "compatibility", "authorized_at": "2000-01-01T00:00:00Z",
        "source_commit_ref": state.head_commit,
        "execution_plan_sha256": plan.plan_sha256,
        "observed_evidence_census_sha256": evidence.census_sha256,
        "observed_evidence_receipt_sha256": receipt.receipt_sha256,
        "model": plan.model, "model_snapshot": plan.model_snapshot,
        "primary_request_count": plan.primary_request_count,
        "model_request_count": plan.model_request_count,
        "deterministic_request_count": plan.deterministic_request_count,
        "maximum_provider_attempts_per_request": plan.maximum_provider_attempts_per_request,
        "maximum_output_tokens_per_model_request": AMENDED_MAX_OUTPUT_TOKENS,
        "protocol_sha256": rehearsal["protocol_sha256"],
        "output_budget_amendment_sha256": amendment.amendment_sha256,
        "failed_compatibility_receipt_sha256": (
            amendment.failed_compatibility_receipt_sha256
        ),
        "failed_compatibility_store_sha256": amendment.failed_compatibility_store_sha256,
        "rehearsal_sha256": rehearsal["receipt_sha256"],
        "destination_sha256": destination_sha256(run_dir),
        "predecessor_store_sha256": PREDECESSOR_TERMINAL_STORE_SHA256,
        "compatibility_receipt_sha256": None,
        "estimated_upper_cost_usd": 1.0, "operator_cost_ceiling_usd": 1.0,
        "registered_attempts": 1, "relation_assignment_authorized": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return RecoveryAuthorization.model_validate({
        **payload, "authorization_sha256": canonical_execution_sha256(payload)
    })


def test_recovery_lease_is_single_use(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    authority = _authorization(run_dir)
    acquire_recovery_lease(run_dir, authority)
    first = (run_dir / "compatibility-lease.json").read_bytes()
    with pytest.raises(ValueError, match="already consumed"):
        acquire_recovery_lease(run_dir, authority)
    assert (run_dir / "compatibility-lease.json").read_bytes() == first


def test_recovery_directory_cannot_overlap_repository_or_predecessor(tmp_path: Path) -> None:
    predecessor = tmp_path / "preserved" / "attempt-store"
    predecessor.mkdir(parents=True)
    with pytest.raises(ValueError, match="repository"):
        checked_run_directory(ROOT, ROOT / "private", predecessor)
    with pytest.raises(ValueError, match="predecessor"):
        checked_run_directory(ROOT, predecessor.parent / "recovery", predecessor)


def test_recovery_authorization_detects_tampering(tmp_path: Path) -> None:
    authority = _authorization(tmp_path / "run")
    serialized = authority.model_dump_json()
    assert "claim-corpus-execution-authorization/v1" not in serialized
    assert "base_binding" not in serialized
    payload = authority.model_dump(mode="python")
    payload["operator_cost_ceiling_usd"] = 2.0
    with pytest.raises(ValueError, match="identity"):
        RecoveryAuthorization.model_validate(payload)


def test_compatibility_executes_once_and_verifies_from_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = RepositoryExecutionState(
        branch="main", head_commit="0" * 40, origin_main_commit="0" * 40, clean=True
    )
    run_dir = tmp_path / "run"
    predecessor = tmp_path / "preserved" / "attempt-store"
    monkeypatch.setattr(
        "aletheia_lab.evaluation.claim_corpus_recovery_run.audit_predecessor_provider_failures",
        lambda _path: {"terminal_store_sha256": PREDECESSOR_TERMINAL_STORE_SHA256},
    )
    amendment = load_recovery_output_budget_amendment(ROOT)
    monkeypatch.setattr(
        "aletheia_lab.evaluation.claim_corpus_recovery_run.audit_retired_compatibility_run",
        lambda _root, _path: {
            "receipt_sha256": amendment.failed_compatibility_receipt_sha256,
            "terminal_store_sha256": amendment.failed_compatibility_store_sha256,
        },
    )
    retired = tmp_path / "retired"
    authorization = make_recovery_authorization(
        ROOT, state=state, run_dir=run_dir, predecessor_store=predecessor,
        phase="compatibility", authorized_at="2000-01-01T00:00:00Z",
        operator_cost_ceiling_usd=1.0,
        retired_compatibility_run=retired,
    )
    publish_recovery_json(
        run_dir / "compatibility-authorization.json",
        authorization.model_dump(mode="json"),
    )
    prepared = build_compatibility_requests(prepare_recovery_rehearsal(ROOT)[0])
    adapter = _SchemaValidAdapter(
        ProviderBinding.from_model_policy(prepared[0].request.initial_attempt.model_policy)
    )
    result = execute_recovery(
        ROOT, state=state, run_dir=run_dir, predecessor_store=predecessor,
        phase="compatibility",
        confirm_authorization_sha256=authorization.authorization_sha256,
        adapter=adapter,
        retired_compatibility_run=retired,
    )
    assert result["status"] == "recovery_compatibility_pass"
    assert result["terminal_request_count"] == 3
    assert result["claim_candidate_count"] == 3
    assert verify_completed_recovery(
        ROOT, state=state, run_dir=run_dir, phase="compatibility"
    ) == result
    with pytest.raises(ValueError, match="already started"):
        execute_recovery(
            ROOT, state=state, run_dir=run_dir, predecessor_store=predecessor,
            phase="compatibility",
            confirm_authorization_sha256=authorization.authorization_sha256,
            adapter=adapter,
            retired_compatibility_run=retired,
        )
