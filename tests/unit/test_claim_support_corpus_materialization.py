from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_adapters import normalize_variant_output
from aletheia_lab.evaluation.claim_corpus_audit import audit_claim_corpus_run
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    DiagnosisOutputV2,
    VisibleEvidenceRelation,
)
from aletheia_lab.evaluation.claim_corpus_materializer import materialize_request_claims
from aletheia_lab.evaluation.claim_corpus_readiness import (
    build_readiness_artifacts,
    verify_readiness,
)
from aletheia_lab.evaluation.claim_corpus_store import ClaimCorpusArtifactStore
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimEvidenceBinding,
    ClaimRelationAssignmentResponse,
    build_evidence_binding,
    build_relation_assignment_request,
    build_visible_evidence_item,
    parse_relation_assignment,
)
from aletheia_lab.evaluation.claim_support_instrument import classify_visible_support
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]


def _request(*, variant: str = "A1", role: str = "primary") -> ClaimCorpusRequest:
    payload = {
        "family_id": "ccf-data-drift-categorical-contract-shift-80-1",
        "family_sha256": "1" * 64,
        "mechanism": "data_drift",
        "family_role": role,
        "evidence_condition": "full",
        "variant": variant,
        "seed": 9101,
        "source_partition": "development",
        "provider_call_authorized": False,
    }
    return ClaimCorpusRequest.model_validate(
        {**payload, "request_sha256": canonical_execution_sha256(payload)}
    )


def _output(source_record_sha256: str = "2" * 64) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "diagnosis-output/2",
        "output_status": "completed",
        "atomic_claims": (
            {
                "claim_local_id": "claim-1",
                "claim_type": "cause_assertion",
                "claim_text": "The bounded drift is consistent with the visible metric change.",
                "material_parts": ({"part_id": "part-drift", "text": "A bounded drift occurred."},),
                "visible_evidence_ids": ("evidence-visible",),
            },
        ),
        "abstention_reason": None,
        "parse_failure_code": None,
        "source_record_sha256": source_record_sha256,
    }
    payload["output_sha256"] = canonical_execution_sha256(payload)
    return payload


def _evidence(
    polarity: str = "supports", scope: str = "entire"
) -> tuple[VisibleEvidenceRelation, ...]:
    return (
        VisibleEvidenceRelation.model_validate(
            {
                "evidence_id": "evidence-visible",
                "text": "The visible metric changed after the registered intervention.",
                "relation_polarity": polarity,
                "relation_scope": scope,
            }
        ),
    )


def _binding(request: ClaimCorpusRequest | None = None) -> ClaimEvidenceBinding:
    selected_request = request or _request()
    item = build_visible_evidence_item(
        evidence_id="evidence-visible",
        kind="metric",
        title="Observed metric comparison",
        content="The visible metric changed after the registered operation.",
        source_content_sha256="5" * 64,
    )
    return build_evidence_binding(
        selected_request,
        items=(item,),
        source_projection_sha256="6" * 64,
    )


def _assignment(
    request: ClaimCorpusRequest,
    output: dict[str, object],
    binding: ClaimEvidenceBinding,
    *,
    polarity: str = "supports",
    scope: str = "entire",
) -> ClaimRelationAssignmentResponse:
    claim = output["atomic_claims"][0]  # type: ignore[index]
    relation_request = build_relation_assignment_request(
        source_output_sha256=output["output_sha256"],  # type: ignore[arg-type]
        claim_local_id=claim["claim_local_id"],  # type: ignore[index]
        claim_text=claim["claim_text"],  # type: ignore[index]
        claim_type=claim["claim_type"],  # type: ignore[index]
        cited_evidence_ids=claim["visible_evidence_ids"],  # type: ignore[index]
        evidence_binding=binding,
    )
    return parse_relation_assignment(
        relation_request,
        {
            "decisions": (
                {
                    "evidence_id": "evidence-visible",
                    "relation_polarity": polarity,
                    "relation_scope": scope,
                },
            )
        },
    )


def test_tracked_zero_outcome_readiness_chain_is_complete() -> None:
    receipt = verify_readiness(ROOT)

    assert receipt.materialization_ready
    assert receipt.primary_family_count == 15
    assert receipt.reserve_family_count == 6
    assert receipt.primary_request_count == 360
    assert receipt.reserve_request_count == 144
    assert receipt.adapter_count == 8
    assert not receipt.provider_calls_executed
    assert not receipt.outputs_generated
    assert not receipt.development_claim_pool_materialized
    assert not receipt.automatic_labels_generated
    assert not receipt.human_annotations_collected
    assert not receipt.main_or_sealed_outcomes_opened


def test_generated_readiness_artifacts_are_byte_identical() -> None:
    expected = build_readiness_artifacts(ROOT)

    assert expected
    for relative, payload in expected.items():
        assert (ROOT / relative).read_bytes() == payload


