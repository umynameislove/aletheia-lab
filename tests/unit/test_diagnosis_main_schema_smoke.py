"""Outcome-blind tests for the one-call main-schema compatibility gate."""

from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from aletheia_lab.diagnosis.main_schema_smoke import (
    FORMAT_ONLY_REPAIR_SHA256,
    MAIN_RECOVERY_TRANSPORT_V2_SHA256,
    SMOKE_DESTINATION,
    SYNTHETIC_EVIDENCE_ID,
    OpenAIMainRecoveryAdapter,
    SmokeResponseValidationError,
    _strip_boundary_whitespace,
    assess_smoke_response,
    build_smoke_plan,
    build_synthetic_main_schema_request,
    exact_outbound_payload,
    main_provider_wire_schema,
    provider_call,
    validate_self_hash,
    validate_smoke_response,
)
from aletheia_lab.model_gateway import execute_gateway_request
from aletheia_lab.model_gateway.schema import validate_response_payload
from aletheia_lab.project.identity import canonical_project_json, content_sha256

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "1" * 40


def _load_smoke_cli() -> ModuleType:
    script = ROOT / "scripts" / "diagnosis_main_schema_smoke.py"
    spec = importlib.util.spec_from_file_location("diagnosis_main_schema_smoke_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke_cli = _load_smoke_cli()


def _response(*, claim_id: str = "claim-1", material_text: str | None = None) -> bytes:
    text = material_text or "The synthetic counter is 7."
    return canonical_project_json(
        {
            "schema_version": "diagnosis-main-provider-output/1",
            "output_status": "completed",
            "atomic_claims": [
                {
                    "claim_local_id": claim_id,
                    "claim_type": "evidence_statement",
                    "claim_text": "The synthetic counter is 7.",
                    "material_parts": [{"part_id": "part-1", "text": text}],
                    "visible_evidence_ids": [SYNTHETIC_EVIDENCE_ID],
                }
            ],
            "abstention_reason": None,
        }
    ).encode()


def test_smoke_payload_is_synthetic_single_call_and_keeps_frozen_transport() -> None:
    request, policy = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = exact_outbound_payload(request, policy)
    assert payload["model"] == "gpt-4.1-2025-04-14"
    assert payload["max_tokens"] == 600
    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 1.0
    assert payload["seed"] == 17
    assert payload["store"] is False and payload["stream"] is False
    assert payload["timeout"] == 60.0
    assert '"pattern"' not in json.dumps(payload["response_format"])
    claim_id_wire = payload["response_format"]["json_schema"]["schema"]["properties"][
        "atomic_claims"
    ]["items"]["properties"]["claim_local_id"]
    assert claim_id_wire == {
        "type": "string",
        "enum": ["claim-1", "claim-2", "claim-3", "claim-4", "claim-5"],
    }
    assert "synthetic" in str(payload["messages"][1]).lower()

    plan = build_smoke_plan(
        ROOT,
        source_commit_ref=COMMIT,
        predecessor_receipt_sha256="2" * 64,
        predecessor_tree_sha256="3" * 64,
        prior_smoke_receipt_sha256="4" * 64,
    )
    validate_self_hash(plan, "plan_sha256")
    assert plan["destination"] == SMOKE_DESTINATION
    assert plan["provider_call_count"] == 1
    assert plan["sdk_retries"] == 0
    assert plan["synthetic_payload_only"] is True
    assert plan["recovery_authorized"] is False
    assert plan["transport_sha256"] == MAIN_RECOVERY_TRANSPORT_V2_SHA256
    assert plan["format_only_repair_policy_sha256"] == FORMAT_ONLY_REPAIR_SHA256
    assert plan["prior_smoke_receipt_sha256"] == "4" * 64


def test_smoke_validates_against_original_schema_without_new_text_ceiling() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    validate_smoke_response(_response(), request=request)
    validate_smoke_response(_response(material_text="x" * 1500), request=request)

    with pytest.raises(ValueError):
        validate_smoke_response(_response(claim_id="claim-99"), request=request)


def test_smoke_plan_is_deterministic_and_tamper_evident() -> None:
    arguments = {
        "source_commit_ref": COMMIT,
        "predecessor_receipt_sha256": "2" * 64,
        "predecessor_tree_sha256": "3" * 64,
        "prior_smoke_receipt_sha256": "4" * 64,
    }
    first = build_smoke_plan(ROOT, **arguments)
    second = build_smoke_plan(ROOT, **arguments)
    assert first == second
    first["provider_call_count"] = 2
    with pytest.raises(ValueError, match="plan_sha256"):
        validate_self_hash(first, "plan_sha256")


def test_plan_payload_equals_the_actual_adapter_invocation() -> None:
    request, policy = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    captured: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object) -> object:
            captured.append(kwargs)
            return SimpleNamespace(
                id="chatcmpl-synthetic-smoke",
                model="gpt-4.1-2025-04-14",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=_response().decode(),
                            refusal=None,
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    adapter = OpenAIMainRecoveryAdapter(
        client=client,
        model_policy=request.initial_attempt.model_policy,
        policy=policy,
    )
    envelope = adapter.invoke(provider_call(request))
    validate_smoke_response(envelope.raw_response.content, request=request)
    assert captured == [exact_outbound_payload(request, policy)]


def test_projection_removes_only_patterns_and_preserves_original_local_gate() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    original = json.loads(request.response_schema_json)
    original["properties"]["pattern"] = {"type": "string"}
    original["required"].append("pattern")
    projected = main_provider_wire_schema(canonical_project_json(original))
    assert '"pattern"' in json.dumps(original)
    assert "pattern" in projected["properties"]
    assert '"pattern": "' not in json.dumps(projected)

    valid = json.loads(_response())
    valid["pattern"] = "a retained data field"
    validate_response_payload(valid, original)
    validate_response_payload(valid, projected)
    valid["atomic_claims"][0]["claim_local_id"] = "claim-99"
    with pytest.raises(ValueError):
        validate_response_payload(valid, original)
    with pytest.raises(ValueError):
        validate_response_payload(valid, projected)


def test_successor_wire_diff_is_only_the_equivalent_claim_id_enum() -> None:
    request, policy = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    new = exact_outbound_payload(request, policy)
    old = deepcopy(new)
    old["response_format"]["json_schema"]["schema"]["properties"]["atomic_claims"]["items"][
        "properties"
    ]["claim_local_id"].pop("enum")
    assert smoke_cli._only_claim_id_enum_changed(old, new)
    old["messages"][0]["content"] = "changed prompt"
    assert not smoke_cli._only_claim_id_enum_changed(old, new)


def test_successor_accepts_only_boundary_format_repair() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = json.loads(_response())
    payload["atomic_claims"][0]["claim_text"] = "  The synthetic counter is 7. \n"
    payload["atomic_claims"][0]["material_parts"][0]["text"] = " The synthetic counter is 7. "
    payload["atomic_claims"][0]["visible_evidence_ids"] = [f" {SYNTHETIC_EVIDENCE_ID} "]
    raw = canonical_project_json(payload).encode()
    assessment = assess_smoke_response(raw, request=request)
    assert assessment.payload == json.loads(_response())
    assert assessment.format_repaired_fields == (
        "atomic_claims[0].claim_text",
        "atomic_claims[0].material_parts[0].text",
        "atomic_claims[0].visible_evidence_ids[0]",
    )
    assert len(assessment.accepted_payload_sha256) == 64
    assert json.loads(raw) == payload


def test_successor_keeps_valid_response_byte_semantics_unchanged() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    assessment = assess_smoke_response(_response(), request=request)
    assert assessment.format_repaired_fields == ()
    assert assessment.payload == json.loads(_response())


def test_repair_changes_only_fields_that_failed_original_patterns() -> None:
    payload = json.loads(_response())
    payload["atomic_claims"][0]["claim_text"] = " Claim text "
    payload["atomic_claims"][0]["material_parts"][0]["text"] = " Material text "
    repaired, fields = _strip_boundary_whitespace(payload, ("atomic_claims[0].claim_text",))
    assert fields == ("atomic_claims[0].claim_text",)
    assert repaired["atomic_claims"][0]["claim_text"] == "Claim text"
    assert repaired["atomic_claims"][0]["material_parts"][0]["text"] == " Material text "
    assert payload["atomic_claims"][0]["claim_text"] == " Claim text "


@pytest.mark.parametrize(
    ("path", "value", "expected_path"),
    [
        (
            ("material_parts", 0, "part_id"),
            " part-1 ",
            "atomic_claims[0].material_parts[0].part_id",
        ),
        (("abstention_reason",), " Insufficient evidence ", "abstention_reason"),
    ],
)
def test_successor_covers_every_boundary_field_family(
    path: tuple[str | int, ...], value: str, expected_path: str
) -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = json.loads(_response())
    if path == ("abstention_reason",):
        payload["output_status"] = "abstained"
        payload["abstention_reason"] = value
    else:
        target = payload["atomic_claims"][0]
        for segment in path[:-1]:
            target = target[segment]
        target[path[-1]] = value
    assessment = assess_smoke_response(canonical_project_json(payload).encode(), request=request)
    assert assessment.format_repaired_fields == (expected_path,)


def test_successor_rejects_claim_id_collision() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = json.loads(_response())
    duplicate = json.loads(_response())["atomic_claims"][0]
    payload["atomic_claims"].append(duplicate)
    with pytest.raises(SmokeResponseValidationError) as caught:
        assess_smoke_response(canonical_project_json(payload).encode(), request=request)
    assert caught.value.code == "semantic_contract_invalid"


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("claim_local_id", "claim-99", "wire_schema_invalid"),
        ("claim_local_id", " claim-1 ", "wire_schema_invalid"),
        ("claim_text", "The synthetic\ncounter is 7.", "unrepaired_pattern_mismatch"),
        ("claim_text", "   ", "unrepaired_pattern_mismatch"),
    ],
)
def test_successor_rejects_non_boundary_or_empty_repairs(
    field: str, value: str, expected_code: str
) -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = json.loads(_response())
    payload["atomic_claims"][0][field] = value
    with pytest.raises(SmokeResponseValidationError) as caught:
        assess_smoke_response(canonical_project_json(payload).encode(), request=request)
    assert caught.value.code == expected_code
    assert caught.value.fields == (
        () if expected_code == "wire_schema_invalid" else (f"atomic_claims[0].{field}",)
    )
    assert value not in str(caught.value)


