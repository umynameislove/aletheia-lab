from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.diagnosis.main_runtime import (
    MainExecutionAuthority,
    MainRuntimeError,
    MainRuntimeStore,
    build_main_runtime_preflight,
    load_main_runtime_inputs,
    run_main_logical_request,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainFamily,
)
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainPrivateCensusPacket,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import (
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
)
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_PACKET = ROOT.parent / "memory/diagnosis-main-census-v1/private-census-packet.json"


class _Clock:
    def __init__(self) -> None:
        self.value = 0

    def now_ns(self) -> int:
        self.value += 1
        return self.value


def _opaque(payload: object) -> str:
    return f"ev-{canonical_execution_sha256(payload)}"


class _RouteAdapter:
    def __init__(self, *, invalid_final_citations: bool = False) -> None:
        self._binding = ProviderBinding(
            provider_ref=_opaque({"provider": "openai"}),
            model_ref=_opaque({"model": "gpt-4.1"}),
            model_version_ref=_opaque({"model_version": "gpt-4.1-2025-04-14"}),
        )
        self.invalid_final_citations = invalid_final_citations
        self.calls: list[ProviderCall] = []

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        self.calls.append(call)
        context = json.loads(call.context_json)["payload"]
        evidence_ids = [item["evidence_id"] for item in context["items"]]
        if "diagnosis-main-selection-output/v1" in call.response_schema_json:
            payload: dict[str, object] = {
                "schema_version": "diagnosis-main-selection-output/v1",
                "requested_evidence_ids": [evidence_ids[0]],
            }
        else:
            folded_prompt = call.prompt_text.casefold()
            citation_required = "cite" in folded_prompt and not any(
                marker in folded_prompt
                for marker in ("do not cite", "without evidence-id citations")
            )
            citations = (
                [] if self.invalid_final_citations or not citation_required else [evidence_ids[0]]
            )
            payload = {
                "schema_version": "diagnosis-main-provider-output/1",
                "output_status": "completed",
                "atomic_claims": [
                    {
                        "claim_local_id": "claim-1",
                        "claim_type": "evidence_statement",
                        "claim_text": "The visible record contains the stated measurement.",
                        "material_parts": [
                            {
                                "part_id": "part-1",
                                "text": "The visible record contains the stated measurement.",
                            }
                        ],
                        "visible_evidence_ids": citations,
                    }
                ],
                "abstention_reason": None,
            }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=_opaque(
                {
                    "request": call.request_identity_sha256,
                    "attempt": call.attempt_ordinal,
                }
            ),
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(raw),
            usage=UsageMetadata(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def _visible_context() -> ModelVisibleEvidenceContext:
    items = tuple(
        build_visible_evidence_item(
            evidence_id=evidence_id,
            kind="metric",
            title=title,
            content=canonical_execution_json({"value": value}),
            source_content_sha256=canonical_execution_sha256({"source": evidence_id}),
        )
        for evidence_id, title, value in (
            ("ev-measurement", "Observed measurement", 0.2),
            ("ev-performance", "Measured performance", 0.8),
            ("ev-provenance", "Observed provenance", 1.0),
        )
    )
    ordered = tuple(sorted(items, key=lambda item: item.evidence_id))
    payload = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": tuple(item.model_dump(mode="json") for item in ordered),
    }
    digest = canonical_execution_sha256(payload)
    return ModelVisibleEvidenceContext(
        context_id=f"ccctx-{digest}",
        items=ordered,
        context_sha256=digest,
    )


def _family() -> DiagnosisMainFamily:
    payload = {
        "family_id": "family-test",
        "mechanism": "data_drift",
        "dataset_id": "dataset-test",
        "source_unit_id": "source-test",
        "source_record_sha256": "1" * 64,
        "source_artifact_sha256": "2" * 64,
        "intervention_template_id": "template-test",
        "superfamily_id": "superfamily-test",
        "source_partition": "prior_registered_outcome",
    }
    return DiagnosisMainFamily.model_validate(
        {**payload, "family_sha256": canonical_execution_sha256(payload)}
    )


