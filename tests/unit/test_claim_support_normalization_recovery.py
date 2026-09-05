from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ELIGIBLE_VARIANTS,
    ClaimCorpusContractError,
    ClaimCorpusRequestCensus,
)
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    PROVIDER_OUTPUT_SCHEMA_VERSION_V2,
    RECOVERY_PROTOCOL_PATH,
    ClaimCorpusNormalizationRecoveryError,
    build_recovery_protocol,
    load_recovery_protocol,
    normalize_provider_output_v2,
    provider_response_schema_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import (
    GatewayContractError,
    validate_response_payload,
)
from aletheia_lab.project.identity import canonical_project_json

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = ROOT / "configs/evaluation/claim_support_observed_evidence_census.json"


def _inputs() -> tuple[ClaimCorpusRequestCensus, ObservedEvidenceCensus]:
    return (
        ClaimCorpusRequestCensus.model_validate_json((ROOT / REQUEST_CENSUS_PATH).read_bytes()),
        ObservedEvidenceCensus.model_validate_json(EVIDENCE_PATH.read_bytes()),
    )


def _request_and_evidence(variant: str) -> tuple[object, tuple[str, ...]]:
    census, evidence = _inputs()
    request = next(item for item in census.primary_requests if item.variant == variant)
    binding = next(
        item
        for item in evidence.bindings
        if item.family_id == request.family_id
        and item.evidence_condition == request.evidence_condition
    )
    return request, tuple(item.evidence_id for item in binding.visible_context.items)


def _completed_payload(evidence_id: str, *, claim_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": PROVIDER_OUTPUT_SCHEMA_VERSION_V2,
        "result": {
            "output_status": "completed",
            "atomic_claims": [
                {
                    "claim_type": "evidence_statement",
                    "claim_text": f"Measured evidence supports observation {index}.",
                    "material_parts": [{"text": f"Measured evidence supports part {index}."}],
                    "visible_evidence_ids": [evidence_id],
                }
                for index in range(1, claim_count + 1)
            ],
        },
    }


def test_registered_recovery_is_reproducible_and_outcome_blind() -> None:
    registered = load_recovery_protocol(ROOT)
    rebuilt = build_recovery_protocol(ROOT)

    assert registered == rebuilt
    assert (ROOT / RECOVERY_PROTOCOL_PATH).is_file()
    assert registered.predecessor_attempt_retired is True
    assert registered.predecessor_outputs_reused is False
    assert registered.predecessor_claim_candidate_count == 152
    assert registered.target_claim_count == 200
    assert registered.new_authorization_required is True
    assert registered.registered_recovery_attempts == 1
    assert registered.provider_calls_executed is False
    assert registered.claims_materialized is False
    assert registered.automatic_labels_generated is False
    assert registered.blind_packets_generated is False
    assert registered.human_annotations_collected is False
    assert registered.main_or_sealed_outcomes_opened is False


def test_recomputed_registration_tamper_is_rejected_against_frozen_inputs(
    tmp_path: Path,
) -> None:
    relative_inputs = (
        "configs/evaluation/claim_support_request_census.json",
        "configs/evaluation/diagnosis_variant_fairness_freeze.json",
        "configs/evaluation/claim_support_observed_evidence_census.json",
        "configs/evaluation/claim_support_corpus_protocol.json",
        RECOVERY_PROTOCOL_PATH,
    )
    for relative in relative_inputs:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    path = tmp_path / RECOVERY_PROTOCOL_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["response_schema_set_sha256"] = "f" * 64
    identity = dict(payload)
    identity.pop("protocol_sha256")
    payload["protocol_sha256"] = canonical_execution_sha256(identity)
    path.write_text(canonical_project_json(payload), encoding="utf-8")

    with pytest.raises(
        ClaimCorpusNormalizationRecoveryError,
        match="differs from frozen inputs",
    ):
        load_recovery_protocol(tmp_path)


