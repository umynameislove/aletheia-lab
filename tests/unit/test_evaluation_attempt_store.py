"""Adversarial tests for the immutable evaluation attempt store."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from typing import Literal

import pytest

from aletheia_lab.context.evaluation_context import EvaluationContextPayload
from aletheia_lab.evaluation.attempt_store import (
    AttemptStoreConflictError,
    AttemptStoreIntegrityError,
    AttemptStoreTransitionError,
    ImmutableAttemptStore,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import (
    DeterministicFakeAdapter,
    FakeFixture,
    FakeStep,
    GatewayExecutionResult,
    GatewayRequest,
    ProviderBinding,
    RuntimePolicyReference,
    UsageMetadata,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
}


def _sha(character: str) -> str:
    return character * 64


def _opaque(character: str) -> str:
    return f"ev-{_sha(character)}"


class _Clock:
    def __init__(self, *, start: int = 0, step: int = 1) -> None:
        self._value = start
        self._step = step

    def now_ns(self) -> int:
        value = self._value
        self._value += self._step
        return value


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class _BarrierClock:
    def __init__(self) -> None:
        self._barrier = Barrier(2)
        self._lock = Lock()
        self._value = 100

    def now_ns(self) -> int:
        self._barrier.wait()
        with self._lock:
            value = self._value
            self._value += 1
        return value


def _request(*, manifest_character: str = "3") -> GatewayRequest:
    manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{_sha('1')}",
        snapshot_id=f"p3-snapshot-{_sha('2')}",
        manifest_content_sha256=_sha(manifest_character),
        source_commit_ref="4" * 40,
        authorization_state="authorized",
        authorization_ref=_opaque("5"),
        provenance_sha256=_sha("6"),
        created_at="2026-08-30T00:00:00Z",
        frozen_at="2026-08-30T00:00:01Z",
        visibility="diagnosis",
    )
    case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque("7"),
        family_id=_opaque("8"),
        mechanism_id=_opaque("9"),
        dataset_id=_opaque("a"),
        variant_id=_opaque("b"),
        variant_content_sha256=_sha("c"),
        case_content_sha256=_sha("d"),
        evidence_bundle_id=f"p3-evidence-bundle-{_sha('e')}",
        evidence_content_sha256=_sha("f"),
        lineage_graph_id=f"p3-lineage-graph-{_sha('0')}",
        lineage_sha256=_sha("1"),
        visibility_projection_sha256=_sha("2"),
        provenance_sha256=_sha("3"),
        visibility="diagnosis",
    )
    schema_json = canonical_project_json(_SCHEMA)
    policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=_sha("4"),
        provider_ref=_opaque("5"),
        model_ref=_opaque("6"),
        model_version_ref=_opaque("7"),
        resource_policy_ref=_opaque("8"),
        prompt_policy_ref=_opaque("9"),
        response_schema_sha256=content_sha256(schema_json.encode()),
        provenance_sha256=_sha("a"),
        visibility="diagnosis",
    )
    context_fields = {
        "schema_version": "evaluation-context/v1",
        "project_id": case.project_id,
        "snapshot_id": case.snapshot_id,
        "case_reference_id": case.reference_id,
        "case_id": case.case_id,
        "evidence_bundle_id": case.evidence_bundle_id,
        "visibility_projection_sha256": case.visibility_projection_sha256,
        "selected_evidence": [],
        "omitted_evidence_ids": [],
    }
    context_sha = canonical_execution_sha256(context_fields)
    context = EvaluationContextPayload(
        context_id=f"ev-{context_sha}",
        context_sha256=context_sha,
        project_id=case.project_id,
        snapshot_id=case.snapshot_id,
        case_reference_id=case.reference_id,
        case_id=case.case_id,
        evidence_bundle_id=case.evidence_bundle_id,
        visibility_projection_sha256=case.visibility_projection_sha256,
        selected_evidence=(),
        omitted_evidence_ids=(),
    )
    runtime_policy = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=policy,
        retry_policy_ref=_opaque("b"),
        timeout_ns=1_000_000_000,
        max_attempts=2,
        max_response_bytes=256,
        provenance_sha256=_sha("c"),
    )
    return prepare_gateway_request(
        manifest=manifest,
        case=case,
        model_policy=policy,
        context=context,
        prompt_text="immutable attempt store fixture",
        response_schema=_SCHEMA,
        runtime_policy=runtime_policy,
    )


def _usage() -> UsageMetadata:
    return UsageMetadata(
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost_amount=None,
        cost_currency_ref=None,
    )


def _step(
    kind: Literal["valid_response", "transient_error", "permanent_error"],
) -> FakeStep:
    if kind == "valid_response":
        return FakeStep(kind=kind, raw_content=b'{"value":"ok"}', usage=_usage())
    return FakeStep(kind=kind, raw_content=None, usage=None)


def _result(
    request: GatewayRequest,
    *,
    steps: tuple[FakeStep, ...] = (_step("valid_response"),),
    clock_start: int = 0,
) -> GatewayExecutionResult:
    adapter = DeterministicFakeAdapter(
        binding=ProviderBinding.from_model_policy(request.initial_attempt.model_policy),
        fixtures=(
            FakeFixture(
                request_identity_sha256=request.initial_attempt.request_identity_sha256,
                steps=steps,
            ),
        ),
    )
    return execute_gateway_request(
        request,
        adapter=adapter,
        clock=_Clock(start=clock_start),
        cancellation=_NeverCancelled(),
    )


def _record_until(
    store: ImmutableAttemptStore,
    request: GatewayRequest,
    result: GatewayExecutionResult,
    state: Literal[
        "started",
        "attempt_recorded",
        "response_recorded",
        "parsed_or_failed",
        "closeout_pending",
        "terminal_published",
    ],
) -> None:
    store.prepare(request)
    store.start(request)
    if state == "started":
        return
    for record in result.attempts:
        store.record_attempt(request, record)
    if state == "attempt_recorded":
        return
    if result.raw_response is not None:
        store.record_response(request, result)
        if state == "response_recorded":
            return
    store.record_parsed_or_failed(request, result)
    if state == "parsed_or_failed":
        return
    store.mark_closeout_pending(request, result)
    if state == "closeout_pending":
        return
    store.publish_terminal(request, result)


def test_complete_lifecycle_has_explicit_states_and_atomic_terminal_index(
    tmp_path: Path,
) -> None:
    request = _request()
    result = _result(
        request,
        steps=(_step("transient_error"), _step("valid_response")),
    )
    store = ImmutableAttemptStore(tmp_path, clock=_Clock(start=100))

    prepared = store.prepare(request)
    assert prepared.state == "prepared"
    assert not prepared.partial_publication
    assert not store.is_terminal(result.request_identity_sha256)
    store.start(request)
    first = store.record_attempt(request, result.attempts[0])
    second = store.record_attempt(request, result.attempts[1])
    assert first.counted_attempt and second.counted_attempt
    store.record_response(request, result)
    store.record_parsed_or_failed(request, result)
    store.mark_closeout_pending(request, result)
    terminal = store.publish_terminal(request, result)

    assert terminal.state == "terminal_published"
    assert not terminal.partial_publication
    assert store.current_state(result.request_identity_sha256) == "terminal_published"
    assert store.list_terminal_requests() == (result.request_identity_sha256,)
    assert store.prepare(request).disposition == "idempotent"


def test_identical_replay_is_noop_and_does_not_count_an_attempt(tmp_path: Path) -> None:
    request = _request()
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    store.start(request)

    created = store.record_attempt(request, result.attempts[0])
    replay = store.record_attempt(request, result.attempts[0])

    assert created.disposition == "created" and created.counted_attempt
    assert replay.disposition == "idempotent" and not replay.counted_attempt
    assert replay.sequence == created.sequence


def test_two_writers_same_identity_converge_idempotently(tmp_path: Path) -> None:
    request = _request()
    first = ImmutableAttemptStore(tmp_path, clock=_Clock(start=10))
    second = ImmutableAttemptStore(tmp_path, clock=_Clock(start=999))

    created = first.prepare(request)
    replay = second.prepare(request)

    assert created.disposition == "created"
    assert replay.disposition == "idempotent"
    assert replay.event_sha256 == created.event_sha256
    assert replay.recorded_at_ns == created.recorded_at_ns


def test_simultaneous_writers_count_one_created_event(tmp_path: Path) -> None:
    request = _request()
    clock = _BarrierClock()
    first = ImmutableAttemptStore(tmp_path, clock=clock)
    second = ImmutableAttemptStore(tmp_path, clock=clock)

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(
            executor.map(lambda store: store.prepare(request), (first, second))
        )

    assert sorted(receipt.disposition for receipt in receipts) == ["created", "idempotent"]
    assert receipts[0].event_sha256 == receipts[1].event_sha256


def test_different_duplicate_attempt_is_rejected_as_replay(tmp_path: Path) -> None:
    request = _request()
    original = _result(request, clock_start=0)
    changed = _result(request, clock_start=50)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    store.start(request)
    store.record_attempt(request, original.attempts[0])

    with pytest.raises(AttemptStoreTransitionError, match="ordinal"):
        store.record_attempt(request, changed.attempts[0])


def test_invalid_transition_fails_closed_without_terminal_state(tmp_path: Path) -> None:
    request = _request()
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())

    with pytest.raises(AttemptStoreTransitionError, match="not allowed"):
        store.start(request)
    assert store.current_state(request.initial_attempt.request_identity_sha256) is None


def test_crash_after_raw_response_does_not_mint_terminal_state(tmp_path: Path) -> None:
    request = _request()
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_until(store, request, result, "response_recorded")

    reopened = ImmutableAttemptStore(tmp_path, clock=_Clock(start=50))
    assert reopened.current_state(result.request_identity_sha256) == "response_recorded"
    assert not reopened.is_terminal(result.request_identity_sha256)
    assert reopened.list_terminal_requests() == ()


def test_crash_during_closeout_remains_pending_and_nonterminal(tmp_path: Path) -> None:
    request = _request()
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_until(store, request, result, "closeout_pending")

    reopened = ImmutableAttemptStore(tmp_path, clock=_Clock(start=50))
    assert reopened.current_state(result.request_identity_sha256) == "closeout_pending"
    assert not reopened.is_terminal(result.request_identity_sha256)


def test_stale_stage_file_is_never_promoted_or_counted(tmp_path: Path) -> None:
    request = _request()
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    ledger = tmp_path / "requests" / request.initial_attempt.request_identity_sha256 / "ledger"
    stale = ledger / ".00000002.json.synthetic.stage"
    stale.write_bytes(b'{"forged":"partial"}')

    reopened = ImmutableAttemptStore(tmp_path, clock=_Clock())
    assert reopened.current_state(request.initial_attempt.request_identity_sha256) == "prepared"
    assert not reopened.is_terminal(request.initial_attempt.request_identity_sha256)


def test_truncated_ledger_json_fails_integrity_check(tmp_path: Path) -> None:
    request = _request()
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    entry = (
        tmp_path
        / "requests"
        / request.initial_attempt.request_identity_sha256
        / "ledger"
        / "00000001.json"
    )
    entry.write_bytes(b'{"schema_version":')

    with pytest.raises(AttemptStoreIntegrityError, match="truncated"):
        ImmutableAttemptStore(tmp_path, clock=_Clock())


def test_forged_ledger_link_is_detected_on_restart(tmp_path: Path) -> None:
    request = _request()
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    store.start(request)
    entry = (
        tmp_path
        / "requests"
        / request.initial_attempt.request_identity_sha256
        / "ledger"
        / "00000002.json"
    )
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["previous_entry_sha256"] = "0" * 64
    entry.write_bytes((canonical_project_json(payload) + "\n").encode("utf-8"))

    with pytest.raises(AttemptStoreIntegrityError, match="link"):
        ImmutableAttemptStore(tmp_path, clock=_Clock())


def test_forged_ledger_content_hash_is_detected_on_restart(tmp_path: Path) -> None:
    request = _request()
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    entry = (
        tmp_path
        / "requests"
        / request.initial_attempt.request_identity_sha256
        / "ledger"
        / "00000001.json"
    )
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["event_sha256"] = "0" * 64
    payload["entry_id"] = f"ev-{'0' * 64}"
    entry.write_text(canonical_project_json(payload) + "\n", encoding="utf-8")

    with pytest.raises(AttemptStoreIntegrityError, match="truncated or invalid"):
        ImmutableAttemptStore(tmp_path, clock=_Clock())


def test_self_consistent_but_invalid_persisted_transition_is_rejected(
    tmp_path: Path,
) -> None:
    request = _request()
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    entry = (
        tmp_path
        / "requests"
        / request.initial_attempt.request_identity_sha256
        / "ledger"
        / "00000001.json"
    )
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["state"] = "started"
    event_payload = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "entry_id",
            "event_sha256",
            "sequence",
            "previous_entry_sha256",
            "recorded_at_ns",
        }
    }
    event_sha = canonical_execution_sha256(event_payload)
    payload["entry_id"] = f"ev-{event_sha}"
    payload["event_sha256"] = event_sha
    entry.write_bytes((canonical_project_json(payload) + "\n").encode("utf-8"))

    with pytest.raises(AttemptStoreIntegrityError, match="persisted store transition"):
        ImmutableAttemptStore(tmp_path, clock=_Clock())


def test_tampered_object_bytes_block_all_further_writes(tmp_path: Path) -> None:
    request = _request()
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    store.start(request)
    receipt = store.record_attempt(request, result.attempts[0])
    ledger = (
        tmp_path
        / "requests"
        / result.request_identity_sha256
        / "ledger"
        / f"{receipt.sequence:08d}.json"
    )
    entry = json.loads(ledger.read_text(encoding="utf-8"))
    digest = entry["attempt_record_sha256"]
    object_path = tmp_path / "objects" / "sha256" / digest[:2] / digest[2:]
    object_path.write_bytes(b"different bytes")

    with pytest.raises(AttemptStoreIntegrityError, match="content-addressed object hash"):
        store.record_attempt(request, result.attempts[0])


def test_overwrite_with_different_immutable_bytes_is_blocked(tmp_path: Path) -> None:
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    destination = tmp_path / "failures" / f"{'1' * 64}.json"
    store._atomic_create(destination, b"first")

    with pytest.raises(AttemptStoreConflictError, match="overwrite"):
        store._atomic_create(destination, b"second")


def test_cross_manifest_attempt_and_response_injection_are_rejected(tmp_path: Path) -> None:
    request = _request(manifest_character="3")
    foreign_request = _request(manifest_character="d")
    result = _result(request)
    foreign_result = _result(foreign_request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    store.prepare(request)
    store.start(request)

    with pytest.raises(AttemptStoreTransitionError, match="immutable request"):
        store.record_attempt(request, foreign_result.attempts[0])
    store.record_attempt(request, result.attempts[0])
    with pytest.raises(AttemptStoreTransitionError, match="another request"):
        store.record_response(request, foreign_result)


def test_uppercase_request_directory_is_rejected_cross_platform(tmp_path: Path) -> None:
    ImmutableAttemptStore(tmp_path, clock=_Clock())
    forged = tmp_path / "requests" / ("A" * 64)
    (forged / "ledger").mkdir(parents=True)

    with pytest.raises(AttemptStoreIntegrityError, match="canonical lowercase"):
        ImmutableAttemptStore(tmp_path, clock=_Clock())


def test_atomic_terminal_failure_leaves_closeout_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    result = _result(request)
    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_until(store, request, result, "closeout_pending")

    def fail_link(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic atomic publication failure")

    monkeypatch.setattr("aletheia_lab.evaluation.attempt_store.os.link", fail_link)
    with pytest.raises(OSError, match="synthetic"):
        store.publish_terminal(request, result)

    assert store.current_state(result.request_identity_sha256) == "closeout_pending"
    assert not store.is_terminal(result.request_identity_sha256)
    assert not tuple(tmp_path.rglob("*.stage"))


def test_failure_receipt_is_public_safe_and_has_no_scientific_disposition(
    tmp_path: Path,
) -> None:
    request = _request()
    store = ImmutableAttemptStore(tmp_path, clock=_Clock(start=500))
    receipt = store.record_failure(
        request,
        stage="terminal_publish",
        exception_class="attempt_store_conflict",
        error_code="conflict",
        private_message="SYNTHETIC_SECRET at C:\\private\\response.json",
        partial_publication=False,
        attempt=request.initial_attempt,
    )
    serialized = receipt.model_dump_json()

    assert receipt.retry_policy_ref == request.runtime_policy.retry_policy_ref
    assert receipt.request_identity_sha256 == request.initial_attempt.request_identity_sha256
    assert not receipt.partial_publication
    assert "SYNTHETIC_SECRET" not in serialized
    assert "private" not in serialized
    assert "scientific_failure" not in serialized
    ImmutableAttemptStore(tmp_path, clock=_Clock(start=999))


def test_store_does_not_mutate_read_only_source_models(tmp_path: Path) -> None:
    request = _request()
    result = _result(request)
    request_before = request.model_dump_json()
    result_before = result.model_dump_json()

    store = ImmutableAttemptStore(tmp_path, clock=_Clock())
    _record_until(store, request, result, "terminal_published")

    assert request.model_dump_json() == request_before
    assert result.model_dump_json() == result_before