def test_successor_does_not_repair_semantic_or_wire_failures() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = json.loads(_response())
    payload["atomic_claims"][0]["visible_evidence_ids"] = ["ev-invented"]
    with pytest.raises(SmokeResponseValidationError) as caught:
        assess_smoke_response(canonical_project_json(payload).encode(), request=request)
    assert caught.value.code == "semantic_contract_invalid"

    payload["atomic_claims"][0]["claim_type"] = "invented"
    with pytest.raises(SmokeResponseValidationError) as caught:
        assess_smoke_response(canonical_project_json(payload).encode(), request=request)
    assert caught.value.code == "wire_schema_invalid"


def test_successor_cannot_pass_without_exercising_a_cited_claim() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    payload = json.loads(_response())
    payload["atomic_claims"] = []
    with pytest.raises(SmokeResponseValidationError) as caught:
        assess_smoke_response(canonical_project_json(payload).encode(), request=request)
    assert caught.value.code == "synthetic_coverage_invalid"


def test_successor_rejects_duplicate_json_keys_without_echoing_content() -> None:
    request, _ = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    raw = b'{"schema_version":"x","schema_version":"y"}'
    with pytest.raises(SmokeResponseValidationError) as caught:
        assess_smoke_response(raw, request=request)
    assert caught.value.code == "duplicate_json_key"
    assert "schema_version" not in str(caught.value)