def test_read_only_auditor_has_no_writer_or_provider_dependency() -> None:
    audit_path = ROOT / "src/aletheia_lab/evaluation/claim_corpus_audit.py"
    materializer_path = ROOT / "src/aletheia_lab/evaluation/claim_corpus_materializer.py"
    imported = {
        node.module
        for path in (audit_path, materializer_path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    }

    assert "aletheia_lab.evaluation.claim_corpus_store" not in imported
    assert not any(
        module is not None and module.startswith("aletheia_lab.model_gateway")
        for module in imported
    )


def test_request_census_is_exact_and_never_authorizes_execution() -> None:
    payload = json.loads(
        (ROOT / "configs/evaluation/claim_support_request_census.json").read_text()
    )

    assert len(payload["primary_requests"]) == 360
    assert len(payload["reserve_requests"]) == 144
    assert {item["variant"] for item in payload["primary_requests"]} == {
        "A1",
        "A2",
        "A3",
        "B0",
        "B1",
        "B2",
        "CodeGraph",
        "FULL",
    }
    assert all(not item["provider_call_authorized"] for item in payload["primary_requests"])
    assert all(not item["provider_call_authorized"] for item in payload["reserve_requests"])


@pytest.mark.parametrize(
    ("relations", "expected"),
    (
        (("contradicts", "partial"), "contradicted"),
        (("neutral", "none"), "unsupported"),
        (("supports", "partial"), "partially_supported"),
        (("supports", "entire"), "fully_supported"),
    ),
)
def test_instrument_preserves_registered_precedence(
    relations: tuple[str, str], expected: str
) -> None:
    assert (
        classify_visible_support(
            claim_text="A bounded claim.",
            claim_type="cause_assertion",
            visible_evidence=_evidence(*relations),  # type: ignore[arg-type]
        )
        == expected
    )


def test_contradiction_has_precedence_over_complete_support() -> None:
    evidence = (
        *_evidence("supports", "entire"),
        VisibleEvidenceRelation(
            evidence_id="evidence-conflict",
            text="A visible record conflicts with the claim.",
            relation_polarity="contradicts",
            relation_scope="partial",
        ),
    )

    assert (
        classify_visible_support(
            claim_text="A bounded claim.",
            claim_type="cause_assertion",
            visible_evidence=evidence,
        )
        == "contradicted"
    )


def test_adapter_rejects_b3_and_free_text_fallback() -> None:
    with pytest.raises(ClaimCorpusContractError, match="not eligible"):
        normalize_variant_output("B3", _output())
    with pytest.raises(ClaimCorpusContractError, match="diagnosis-output/2"):
        normalize_variant_output("A1", {"free_text": "Split me after the fact."})


def test_materializer_is_deterministic_and_visibility_bounded() -> None:
    request = _request()
    output = _output()
    binding = _binding(request)
    assignments = {"claim-1": _assignment(request, output, binding)}
    first = materialize_request_claims(request, output, binding, assignments)
    second = materialize_request_claims(request, output, binding, assignments)

    assert first == second
    assert len(first) == 1
    assert first[0].automatic_label == "fully_supported"
    assert not first[0].hidden_ground_truth_present
    assert not first[0].human_judgment_present
    assert not first[0].main_outcome_present


def test_materializer_rejects_missing_evidence_and_unactivated_reserve() -> None:
    request = _request()
    output = _output()
    binding = _binding(request)
    with pytest.raises(ClaimCorpusContractError, match="exactly cover"):
        materialize_request_claims(request, output, binding, {})
    reserve = _request(role="reserve")
    with pytest.raises(ClaimCorpusContractError, match="reserve family"):
        materialize_request_claims(
            reserve,
            output,
            _binding(reserve),
            {},
        )


def test_store_replay_and_independent_audit_are_idempotent(tmp_path: Path) -> None:
    request = _request()
    output = _output()
    binding = _binding(request)
    entry = materialize_request_claims(
        request,
        output,
        binding,
        {"claim-1": _assignment(request, output, binding)},
    )[0]
    store = ClaimCorpusArtifactStore(tmp_path)
    kwargs = {
        "protocol_sha256": "3" * 64,
        "census_sha256": "4" * 64,
        "entries": (entry,),
        "provider_calls_recorded": 1,
    }

    first = store.publish(**kwargs)  # type: ignore[arg-type]
    second = store.publish(**kwargs)  # type: ignore[arg-type]
    audit = audit_claim_corpus_run(tmp_path, first.run_id)

    assert first == second
    assert audit.ready
    assert audit.entry_count == 1
    assert not audit.writer_state_trusted


def test_independent_audit_detects_missing_and_untracked_objects(tmp_path: Path) -> None:
    request = _request()
    output = _output()
    binding = _binding(request)
    entry = materialize_request_claims(
        request,
        output,
        binding,
        {"claim-1": _assignment(request, output, binding)},
    )[0]
    store = ClaimCorpusArtifactStore(tmp_path)
    receipt = store.publish(
        protocol_sha256="3" * 64,
        census_sha256="4" * 64,
        entries=(entry,),
        provider_calls_recorded=1,
    )
    run_root = tmp_path / "runs" / receipt.run_id
    object_path = next((run_root / "objects").rglob("*.json"))
    object_path.unlink()
    (run_root / "writer-success.txt").write_text("untrusted", encoding="utf-8")

    audit = audit_claim_corpus_run(tmp_path, receipt.run_id)

    assert not audit.ready
    assert {item.code for item in audit.findings} == {"missing_object", "unexpected_file"}


def test_output_terminal_shapes_cannot_mix_claims_with_abstention() -> None:
    payload = _output()
    payload["output_status"] = "abstained"
    payload["abstention_reason"] = "Evidence is insufficient."
    payload["output_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "output_sha256"}
    )

    with pytest.raises(ValueError):
        DiagnosisOutputV2.model_validate(payload)
