"""Build and independently verify zero-outcome claim-corpus readiness artifacts."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Final, cast

from pydantic import BaseModel, ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.evaluation.claim_corpus_adapters import adapter_inventory
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ELIGIBLE_VARIANTS,
    EVIDENCE_CONDITIONS,
    MECHANISMS,
    ClaimCorpusContractError,
    ClaimCorpusFamily,
    ClaimCorpusFamilyInventory,
    ClaimCorpusProtocolAmendment,
    ClaimCorpusReadinessPlan,
    ClaimCorpusReadinessReceipt,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
    SourceArtifactBinding,
    VisibleEvidenceRelation,
)
from aletheia_lab.evaluation.claim_support_instrument import (
    INSTRUMENT_VERSION,
    classify_visible_support,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json, content_sha256

PARENT_PROTOCOL_PATH: Final = "configs/evaluation/claim_support_corpus_protocol.json"
FAMILY_INVENTORY_PATH: Final = "configs/evaluation/claim_support_family_inventory.json"
AMENDMENT_PATH: Final = "configs/evaluation/claim_support_corpus_amendment.json"
REQUEST_CENSUS_PATH: Final = "configs/evaluation/claim_support_request_census.json"
DIAGNOSIS_SCHEMA_PATH: Final = "configs/evaluation/diagnosis_output_v2_schema.json"
ADAPTER_MANIFEST_PATH: Final = "configs/evaluation/claim_support_adapter_manifest.json"
SEMANTIC_FIXTURES_PATH: Final = "configs/evaluation/claim_support_instrument_fixtures.json"
INSTRUMENT_MANIFEST_PATH: Final = (
    "configs/evaluation/claim_support_automatic_instrument_manifest.json"
)
IMPLEMENTATION_MANIFEST_PATH: Final = (
    "configs/evaluation/claim_support_materialization_implementation.json"
)
READINESS_PLAN_PATH: Final = "configs/evaluation/claim_support_materialization_plan.json"
READINESS_RECEIPT_PATH: Final = (
    "configs/evaluation/claim_support_materialization_readiness.json"
)
FAIRNESS_PATH: Final = "configs/evaluation/diagnosis_variant_fairness_freeze.json"

_SOURCE_PATHS: Final[dict[str, str]] = {
    "data_drift": "configs/benchmark/fault_types.yaml",
    "preprocessing_mismatch": "configs/benchmark/p2r_preprocessing_confirmatory_protocol.json",
    "label_noise": "configs/benchmark/p2_label_noise_shift_v3_design.json",
}

_FAMILY_SPECS: Final[dict[str, tuple[tuple[str, dict[str, object]], ...]]] = {
    "data_drift": (
        ("categorical-contract-shift-80", {"feature": "Contract", "month_to_month": 0.80}),
        ("categorical-contract-shift-70", {"feature": "Contract", "month_to_month": 0.70}),
        ("categorical-contract-shift-90", {"feature": "Contract", "month_to_month": 0.90}),
        ("categorical-contract-balance-40", {"feature": "Contract", "month_to_month": 0.40}),
        ("categorical-contract-shift-60", {"feature": "Contract", "month_to_month": 0.60}),
        ("categorical-payment-shift", {"feature": "PaymentMethod", "dominant_share": 0.72}),
        ("categorical-internet-shift", {"feature": "InternetService", "dominant_share": 0.74}),
    ),
    "preprocessing_mismatch": (
        ("scaler-centering-omitted", {"operation": "standard_scaler", "change": "with_mean_false"}),
        ("scaler-variance-omitted", {"operation": "standard_scaler", "change": "with_std_false"}),
        ("encoder-category-order", {"operation": "one_hot", "change": "category_order_permuted"}),
        ("imputer-statistic-mismatch", {"operation": "imputation", "change": "median_to_mean"}),
        ("feature-column-order", {"operation": "column_transform", "change": "numeric_order_swapped"}),
        ("normalization-divisor", {"operation": "normalization", "change": "divisor_mismatch"}),
        ("missing-indicator-omitted", {"operation": "imputation", "change": "indicator_removed"}),
    ),
    "label_noise": (
        ("symmetric-flip-05", {"noise": "symmetric", "rate": 0.05}),
        ("symmetric-flip-10", {"noise": "symmetric", "rate": 0.10}),
        ("symmetric-flip-20", {"noise": "symmetric", "rate": 0.20}),
        ("positive-to-negative-15", {"noise": "class_conditional", "from": 1, "to": 0, "rate": 0.15}),
        ("boundary-targeted-20", {"noise": "boundary_targeted", "rate": 0.20}),
        ("negative-to-positive-15", {"noise": "class_conditional", "from": 0, "to": 1, "rate": 0.15}),
        ("cluster-targeted-10", {"noise": "cluster_targeted", "rate": 0.10}),
    ),
}


def _load_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClaimCorpusContractError(f"invalid readiness artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise ClaimCorpusContractError(f"readiness artifact is not an object: {path.name}")
    return value


def _self_hash(payload: Mapping[str, object], field: str) -> str:
    return canonical_sha256({key: value for key, value in payload.items() if key != field})


def _require_self_hash(payload: Mapping[str, object], field: str) -> str:
    declared = payload.get(field)
    expected = _self_hash(payload, field)
    if declared != expected:
        raise ClaimCorpusContractError(f"{field} does not match canonical content")
    return expected


def _resolve_regular(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ClaimCorpusContractError(f"required artifact is unavailable: {relative}") from exc
    if candidate.is_symlink() or not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ClaimCorpusContractError(f"required artifact is not repository-local: {relative}")
    return resolved


def _model_payload(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json")
    return (canonical_project_json(payload) + "\n").encode("utf-8")


def build_family_inventory(root: Path, parent_protocol_sha256: str) -> ClaimCorpusFamilyInventory:
    families: list[ClaimCorpusFamily] = []
    for mechanism_index, mechanism in enumerate(MECHANISMS, start=1):
        source_path = _resolve_regular(root, _SOURCE_PATHS[mechanism])
        source = SourceArtifactBinding(
            path=_SOURCE_PATHS[mechanism],
            content_sha256=content_sha256(source_path.read_bytes()),
        )
        for order, (slug, parameters) in enumerate(_FAMILY_SPECS[mechanism], start=1):
            family_payload = {
                "family_id": f"ccf-{mechanism.replace('_', '-')}-{slug}-{order}",
                "mechanism": mechanism,
                "role": "primary" if order <= 5 else "reserve",
                "registered_order": order,
                "seed": 9000 + mechanism_index * 100 + order,
                "intervention_kind": slug,
                "intervention_parameters": parameters,
                "evidence_conditions": EVIDENCE_CONDITIONS,
                "invariants": (
                    "development partition only",
                    "one intervention family changes while model and evaluation budget remain fixed",
                    "mechanism disposition is preserved and causal admission is not inferred",
                ),
                "source_artifact": source.model_dump(mode="json"),
            }
            families.append(
                ClaimCorpusFamily.model_validate(
                    {
                        **family_payload,
                        "family_sha256": canonical_execution_sha256(family_payload),
                    }
                )
            )
    identity_payload = {
        "schema_version": "claim-corpus-family-inventory/v1",
        "inventory_id": "claim-support-development-families-v1",
        "parent_protocol_sha256": parent_protocol_sha256,
        "source_partition": "development",
        "families": tuple(item.model_dump(mode="json") for item in families),
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
        "labels_generated": False,
    }
    return ClaimCorpusFamilyInventory.model_validate(
        {
            **identity_payload,
            "families": tuple(families),
            "inventory_sha256": canonical_execution_sha256(identity_payload),
        }
    )


def build_request_census(inventory: ClaimCorpusFamilyInventory) -> ClaimCorpusRequestCensus:
    primary: list[ClaimCorpusRequest] = []
    reserve: list[ClaimCorpusRequest] = []
    for role in ("primary", "reserve"):
        for family in (item for item in inventory.families if item.role == role):
            for condition in EVIDENCE_CONDITIONS:
                for variant in ELIGIBLE_VARIANTS:
                    payload = {
                        "family_id": family.family_id,
                        "family_sha256": family.family_sha256,
                        "mechanism": family.mechanism,
                        "family_role": family.role,
                        "evidence_condition": condition,
                        "variant": variant,
                        "seed": family.seed,
                        "source_partition": "development",
                        "provider_call_authorized": False,
                    }
                    request = ClaimCorpusRequest.model_validate(
                        {**payload, "request_sha256": canonical_execution_sha256(payload)}
                    )
                    (primary if role == "primary" else reserve).append(request)
    identity_payload = {
        "schema_version": "claim-corpus-request-census/v1",
        "family_inventory_sha256": inventory.inventory_sha256,
        "primary_requests": tuple(item.model_dump(mode="json") for item in primary),
        "reserve_requests": tuple(item.model_dump(mode="json") for item in reserve),
        "reserve_activation": "pre_execution_technical_ineligibility_only",
        "outcome_driven_activation_forbidden": True,
        "provider_calls_executed": False,
    }
    return ClaimCorpusRequestCensus.model_validate(
        {
            **identity_payload,
            "primary_requests": tuple(primary),
            "reserve_requests": tuple(reserve),
            "census_sha256": canonical_execution_sha256(identity_payload),
        }
    )


def build_amendment(
    root: Path,
    inventory: ClaimCorpusFamilyInventory,
) -> ClaimCorpusProtocolAmendment:
    parent = _resolve_regular(root, PARENT_PROTOCOL_PATH)
    historical_path = "configs/evaluation/claim_support_corpus_feasibility_receipt.json"
    historical = _resolve_regular(root, historical_path)
    payload = {
        "schema_version": "claim-corpus-protocol-amendment/v1",
        "amendment_id": "claim-support-development-corpus-amendment-v1",
        "parent_protocol_path": PARENT_PROTOCOL_PATH,
        "parent_protocol_sha256": inventory.parent_protocol_sha256,
        "historical_receipt_path": historical_path,
        "historical_receipt_sha256": content_sha256(historical.read_bytes()),
        "family_inventory_path": FAMILY_INVENTORY_PATH,
        "family_inventory_sha256": inventory.inventory_sha256,
        "change_scope": "prospective_family_inventory_expansion_only",
        "parent_sampling_policy_unchanged": True,
        "parent_variant_policy_unchanged": True,
        "parent_label_policy_unchanged": True,
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
        "labels_generated": False,
    }
    if _load_object(parent).get("protocol_sha256") != inventory.parent_protocol_sha256:
        raise ClaimCorpusContractError("parent protocol identity changed during amendment")
    return ClaimCorpusProtocolAmendment.model_validate(
        {**payload, "amendment_sha256": canonical_execution_sha256(payload)}
    )


def diagnosis_schema_manifest() -> dict[str, object]:
    claim_type = [
        "cause_assertion",
        "evidence_statement",
        "uncertainty_statement",
        "recommended_action",
        "other",
    ]
    payload: dict[str, object] = {
        "schema_version": "diagnosis-output-schema-manifest/v1",
        "schema_ref": "diagnosis-output/2",
        "frozen_before_provider_calls": True,
        "provider_calls_executed": False,
        "sentence_splitting_forbidden": True,
        "free_text_fallback_forbidden": True,
        "json_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "output_status",
                "atomic_claims",
                "abstention_reason",
                "parse_failure_code",
                "source_record_sha256",
                "output_sha256",
            ],
            "properties": {
                "schema_version": {"type": "string", "const": "diagnosis-output/2"},
                "output_status": {"type": "string", "enum": ["completed", "abstained", "parse_failure"]},
                "atomic_claims": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["claim_local_id", "claim_type", "claim_text", "material_parts", "visible_evidence_ids"],
                        "properties": {
                            "claim_local_id": {"type": "string", "pattern": "^claim-[1-5]$"},
                            "claim_type": {"type": "string", "enum": claim_type},
                            "claim_text": {"type": "string", "minLength": 1, "maxLength": 2048},
                            "material_parts": {"type": "array", "minItems": 1, "maxItems": 8},
                            "visible_evidence_ids": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True},
                        },
                    },
                },
                "abstention_reason": {"type": ["string", "null"]},
                "parse_failure_code": {"type": ["string", "null"]},
                "source_record_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "output_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        },
    }
    return {**payload, "manifest_sha256": canonical_sha256(payload)}


def semantic_fixtures() -> dict[str, object]:
    fixtures = (
        _fixture("contradiction-first", "contradicted", (("contradicts", "partial"), ("supports", "entire"))),
        _fixture("no-support", "unsupported", (("neutral", "none"),)),
        _fixture("partial-support", "partially_supported", (("supports", "partial"),)),
        _fixture("complete-support", "fully_supported", (("supports", "entire"),)),
        _fixture("polarity-control", "contradicted", (("contradicts", "entire"),)),
        _fixture("neutral-plus-support", "partially_supported", (("neutral", "none"), ("supports", "partial"))),
    )
    payload: dict[str, object] = {
        "schema_version": "claim-support-instrument-fixtures/v1",
        "synthetic_semantic_fixtures_only": True,
        "eligible_for_human_validation": False,
        "fixtures": fixtures,
    }
    return {**payload, "fixtures_sha256": canonical_sha256(payload)}


def _fixture(
    fixture_id: str,
    expected: str,
    relations: tuple[tuple[str, str], ...],
) -> dict[str, object]:
    return {
        "fixture_id": fixture_id,
        "claim_text": "The visible metric change supports the bounded diagnosis.",
        "claim_type": "cause_assertion",
        "visible_evidence": tuple(
            {
                "evidence_id": f"fixture-evidence-{index}",
                "text": f"Synthetic relation fixture {index}.",
                "relation_polarity": polarity,
                "relation_scope": scope,
            }
            for index, (polarity, scope) in enumerate(relations, start=1)
        ),
        "expected_label": expected,
    }


def adapter_manifest(root: Path, parent_protocol_sha256: str) -> dict[str, object]:
    fairness = _resolve_regular(root, FAIRNESS_PATH)
    rows = []
    for variant, reference in adapter_inventory():
        module_name, attribute = reference.split(":")
        module = __import__(module_name, fromlist=[attribute])
        function = getattr(module, attribute)
        source_path = Path(inspect.getsourcefile(function) or "")
        rows.append(
            {
                "variant": variant,
                "source_schema_ref": "deterministic-diagnosis/1" if variant == "B0" else "diagnosis-output/2",
                "target_schema_ref": "diagnosis-output/2",
                "implementation_ref": reference,
                "implementation_source_sha256": content_sha256(source_path.read_bytes()),
                "free_text_fallback": False,
            }
        )
    payload: dict[str, object] = {
        "schema_version": "claim-support-adapter-manifest/v1",
        "corpus_protocol_sha256": parent_protocol_sha256,
        "variant_fairness_sha256": content_sha256(fairness.read_bytes()),
        "eligible_variants": ELIGIBLE_VARIANTS,
        "excluded_variant": "B3",
        "adapters": tuple(rows),
        "provider_calls_executed": False,
        "outputs_generated": False,
    }
    return {**payload, "manifest_sha256": canonical_sha256(payload)}


def instrument_manifest(
    root: Path,
    parent_protocol_sha256: str,
    fixture_payload: Mapping[str, object],
) -> dict[str, object]:
    source_path = Path(inspect.getsourcefile(classify_visible_support) or "")
    fixture_count = len(cast(list[object] | tuple[object, ...], fixture_payload["fixtures"]))
    payload: dict[str, object] = {
        "schema_version": "claim-support-automatic-instrument-manifest/v1",
        "corpus_protocol_sha256": parent_protocol_sha256,
        "instrument_version": INSTRUMENT_VERSION,
        "implementation_ref": "aletheia_lab.evaluation.claim_support_instrument:classify_visible_support",
        "implementation_source_sha256": content_sha256(source_path.read_bytes()),
        "semantic_fixture_path": SEMANTIC_FIXTURES_PATH,
        "semantic_fixture_sha256": content_sha256(_json_bytes(fixture_payload)),
        "semantic_fixture_count": fixture_count,
        "permitted_input_fields": ("claim_text", "claim_type", "visible_evidence"),
        "withheld_input_fields": (
            "mechanism",
            "evidence_condition",
            "variant",
            "hidden_ground_truth",
            "human_judgment",
            "main_outcome",
        ),
        "frozen_before_claim_materialization": True,
        "claim_pool_materialized": False,
        "automatic_labels_generated": False,
    }
    return {**payload, "manifest_sha256": canonical_sha256(payload)}


def implementation_manifest(root: Path, parent_protocol_sha256: str) -> dict[str, object]:
    relative_paths = (
        "src/aletheia_lab/evaluation/claim_corpus_contracts.py",
        "src/aletheia_lab/evaluation/claim_corpus_adapters.py",
        "src/aletheia_lab/evaluation/claim_evidence_semantics.py",
        "src/aletheia_lab/evaluation/claim_evidence_census.py",
        "src/aletheia_lab/evaluation/claim_support_instrument.py",
        "src/aletheia_lab/evaluation/claim_corpus_materializer.py",
        "src/aletheia_lab/evaluation/claim_corpus_store.py",
        "src/aletheia_lab/evaluation/claim_corpus_audit.py",
        "src/aletheia_lab/evaluation/claim_corpus_readiness.py",
    )
    payload: dict[str, object] = {
        "schema_version": "claim-support-materialization-implementation/v1",
        "corpus_protocol_sha256": parent_protocol_sha256,
        "components": tuple(
            {
                "path": relative,
                "content_sha256": content_sha256(_resolve_regular(root, relative).read_bytes()),
            }
            for relative in relative_paths
        ),
        "writer_verifier_dependency_forbidden": True,
        "provider_dependency_forbidden": True,
        "create_only_publication_required": True,
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
    }
    return {**payload, "manifest_sha256": canonical_sha256(payload)}


def build_readiness_artifacts(root: Path) -> dict[str, bytes]:
    parent = _load_object(_resolve_regular(root, PARENT_PROTOCOL_PATH))
    parent_sha = parent.get("protocol_sha256")
    if not isinstance(parent_sha, str):
        raise ClaimCorpusContractError("parent corpus protocol has no identity")
    inventory = build_family_inventory(root, parent_sha)
    amendment = build_amendment(root, inventory)
    census = build_request_census(inventory)
    schema = diagnosis_schema_manifest()
    fixtures = semantic_fixtures()
    adapters = adapter_manifest(root, parent_sha)
    instrument = instrument_manifest(root, parent_sha, fixtures)
    implementation = implementation_manifest(root, parent_sha)
    artifacts: dict[str, bytes] = {
        FAMILY_INVENTORY_PATH: _model_payload(inventory),
        AMENDMENT_PATH: _model_payload(amendment),
        REQUEST_CENSUS_PATH: _model_payload(census),
        DIAGNOSIS_SCHEMA_PATH: _json_bytes(schema),
        ADAPTER_MANIFEST_PATH: _json_bytes(adapters),
        SEMANTIC_FIXTURES_PATH: _json_bytes(fixtures),
        INSTRUMENT_MANIFEST_PATH: _json_bytes(instrument),
        IMPLEMENTATION_MANIFEST_PATH: _json_bytes(implementation),
    }
    plan_payload = {
        "schema_version": "claim-corpus-readiness-plan/v1",
        "parent_protocol_sha256": parent_sha,
        "amendment_path": AMENDMENT_PATH,
        "amendment_sha256": amendment.amendment_sha256,
        "family_inventory_path": FAMILY_INVENTORY_PATH,
        "family_inventory_sha256": inventory.inventory_sha256,
        "request_census_path": REQUEST_CENSUS_PATH,
        "request_census_sha256": census.census_sha256,
        "diagnosis_schema_path": DIAGNOSIS_SCHEMA_PATH,
        "diagnosis_schema_sha256": content_sha256(artifacts[DIAGNOSIS_SCHEMA_PATH]),
        "adapter_manifest_path": ADAPTER_MANIFEST_PATH,
        "adapter_manifest_sha256": content_sha256(artifacts[ADAPTER_MANIFEST_PATH]),
        "instrument_manifest_path": INSTRUMENT_MANIFEST_PATH,
        "instrument_manifest_sha256": content_sha256(artifacts[INSTRUMENT_MANIFEST_PATH]),
        "implementation_manifest_path": IMPLEMENTATION_MANIFEST_PATH,
        "implementation_manifest_sha256": content_sha256(
            artifacts[IMPLEMENTATION_MANIFEST_PATH]
        ),
        "primary_request_count": 360,
        "reserve_request_count": 144,
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    plan = ClaimCorpusReadinessPlan.model_validate(
        {**plan_payload, "plan_sha256": canonical_execution_sha256(plan_payload)}
    )
    artifacts[READINESS_PLAN_PATH] = _model_payload(plan)
    receipt_payload = {
        "schema_version": "claim-corpus-readiness-receipt/v1",
        "plan_sha256": plan.plan_sha256,
        "materialization_ready": True,
        "primary_family_count": 15,
        "reserve_family_count": 6,
        "primary_request_count": 360,
        "reserve_request_count": 144,
        "adapter_count": 8,
        "semantic_fixture_count": len(cast(tuple[object, ...], fixtures["fixtures"])),
        "provider_calls_executed": False,
        "outputs_generated": False,
        "development_claim_pool_materialized": False,
        "automatic_labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
        "status": "claim_corpus_materialization_ready_zero_outcome",
    }
    receipt = ClaimCorpusReadinessReceipt.model_validate(
        {**receipt_payload, "receipt_sha256": canonical_execution_sha256(receipt_payload)}
    )
    artifacts[READINESS_RECEIPT_PATH] = _model_payload(receipt)
    return artifacts


def publish_readiness_artifacts(root: Path) -> tuple[str, ...]:
    dispositions = []
    for relative, payload in build_readiness_artifacts(root).items():
        disposition = publish_immutable_file(root / relative, payload)
        dispositions.append(f"{relative}:{disposition}")
    return tuple(dispositions)


def verify_readiness(root: Path) -> ClaimCorpusReadinessReceipt:
    expected = build_readiness_artifacts(root)
    for relative, payload in expected.items():
        path = _resolve_regular(root, relative)
        if path.read_bytes() != payload:
            raise ClaimCorpusContractError(f"tracked readiness artifact changed: {relative}")
    inventory = ClaimCorpusFamilyInventory.model_validate_json(
        expected[FAMILY_INVENTORY_PATH]
    )
    census = ClaimCorpusRequestCensus.model_validate_json(expected[REQUEST_CENSUS_PATH])
    amendment = ClaimCorpusProtocolAmendment.model_validate_json(expected[AMENDMENT_PATH])
    plan = ClaimCorpusReadinessPlan.model_validate_json(expected[READINESS_PLAN_PATH])
    receipt = ClaimCorpusReadinessReceipt.model_validate_json(
        expected[READINESS_RECEIPT_PATH]
    )
    if (
        amendment.family_inventory_sha256 != inventory.inventory_sha256
        or census.family_inventory_sha256 != inventory.inventory_sha256
    ):
        raise ClaimCorpusContractError("request census is not bound to family inventory")
    if plan.request_census_sha256 != census.census_sha256 or receipt.plan_sha256 != plan.plan_sha256:
        raise ClaimCorpusContractError("readiness identity chain is broken")
    _verify_semantic_fixtures(_load_object(root / SEMANTIC_FIXTURES_PATH))
    _require_self_hash(_load_object(root / DIAGNOSIS_SCHEMA_PATH), "manifest_sha256")
    _require_self_hash(_load_object(root / ADAPTER_MANIFEST_PATH), "manifest_sha256")
    _require_self_hash(_load_object(root / INSTRUMENT_MANIFEST_PATH), "manifest_sha256")
    _require_self_hash(
        _load_object(root / IMPLEMENTATION_MANIFEST_PATH), "manifest_sha256"
    )
    return receipt


def _verify_semantic_fixtures(payload: Mapping[str, object]) -> None:
    _require_self_hash(payload, "fixtures_sha256")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list):
        raise ClaimCorpusContractError("semantic fixture corpus is invalid")
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ClaimCorpusContractError("semantic fixture is invalid")
        try:
            evidence = tuple(
                VisibleEvidenceRelation.model_validate(item)
                for item in cast(list[object], fixture["visible_evidence"])
            )
            actual = classify_visible_support(
                claim_text=cast(str, fixture["claim_text"]),
                claim_type=cast(object, fixture["claim_type"]),  # type: ignore[arg-type]
                visible_evidence=evidence,
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise ClaimCorpusContractError("semantic fixture is invalid") from exc
        if actual != fixture.get("expected_label"):
            raise ClaimCorpusContractError("automatic instrument semantic fixture failed")


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (canonical_project_json(payload) + "\n").encode("utf-8")


__all__ = [
    "READINESS_RECEIPT_PATH",
    "build_amendment",
    "build_family_inventory",
    "build_readiness_artifacts",
    "build_request_census",
    "publish_readiness_artifacts",
    "verify_readiness",
]