@pytest.mark.parametrize("valid_response", [True, False])
def test_successor_cli_consumes_one_lease_and_records_safe_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_response: bool
) -> None:
    predecessor = tmp_path / "predecessor"
    prior = tmp_path / "prior"
    smoke = tmp_path / "successor"
    for directory in (predecessor, prior, smoke):
        directory.mkdir()
    plan = {
        "source_commit_ref": COMMIT,
        "predecessor_tree_sha256": "3" * 64,
        "prior_smoke_receipt_sha256": "4" * 64,
        "format_only_repair_policy_sha256": FORMAT_ONLY_REPAIR_SHA256,
        "plan_sha256": "5" * 64,
    }
    (smoke / "plan.json").write_text(canonical_project_json(plan), encoding="utf-8")
    monkeypatch.setattr(smoke_cli, "_load_and_reconcile_plan", lambda *_: plan)
    monkeypatch.setattr(smoke_cli, "_tree_sha256", lambda *_: "3" * 64)
    payload = json.loads(_response())
    if not valid_response:
        payload["atomic_claims"][0]["claim_text"] = "The synthetic\ncounter is 7."
    raw = canonical_project_json(payload).encode()
    calls: list[object] = []

    class FakeAdapter:
        def invoke(self, call: object) -> object:
            calls.append(call)
            return SimpleNamespace(
                provider_attempt_ref="ev-" + "6" * 64,
                raw_response=SimpleNamespace(
                    content=raw,
                    content_sha256=content_sha256(raw),
                    byte_count=len(raw),
                ),
                usage=SimpleNamespace(
                    model_dump=lambda **_: {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                    }
                ),
            )

    monkeypatch.setattr(
        smoke_cli.OpenAIMainRecoveryAdapter,
        "from_environment",
        lambda **_: FakeAdapter(),
    )
    args = Namespace(
        root=ROOT,
        predecessor_run=predecessor,
        prior_smoke_dir=prior,
        smoke_dir=smoke,
        confirm_plan_sha256=plan["plan_sha256"],
    )
    assert smoke_cli._execute(args) == (0 if valid_response else 1)
    receipt = json.loads((smoke / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["provider_call_count"] == 1
    assert receipt["adapter_invocation_count"] == 1
    assert receipt["raw_response_persisted"] is False
    assert receipt["recovery_authorized"] is False
    assert receipt["response_sha256"] == content_sha256(raw)
    if valid_response:
        assert receipt["status"] == "pass"
        assert receipt["accepted_payload_sha256"] is not None
        assert receipt["invalid_field_paths"] == []
    else:
        assert receipt["status"] == "response_invalid"
        assert receipt["error_code"] == "unrepaired_pattern_mismatch"
        assert receipt["accepted_payload_sha256"] is None
        assert receipt["invalid_field_paths"] == ["atomic_claims[0].claim_text"]
    assert {item.name for item in smoke.iterdir()} == {
        "plan.json",
        "lease.json",
        "receipt.json",
    }
    assert len(calls) == 1
    with pytest.raises(smoke_cli.SchemaSmokeError, match="already been consumed"):
        smoke_cli._execute(args)
    assert len(calls) == 1


@dataclass
class _Clock:
    value: int = 0

    def now_ns(self) -> int:
        self.value += 1
        return self.value


@dataclass(frozen=True)
class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def test_gateway_revalidates_main_output_against_the_unprojected_schema() -> None:
    request, policy = build_synthetic_main_schema_request(ROOT, source_commit_ref=COMMIT)
    calls: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(
                id="chatcmpl-invalid-local-shape",
                model="gpt-4.1-2025-04-14",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=_response(claim_id="claim-99").decode(),
                            refusal=None,
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
            )

    adapter = OpenAIMainRecoveryAdapter(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_policy=request.initial_attempt.model_policy,
        policy=policy,
    )
    result = execute_gateway_request(
        request,
        adapter=adapter,
        clock=_Clock(),
        cancellation=_NeverCancelled(),
    )
    assert result.status == "parse_failed"
    assert result.parsed_response is None
    assert len(calls) == 1