@pytest.mark.parametrize("variant", ELIGIBLE_VARIANTS)
def test_v2_payload_normalizes_for_every_frozen_variant(variant: str) -> None:
    request, evidence_ids = _request_and_evidence(variant)
    payload = _completed_payload(evidence_ids[0], claim_count=5)

    validate_response_payload(payload, provider_response_schema_v2(evidence_ids))
    output = normalize_provider_output_v2(
        request,
        payload,
        source_record_sha256="a" * 64,
        visible_evidence_ids=evidence_ids,
    )

    assert output.output_status == "completed"
    assert tuple(item.claim_local_id for item in output.atomic_claims) == (
        "claim-1",
        "claim-2",
        "claim-3",
        "claim-4",
        "claim-5",
    )
    assert all(
        tuple(part.part_id for part in claim.material_parts) == ("part-1",)
        for claim in output.atomic_claims
    )


def test_v2_abstention_has_an_unambiguous_terminal_shape() -> None:
    request, evidence_ids = _request_and_evidence("A1")
    payload = {
        "schema_version": PROVIDER_OUTPUT_SCHEMA_VERSION_V2,
        "result": {
            "output_status": "abstained",
            "abstention_reason": "Visible evidence is insufficient for an atomic claim.",
        },
    }

    output = normalize_provider_output_v2(
        request,
        payload,
        source_record_sha256="b" * 64,
        visible_evidence_ids=evidence_ids,
    )

    assert output.output_status == "abstained"
    assert output.atomic_claims == ()
    assert output.abstention_reason == payload["result"]["abstention_reason"]


@pytest.mark.parametrize(
    "mutation",
    (
        "provider_authored_claim_id",
        "provider_authored_part_id",
        "too_many_claims",
        "missing_material_parts",
        "unknown_evidence_id",
        "blank_claim_text",
        "ambiguous_terminal_shape",
    ),
)
def test_v2_boundary_rejects_every_known_normalization_failure_class(
    mutation: str,
) -> None:
    request, evidence_ids = _request_and_evidence("FULL")
    payload = _completed_payload(evidence_ids[0])
    result = payload["result"]
    assert isinstance(result, dict)
    claims = result["atomic_claims"]
    assert isinstance(claims, list)
    claim = claims[0]
    assert isinstance(claim, dict)

    if mutation == "provider_authored_claim_id":
        claim["claim_local_id"] = "c1"
    elif mutation == "provider_authored_part_id":
        parts = claim["material_parts"]
        assert isinstance(parts, list) and isinstance(parts[0], dict)
        parts[0]["part_id"] = "p1"
    elif mutation == "too_many_claims":
        result["atomic_claims"] = _completed_payload(evidence_ids[0], claim_count=6)["result"][
            "atomic_claims"
        ]
    elif mutation == "missing_material_parts":
        claim["material_parts"] = []
    elif mutation == "unknown_evidence_id":
        claim["visible_evidence_ids"] = ["not-visible"]
    elif mutation == "blank_claim_text":
        claim["claim_text"] = "   "
    else:
        result["abstention_reason"] = "Conflicting terminal fields."

    schema = provider_response_schema_v2(evidence_ids)
    with pytest.raises(GatewayContractError):
        validate_response_payload(payload, schema)
    with pytest.raises(ClaimCorpusContractError, match="recovery schema"):
        normalize_provider_output_v2(
            request,
            payload,
            source_record_sha256="c" * 64,
            visible_evidence_ids=evidence_ids,
        )


def test_duplicate_citations_have_one_prespecified_canonical_form() -> None:
    request, evidence_ids = _request_and_evidence("B2")
    payload = _completed_payload(evidence_ids[0])
    duplicate = copy.deepcopy(payload)
    result = duplicate["result"]
    assert isinstance(result, dict)
    claims = result["atomic_claims"]
    assert isinstance(claims, list) and isinstance(claims[0], dict)
    claims[0]["visible_evidence_ids"] = [evidence_ids[0], evidence_ids[0]]

    output = normalize_provider_output_v2(
        request,
        duplicate,
        source_record_sha256="d" * 64,
        visible_evidence_ids=evidence_ids,
    )

    assert output.atomic_claims[0].visible_evidence_ids == (evidence_ids[0],)