def _logical_context(visible: ModelVisibleEvidenceContext) -> DiagnosisMainContext:
    payload = {
        "context_id": visible.context_id,
        "case_family_id": "family-test",
        "evidence_condition": "full",
        "expected_response_mode": "diagnose",
        "visible_context_sha256": visible.context_sha256,
        "normalized_content_sha256": canonical_execution_sha256(
            {"normalized": visible.context_sha256}
        ),
        "semantic_cluster_id": "semantic-family-test",
        "counterevidence_source_family_id": None,
    }
    return DiagnosisMainContext.model_validate(
        {**payload, "context_sha256": canonical_execution_sha256(payload)}
    )


def _logical(visible: ModelVisibleEvidenceContext, variant: str) -> DiagnosisMainExpectedRequest:
    payload = {
        "request_id": f"dmr-{canonical_execution_sha256({'variant': variant})}",
        "context_id": visible.context_id,
        "variant": variant,
    }
    return DiagnosisMainExpectedRequest.model_validate(
        {**payload, "request_sha256": canonical_execution_sha256(payload)}
    )


def _authority(contract):  # type: ignore[no-untyped-def]
    manifest_sha = canonical_execution_sha256(
        {
            "analysis_census_sha256": contract.analysis_census_sha256,
            "analysis_plan_sha256": contract.analysis_plan_sha256,
            "response_contract_sha256": contract.response_contract_sha256,
            "runtime_contract_sha256": contract.runtime_contract_sha256,
        }
    )
    payload = {
        "schema_version": "diagnosis-main-execution-authority/v1",
        "execution_authorized": True,
        "source_commit_ref": "1" * 40,
        "analysis_census_sha256": contract.analysis_census_sha256,
        "analysis_plan_sha256": contract.analysis_plan_sha256,
        "response_contract_sha256": contract.response_contract_sha256,
        "runtime_contract_sha256": contract.runtime_contract_sha256,
        "manifest_content_sha256": manifest_sha,
        "authorization_ref": _opaque({"authorization": "unit-test"}),
        "authorized_at": "2026-09-20T00:00:00Z",
    }
    return MainExecutionAuthority.model_validate(
        {**payload, "authority_sha256": canonical_execution_sha256(payload)}
    )


@pytest.mark.parametrize(
    ("variant", "expected_turns"),
    [
        ("A1", 1),
        ("A2", 1),
        ("A3", 1),
        ("B0", 0),
        ("B1", 1),
        ("B2", 2),
        ("CodeGraph", 2),
        ("FULL", 3),
    ],
)
def test_each_controlled_route_executes_exact_declared_path_and_replays_read_only(
    tmp_path: Path,
    variant: str,
    expected_turns: int,
) -> None:
    contract, freeze, response = load_main_runtime_inputs(ROOT)
    visible = _visible_context()
    adapter = None if variant == "B0" else _RouteAdapter()
    store = MainRuntimeStore(tmp_path / variant)
    logical = _logical(visible, variant)
    kwargs = {
        "logical": logical,
        "logical_context": _logical_context(visible),
        "family": _family(),
        "visible_context": visible,
        "contract": contract,
        "authority": _authority(contract),
        "response_contract": response,
        "fairness_freeze": freeze,
        "store": store,
        "adapter": adapter,
        "clock": _Clock(),
    }
    terminal = run_main_logical_request(**kwargs)  # type: ignore[arg-type]
    call_count = 0 if adapter is None else len(adapter.calls)
    replay = run_main_logical_request(**kwargs)  # type: ignore[arg-type]

    assert terminal.status in {"completed", "deterministic_completed"}
    assert terminal.expected_provider_turn_count == expected_turns
    assert terminal.completed_provider_turn_count == expected_turns
    assert len(terminal.turn_receipts) == expected_turns
    assert replay == terminal
    assert (0 if adapter is None else len(adapter.calls)) == call_count


