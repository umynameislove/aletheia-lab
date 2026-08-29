"""Tests for neutral execution contracts."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.execution_contracts import (
    EXECUTION_CANONICAL_SCHEMA_VERSION,
    AttemptIdentity,
    EvaluationCaseReference,
    EvaluationContractError,
    EvaluationManifestReference,
    ModelPolicyReference,
    TechnicalIssue,
    canonical_execution_json,
    canonical_execution_sha256,
    validate_unique_case_references,
)


def _sha256(character: str) -> str:
    return character * 64


def _opaque(character: str) -> str:
    return f"ev-{_sha256(character)}"


def _project(character: str = "1") -> str:
    return f"p3-project-{_sha256(character)}"


def _snapshot(character: str = "2") -> str:
    return f"p3-snapshot-{_sha256(character)}"


def _evidence_bundle(character: str = "3") -> str:
    return f"p3-evidence-bundle-{_sha256(character)}"


def _lineage_graph(character: str = "4") -> str:
    return f"p3-lineage-graph-{_sha256(character)}"


def _oracle_execution_json(payload: object) -> str:
    return json.dumps(
        {
            "schema_version": EXECUTION_CANONICAL_SCHEMA_VERSION,
            "payload": payload,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _manifest(
    *,
    project_id: str | None = None,
    snapshot_id: str | None = None,
    manifest_content_sha256: str | None = None,
) -> EvaluationManifestReference:
    return EvaluationManifestReference.build(
        project_id=project_id or _project(),
        snapshot_id=snapshot_id or _snapshot(),
        manifest_content_sha256=manifest_content_sha256 or _sha256("a"),
        source_commit_ref="b" * 40,
        authorization_state="authorized",
        authorization_ref=_opaque("c"),
        provenance_sha256=_sha256("d"),
        created_at="2026-08-29T00:00:00Z",
        frozen_at="2026-08-29T00:00:01Z",
        visibility="diagnosis",
    )


def _case(manifest: EvaluationManifestReference) -> EvaluationCaseReference:
    return EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque("e"),
        family_id=_opaque("f"),
        mechanism_id=_opaque("a"),
        dataset_id=_opaque("0"),
        variant_id=_opaque("b"),
        variant_content_sha256=_sha256("c"),
        case_content_sha256=_sha256("1"),
        evidence_bundle_id=_evidence_bundle(),
        evidence_content_sha256=_sha256("2"),
        lineage_graph_id=_lineage_graph(),
        lineage_sha256=_sha256("3"),
        visibility_projection_sha256=_sha256("4"),
        provenance_sha256=_sha256("5"),
        visibility="diagnosis",
    )


def _policy(manifest: EvaluationManifestReference) -> ModelPolicyReference:
    return ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=_sha256("6"),
        provider_ref=_opaque("7"),
        model_ref=_opaque("8"),
        model_version_ref=_opaque("9"),
        resource_policy_ref=_opaque("a"),
        prompt_policy_ref=_opaque("b"),
        response_schema_sha256=_sha256("c"),
        provenance_sha256=_sha256("d"),
        visibility="diagnosis",
    )


def _attempt(
    manifest: EvaluationManifestReference,
    case: EvaluationCaseReference,
    policy: ModelPolicyReference,
) -> AttemptIdentity:
    return AttemptIdentity.build(
        manifest=manifest,
        case=case,
        model_policy=policy,
        context_sha256=_sha256("e"),
        prompt_sha256=_sha256("f"),
        response_schema_sha256=policy.response_schema_sha256,
        attempt_ordinal=1,
    )


def test_canonical_round_trip_matches_independent_oracle() -> None:
    payload = {"z": [3, 2, 1], "a": {"beta": "two", "alpha": "one"}}
    expected = _oracle_execution_json(payload)

    assert canonical_execution_json(payload) == expected
    assert canonical_execution_sha256(payload) == hashlib.sha256(
        expected.encode("utf-8")
    ).hexdigest()

    manifest = _manifest()
    attempt = _attempt(manifest, _case(manifest), _policy(manifest))
    assert AttemptIdentity.model_validate_json(attempt.model_dump_json()) == attempt


def test_insertion_order_does_not_change_canonical_identity() -> None:
    forward = {
        "alpha": {"first": 1, "second": 2},
        "beta": ["x", "y"],
    }
    reversed_order = {
        "beta": ["x", "y"],
        "alpha": {"second": 2, "first": 1},
    }

    assert canonical_execution_json(forward) == canonical_execution_json(reversed_order)
    assert canonical_execution_sha256(forward) == canonical_execution_sha256(reversed_order)


def test_mutating_bound_content_changes_case_and_attempt_identity() -> None:
    manifest = _manifest()
    original_case = _case(manifest)
    changed_case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=original_case.case_id,
        family_id=original_case.family_id,
        mechanism_id=original_case.mechanism_id,
        dataset_id=original_case.dataset_id,
        variant_id=original_case.variant_id,
        variant_content_sha256=_sha256("6"),
        case_content_sha256=original_case.case_content_sha256,
        evidence_bundle_id=original_case.evidence_bundle_id,
        evidence_content_sha256=original_case.evidence_content_sha256,
        lineage_graph_id=original_case.lineage_graph_id,
        lineage_sha256=original_case.lineage_sha256,
        visibility_projection_sha256=original_case.visibility_projection_sha256,
        provenance_sha256=original_case.provenance_sha256,
        visibility=original_case.visibility,
    )
    policy = _policy(manifest)

    assert changed_case.reference_id != original_case.reference_id
    assert _attempt(manifest, changed_case, policy).request_identity_sha256 != _attempt(
        manifest, original_case, policy
    ).request_identity_sha256


def test_forged_digests_are_rejected() -> None:
    manifest = _manifest()
    attempt = _attempt(manifest, _case(manifest), _policy(manifest))
    forged = attempt.model_dump(mode="python")
    forged["request_identity_sha256"] = _sha256("0")

    with pytest.raises(ValidationError):
        AttemptIdentity.model_validate(forged)

    forged_manifest = manifest.model_dump(mode="python")
    forged_manifest["reference_id"] = _opaque("0")
    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(forged_manifest)


def test_attempt_rejects_a_variant_not_bound_to_its_case() -> None:
    manifest = _manifest()
    attempt = _attempt(manifest, _case(manifest), _policy(manifest))
    forged = attempt.model_dump(mode="python")
    forged["variant_id"] = _opaque("0")

    with pytest.raises(ValidationError):
        AttemptIdentity.model_validate(forged)


def test_unknown_schema_and_unknown_field_are_rejected() -> None:
    manifest = _manifest()

    unknown_schema = manifest.model_dump(mode="python")
    unknown_schema["schema_version"] = "evaluation-manifest-reference/v2"
    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(unknown_schema)

    unknown_field = manifest.model_dump(mode="python")
    unknown_field["unexpected"] = "value"
    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(unknown_field)


def test_cross_project_and_cross_snapshot_attempt_references_are_rejected() -> None:
    manifest = _manifest()
    policy = _policy(manifest)

    foreign_project_manifest = _manifest(
        project_id=_project("6"),
        manifest_content_sha256=_sha256("7"),
    )
    with pytest.raises(ValidationError):
        _attempt(manifest, _case(foreign_project_manifest), policy)

    foreign_snapshot_manifest = _manifest(
        snapshot_id=_snapshot("8"),
        manifest_content_sha256=_sha256("9"),
    )
    with pytest.raises(ValidationError):
        _attempt(manifest, _case(foreign_snapshot_manifest), policy)


def test_missing_or_false_authorization_is_rejected() -> None:
    manifest = _manifest()

    missing = manifest.model_dump(mode="python")
    del missing["authorization_ref"]
    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(missing)

    false_value = manifest.model_dump(mode="python")
    false_value["authorization_state"] = False
    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(false_value)


def test_noncanonical_source_commit_reference_is_rejected() -> None:
    manifest = _manifest()
    malformed = manifest.model_dump(mode="python")
    malformed["source_commit_ref"] = "B" * 40

    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(malformed)


def test_contracts_do_not_supply_scientific_defaults() -> None:
    forbidden_fields = {
        "threshold",
        "denominator",
        "endpoint",
        "mechanism_status",
        "temperature",
        "token_budget",
        "seed",
        "prompt_text",
        "primary_analysis",
    }
    model_fields = set(EvaluationManifestReference.model_fields)
    model_fields |= set(EvaluationCaseReference.model_fields)
    model_fields |= set(ModelPolicyReference.model_fields)
    model_fields |= set(AttemptIdentity.model_fields)

    assert not forbidden_fields & model_fields
    for field_name in (
        "provider_ref",
        "model_ref",
        "model_version_ref",
        "resource_policy_ref",
        "prompt_policy_ref",
    ):
        assert ModelPolicyReference.model_fields[field_name].is_required()


@pytest.mark.parametrize(
    "unsafe_value",
    (
        r"C:\secrets\credential.txt",
        "/var/tmp/credential.txt",
        r"\\server\share\credential.txt",
        "api_key=not-permitted",
    ),
)
def test_public_contracts_reject_paths_and_raw_secrets(unsafe_value: str) -> None:
    manifest = _manifest()
    payload = manifest.model_dump(mode="python")
    payload["authorization_ref"] = unsafe_value

    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(payload)

    serialized = manifest.model_dump_json()
    assert "C:\\" not in serialized
    assert "/var/" not in serialized
    assert "api_key=" not in serialized


def test_duplicate_case_variant_identity_is_rejected() -> None:
    manifest = _manifest()
    case = _case(manifest)

    with pytest.raises(EvaluationContractError):
        validate_unique_case_references((case, case))

    other_variant = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=case.case_id,
        family_id=case.family_id,
        mechanism_id=case.mechanism_id,
        dataset_id=case.dataset_id,
        variant_id=_opaque("6"),
        variant_content_sha256=_sha256("6"),
        case_content_sha256=case.case_content_sha256,
        evidence_bundle_id=case.evidence_bundle_id,
        evidence_content_sha256=case.evidence_content_sha256,
        lineage_graph_id=case.lineage_graph_id,
        lineage_sha256=case.lineage_sha256,
        visibility_projection_sha256=case.visibility_projection_sha256,
        provenance_sha256=case.provenance_sha256,
        visibility=case.visibility,
    )
    validate_unique_case_references((case, other_variant))


def test_naive_timestamps_and_noncanonical_numbers_are_rejected() -> None:
    manifest = _manifest()
    payload = manifest.model_dump(mode="python")
    payload["created_at"] = "2026-08-29T00:00:00"

    with pytest.raises(ValidationError):
        EvaluationManifestReference.model_validate(payload)
    with pytest.raises(ValueError):
        canonical_execution_json({"number": float("nan")})


def test_technical_issue_hashes_but_never_serializes_raw_message() -> None:
    issue = TechnicalIssue.build(
        code="authorization_missing",
        stage="manifest_validation",
        severity="blocker",
        subject_reference_id=_opaque("d"),
        message="password=not-permitted",
        authorization_ref=_opaque("c"),
        provenance_sha256=_sha256("e"),
        visibility="public",
    )

    serialized = issue.model_dump_json()
    assert "password=not-permitted" not in serialized
    assert issue.issue_id == f"ev-{issue.issue_sha256}"
    assert issue.subject_sha256 == _sha256("d")
    assert issue.public_message == "blocker: authorization_missing at manifest_validation"

    unsafe_message = issue.model_dump(mode="python")
    unsafe_message["public_message"] = r"C:\secrets\credential.txt"
    with pytest.raises(ValidationError):
        TechnicalIssue.model_validate(unsafe_message)


def test_platform_independent_contract_serialization_has_no_machine_path_input() -> None:
    manifest = _manifest()
    attempt = _attempt(manifest, _case(manifest), _policy(manifest))

    first = canonical_execution_json(attempt.model_dump(mode="json"))
    second = canonical_execution_json(
        json.loads(json.dumps(attempt.model_dump(mode="json"), sort_keys=False))
    )

    assert first == second