def test_response_schema_is_context_bound_and_deterministic() -> None:
    _, evidence_ids = _request_and_evidence("CodeGraph")

    first = provider_response_schema_v2(evidence_ids)
    second = provider_response_schema_v2(evidence_ids)

    assert first == second
    result_schema = first["properties"]["result"]
    assert isinstance(result_schema, dict)
    completed = result_schema["anyOf"][0]
    citation_enum = completed["properties"]["atomic_claims"]["items"]["properties"][  # type: ignore[index]
        "visible_evidence_ids"
    ]["items"]["enum"]
    assert citation_enum == list(evidence_ids)


@pytest.mark.parametrize("field", ("claim_text", "part_text", "abstention_reason"))
@pytest.mark.parametrize(
    "text",
    (
        " Claim.",
        "Claim. ",
        "Claim\ntext.",
        "Claim.\n",
        "Claim\ttext.",
        "Claim\x00text.",
        "Claim\x7ftext.",
        "\u00a0Claim.",
        "Claim.\u2003",
        "",
        " ",
        "\ud800",
    ),
)
def test_text_policy_rejects_at_gateway_and_normalizer(field: str, text: str) -> None:
    request, evidence_ids = _request_and_evidence("FULL")
    payload = _text_payload(evidence_ids[0], field, text)
    with pytest.raises(GatewayContractError):
        validate_response_payload(payload, provider_response_schema_v2(evidence_ids))
    with pytest.raises(ClaimCorpusContractError):
        normalize_provider_output_v2(
            request,
            payload,
            source_record_sha256="a" * 64,
            visible_evidence_ids=evidence_ids,
        )


def _text_payload(evidence_id: str, field: str, text: str) -> dict[str, object]:
    if field == "abstention_reason":
        return {
            "schema_version": PROVIDER_OUTPUT_SCHEMA_VERSION_V2,
            "result": {"output_status": "abstained", "abstention_reason": text},
        }
    payload = _completed_payload(evidence_id)
    result = payload["result"]
    assert isinstance(result, dict)
    claim = result["atomic_claims"][0]
    if field == "claim_text":
        claim["claim_text"] = text
    else:
        claim["material_parts"][0]["text"] = text
    return payload


@pytest.mark.parametrize(
    "field,limit", (("claim_text", 2048), ("part_text", 1024), ("abstention_reason", 2048))
)
@pytest.mark.parametrize("case", ("single", "unicode", "maximum", "overflow"))
def test_text_boundaries_agree(field: str, limit: int, case: str) -> None:
    request, evidence_ids = _request_and_evidence("FULL")
    text = {
        "single": "x",
        "unicode": "Bằng chứng hợp lệ.",
        "maximum": "x" * limit,
        "overflow": "x" * (limit + 1),
    }[case]
    payload = _text_payload(evidence_ids[0], field, text)
    if case == "overflow":
        with pytest.raises(GatewayContractError):
            validate_response_payload(payload, provider_response_schema_v2(evidence_ids))
        with pytest.raises(ClaimCorpusContractError):
            normalize_provider_output_v2(
                request,
                payload,
                source_record_sha256="a" * 64,
                visible_evidence_ids=evidence_ids,
            )
    else:
        validate_response_payload(payload, provider_response_schema_v2(evidence_ids))
        output = normalize_provider_output_v2(
            request,
            payload,
            source_record_sha256="a" * 64,
            visible_evidence_ids=evidence_ids,
        )
        assert output.output_status == (
            "abstained" if field == "abstention_reason" else "completed"
        )