def test_variant_semantic_failure_is_terminal_not_silently_rescued(tmp_path: Path) -> None:
    contract, freeze, response = load_main_runtime_inputs(ROOT)
    visible = _visible_context()
    adapter = _RouteAdapter(invalid_final_citations=True)

    terminal = run_main_logical_request(
        logical=_logical(visible, "A3"),
        logical_context=_logical_context(visible),
        family=_family(),
        visible_context=visible,
        contract=contract,
        authority=_authority(contract),
        response_contract=response,
        fairness_freeze=freeze,
        store=MainRuntimeStore(tmp_path / "store"),
        adapter=adapter,
        clock=_Clock(),
    )

    assert terminal.status == "semantic_failure"
    assert terminal.issue_code == "variant_semantic_validation_failed"
    assert terminal.final_raw_response_sha256 is not None
    assert len(adapter.calls) == 1


def test_incomplete_persisted_turn_forbids_automatic_provider_replay(tmp_path: Path) -> None:
    contract, freeze, response = load_main_runtime_inputs(ROOT)
    visible = _visible_context()
    adapter = _RouteAdapter()
    store = MainRuntimeStore(tmp_path / "store")
    logical = _logical(visible, "B2")
    kwargs = {
        "logical": logical,
        "logical_context": _logical_context(visible),
        "family": _family(),
        "visible_context": visible,
        "contract": contract,
        "authority": _authority(contract),
        "response_contract": response,
        "fairness_freeze": freeze,
        "store": store,
        "adapter": adapter,
        "clock": _Clock(),
    }
    run_main_logical_request(**kwargs)  # type: ignore[arg-type]
    logical_root = store.root / logical.request_id
    (logical_root / "terminal.json").unlink()
    (logical_root / "turn-02" / "result.json").unlink()
    calls_before = len(adapter.calls)

    with pytest.raises(MainRuntimeError, match="forbids automatic provider replay"):
        run_main_logical_request(**kwargs)  # type: ignore[arg-type]
    assert len(adapter.calls) == calls_before


@pytest.mark.skipif(
    not PRIVATE_PACKET.is_file(),
    reason="private main census packet is available only in the project workspace",
)
def test_actual_private_packet_runtime_preflight_covers_exact_matrix() -> None:
    contract, _, _ = load_main_runtime_inputs(ROOT)
    packet = DiagnosisMainPrivateCensusPacket.model_validate_json(PRIVATE_PACKET.read_bytes())
    preflight = build_main_runtime_preflight(packet, contract)

    assert preflight.status == "runtime_conformance_pass_execution_not_authorized"
    assert preflight.logical_request_count == 1024
    assert preflight.provider_backed_logical_request_count == 896
    assert preflight.deterministic_logical_request_count == 128
    assert preflight.provider_turn_count_if_all_routes_complete == 1408
    assert preflight.b3_in_controlled_matrix is False
    assert preflight.execution_authorized is False
    assert preflight.hidden_truth_or_evaluator_fields_visible is False


def test_runtime_contract_and_public_census_seals_are_hash_locked() -> None:
    contract, _, _ = load_main_runtime_inputs(ROOT)
    seal = json.loads((ROOT / "configs/evaluation/diagnosis_main_census_seal.json").read_text())
    qwen = json.loads(
        (ROOT / "configs/evaluation/diagnosis_qwen_sensitivity_census.json").read_text()
    )

    assert contract.analysis_census_sha256 == seal["analysis_census_sha256"]
    assert seal["qwen_census_sha256"] == qwen["census_sha256"]
    for payload, hash_key in (
        (seal, "seal_sha256"),
        (qwen, "census_sha256"),
    ):
        declared = payload.pop(hash_key)
        assert canonical_execution_sha256(payload) == declared
    assert content_sha256(
        (ROOT / "configs/evaluation/diagnosis_main_runtime_contract.json").read_bytes()
    )
