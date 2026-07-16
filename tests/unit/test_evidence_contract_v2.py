"""EvidenceBundle v2 and condition-rubric regression tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.case_schema import (
    CaseManifest,
    diagnosis_context_id_for,
    project_diagnosis_input,
)
from aletheia_lab.evidence.collectors import collect_text_log
from aletheia_lab.evidence.rubric import (
    EVIDENCE_CONDITIONS,
    RUBRIC_SCHEMA_VERSION,
    ConditionRubric,
    EvidenceCondition,
    EvidenceRole,
    condition_rubric_for,
    validate_condition_rubric,
    validate_condition_rubric_set,
)
from aletheia_lab.evidence.schema import (
    DIAGNOSIS_EVIDENCE_VIEW_SCHEMA_VERSION,
    EVIDENCE_BUNDLE_SCHEMA_VERSION,
    DiagnosisEvidenceView,
    EvidenceBundle,
    EvidenceItem,
    EvidenceVisibility,
    RedactionState,
    contains_condition_or_rubric_label,
    project_diagnosis_evidence,
)
from aletheia_lab.evidence.store import load_bundle, save_bundle
from aletheia_lab.evidence.validation import (
    validate_bundle_for_case,
    validate_sibling_bundles,
)


def _item(
    evidence_id: str,
    roles: tuple[EvidenceRole, ...],
    *,
    visibility: EvidenceVisibility = "diagnosis",
    redaction_state: RedactionState = "none",
    content: str | None = None,
    source_path: str | None = None,
    metadata: dict | None = None,
) -> EvidenceItem:
    return EvidenceItem.from_content(
        evidence_id=evidence_id,
        kind="metric" if "metric_comparison" in roles else "dataset_profile",
        evidence_roles=roles,
        title=f"Observable item {evidence_id}",
        content=content or f"Observed value for {evidence_id}.",
        source_path=source_path or f"observations/{evidence_id}.json",
        collector_version="test-fixture/1",
        collected_at="2026-07-17T00:00:00+00:00",
        visibility=visibility,
        redaction_state=redaction_state,
        metadata={} if metadata is None else metadata,
    )


def _items_for(condition: EvidenceCondition) -> tuple[EvidenceItem, ...]:
    visible: list[EvidenceItem] = [
        _item("observed-001", ("symptom", "candidate_distribution_observed"))
    ]
    decisive = [
        _item("reference-001", ("candidate_distribution_reference",)),
        _item("psi-001", ("candidate_psi",)),
        _item("metric-001", ("metric_comparison",)),
    ]
    if condition == "missing_key":
        visible.extend(
            EvidenceItem.from_content(
                evidence_id=item.evidence_id,
                kind=item.kind,
                evidence_roles=item.evidence_roles,
                title=item.title,
                content="Evaluator-only withheld measurement.",
                source_path=item.source_path,
                collector_version=item.collector_version,
                collected_at=item.collected_at,
                visibility="evaluator",
                redaction_state="withheld",
                metadata={entry.key: entry.value for entry in item.metadata},
            )
            for item in decisive
        )
    else:
        visible.extend(decisive)
    if condition == "noisy":
        visible.append(_item("distractor-001", ("distractor_comparison",)))
    return tuple(visible)


def _bundle_for_manifest(
    manifest: CaseManifest,
    *,
    items: tuple[EvidenceItem, ...] | None = None,
) -> EvidenceBundle:
    rubric = condition_rubric_for(manifest.evidence_condition)
    bundle_items = _items_for(manifest.evidence_condition) if items is None else items
    visible_roles = {
        role
        for item in bundle_items
        if item.visibility in {"public", "diagnosis"} and item.redaction_state != "withheld"
        for role in item.evidence_roles
    }
    missing = set(rubric.required_evidence_roles) - visible_roles
    return EvidenceBundle(
        schema_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
        evidence_bundle_id=manifest.evidence_bundle_id,
        case_id=manifest.case_id,
        case_family_id=manifest.case_family_id,
        diagnosis_context_id=diagnosis_context_id_for(
            case_id=manifest.case_id, case_family_id=manifest.case_family_id
        ),
        evidence_condition=manifest.evidence_condition,
        dataset_id=manifest.dataset_id,
        dataset_sha256=manifest.dataset_sha256,
        split_manifest_sha256=manifest.split_manifest_sha256,
        items=bundle_items,
        required_evidence_roles=rubric.required_evidence_roles,
        missing_required_evidence_roles=cast(tuple[EvidenceRole, ...], tuple(missing)),
        intentionally_withheld_evidence_roles=rubric.intentionally_withheld_evidence_roles,
    )


def _full_manifest(p1_manifest_factory: Callable[..., dict]) -> CaseManifest:
    return CaseManifest.model_validate(p1_manifest_factory())


def test_condition_rubrics_encode_claim_relative_sufficiency() -> None:
    full = condition_rubric_for("full")
    missing = condition_rubric_for("missing_key")
    noisy = condition_rubric_for("noisy")

    assert full.causal_claim_sufficiency == "sufficient"
    assert noisy.causal_claim_sufficiency == "sufficient"
    assert missing.causal_claim_sufficiency == "insufficient"
    assert "observation" in missing.allowed_claim_levels
    assert "bounded_causal_hypothesis" in missing.allowed_claim_levels
    assert "causal_conclusion" in missing.forbidden_claim_levels
    assert "strong_causal_conclusion" in missing.forbidden_claim_levels
    assert "causal_conclusion" in full.allowed_claim_levels
    assert "causal_conclusion" in noisy.allowed_claim_levels
    assert "reject_unsupported_distractors" in noisy.required_behaviors


def test_canonical_condition_rubric_set_passes() -> None:
    validate_condition_rubric_set(condition_rubric_for(c) for c in EVIDENCE_CONDITIONS)


def test_tampered_expected_behavior_is_rejected() -> None:
    data = condition_rubric_for("full").model_dump()
    data["expected_diagnosis_behavior"] = "abstain_on_cause_and_request_evidence"
    tampered = ConditionRubric.model_validate(data)

    with pytest.raises(ValueError, match="canonical contract"):
        validate_condition_rubric(tampered)


def test_tampered_allowed_claims_are_rejected() -> None:
    data = condition_rubric_for("missing_key").model_dump()
    data["allowed_claim_levels"] = (*data["allowed_claim_levels"], "causal_conclusion")
    data["forbidden_claim_levels"] = tuple(
        level for level in data["forbidden_claim_levels"] if level != "causal_conclusion"
    )
    tampered = ConditionRubric.model_validate(data)

    with pytest.raises(ValueError, match="canonical contract"):
        validate_condition_rubric(tampered)


def test_swapped_sibling_rubric_is_rejected() -> None:
    data = condition_rubric_for("full").model_dump()
    data["condition"] = "noisy"
    swapped = ConditionRubric.model_validate(data)

    with pytest.raises(ValueError, match="canonical contract"):
        validate_condition_rubric(swapped)


def test_unknown_top_level_and_nested_fields_are_rejected(p1_manifest_factory) -> None:
    bundle = _bundle_for_manifest(_full_manifest(p1_manifest_factory))
    top = bundle.model_dump()
    top["answer"] = "extra"
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvidenceBundle.model_validate(top)

    nested = bundle.model_dump()
    nested_item = dict(nested["items"][0])
    nested_item["answer"] = "extra"
    nested["items"] = (nested_item, *nested["items"][1:])
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvidenceBundle.model_validate(nested)


@pytest.mark.parametrize("field", ["schema_version"])
def test_bundle_schema_version_is_locked(p1_manifest_factory, field: str) -> None:
    data = _bundle_for_manifest(_full_manifest(p1_manifest_factory)).model_dump()
    data[field] = "evidence-bundle/999"
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(data)


def test_bundle_schema_version_cannot_be_omitted(p1_manifest_factory) -> None:
    data = _bundle_for_manifest(_full_manifest(p1_manifest_factory)).model_dump()
    data.pop("schema_version")

    with pytest.raises(ValidationError, match="schema_version"):
        EvidenceBundle.model_validate(data)


def test_bundle_validation_state_cannot_bypass_validators(p1_manifest_factory) -> None:
    data = _bundle_for_manifest(_full_manifest(p1_manifest_factory)).model_dump()
    data["validation_state"] = "caller_claimed_pass"
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(data)

    data = _bundle_for_manifest(_full_manifest(p1_manifest_factory)).model_dump()
    data["validation_state"] = "schema_validated"
    data["missing_required_evidence_roles"] = ("metric_comparison",)
    with pytest.raises(ValidationError, match="do not match visible evidence"):
        EvidenceBundle.model_validate(data)


def test_item_schema_version_is_locked() -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["schema_version"] = "evidence-item/999"
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(data)


def test_item_schema_version_cannot_be_omitted() -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data.pop("schema_version")

    with pytest.raises(ValidationError, match="schema_version"):
        EvidenceItem.model_validate(data)


def test_projection_and_rubric_schema_versions_cannot_be_omitted(
    p1_manifest_factory,
) -> None:
    bundle = _bundle_for_manifest(_full_manifest(p1_manifest_factory))
    projection = project_diagnosis_evidence(bundle).model_dump()
    projection.pop("schema_version")
    with pytest.raises(ValidationError, match="schema_version"):
        DiagnosisEvidenceView.model_validate(projection)

    rubric = condition_rubric_for("full").model_dump()
    rubric.pop("schema_version")
    with pytest.raises(ValidationError, match="schema_version"):
        ConditionRubric.model_validate(rubric)

    assert condition_rubric_for("full").schema_version == RUBRIC_SCHEMA_VERSION
    assert (
        project_diagnosis_evidence(bundle).schema_version
        == DIAGNOSIS_EVIDENCE_VIEW_SCHEMA_VERSION
    )


def test_v1_bundle_is_not_silently_accepted() -> None:
    legacy = {
        "evidence_bundle_id": "legacy-001",
        "case_id": "case-001",
        "allowed_evidence": [],
        "withheld_evidence": [],
        "counterfactual_evidence": [],
        "leakage_check_passed": True,
    }
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(legacy)


def test_documented_example_is_valid_v2() -> None:
    bundle = EvidenceBundle.model_validate_json(
        Path("examples/data_drift_case/evidence_bundle.example.json").read_text(encoding="utf-8")
    )
    assert len(bundle.canonical_sha256()) == 64


def test_text_log_collector_confines_source_and_derives_checksum(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    log = root / "logs" / "service.log"
    log.parent.mkdir()
    log.write_text("Observed timeout count: 3.\n", encoding="utf-8")

    item = collect_text_log(
        log,
        "log-001",
        "Service observation",
        source_root=root,
    )
    assert item.source_path == "logs/service.log"
    assert item.content_sha256 == EvidenceItem.from_content(
        evidence_id="same-content",
        kind="log",
        evidence_roles=("symptom",),
        title="same",
        content="Observed timeout count: 3.\n",
        source_path="observations/same-content.log",
        collector_version="test/1",
        visibility="diagnosis",
    ).content_sha256

    outside = tmp_path / "outside.log"
    outside.write_text("Observed value.", encoding="utf-8")
    with pytest.raises(ValueError, match="inside source_root"):
        collect_text_log(outside, "log-002", "Outside", source_root=root)


def test_bundle_store_roundtrip_preserves_canonical_hash(p1_manifest_factory, tmp_path: Path) -> None:
    bundle = _bundle_for_manifest(_full_manifest(p1_manifest_factory))
    path = tmp_path / "bundle.json"
    save_bundle(bundle, path)
    first_bytes = path.read_bytes()
    loaded = load_bundle(path)
    save_bundle(loaded, path)

    assert loaded.canonical_sha256() == bundle.canonical_sha256()
    assert path.read_bytes() == first_bytes


def test_bundle_and_nested_metadata_are_immutable(p1_manifest_factory) -> None:
    bundle = _bundle_for_manifest(_full_manifest(p1_manifest_factory))
    metadata_item = _item("metadata-001", ("symptom",), metadata={"sample_size": 100})
    with pytest.raises(ValidationError, match="frozen"):
        bundle.case_id = "case-999"
    with pytest.raises(ValidationError, match="frozen"):
        bundle.items[0].title = "Changed"
    with pytest.raises(ValidationError, match="frozen"):
        metadata_item.metadata[0].value = 999


def test_duplicate_evidence_id_is_rejected(p1_manifest_factory) -> None:
    bundle = _bundle_for_manifest(_full_manifest(p1_manifest_factory))
    data = bundle.model_dump()
    data["items"] = (*data["items"], data["items"][0])
    with pytest.raises(ValidationError, match="evidence_id must be unique"):
        EvidenceBundle.model_validate(data)


def test_required_and_missing_roles_are_recomputed(p1_manifest_factory) -> None:
    bundle = _bundle_for_manifest(_full_manifest(p1_manifest_factory))

    wrong_required = bundle.model_dump()
    wrong_required["required_evidence_roles"] = ("symptom",)
    with pytest.raises(ValidationError, match="condition rubric"):
        EvidenceBundle.model_validate(wrong_required)

    wrong_missing = bundle.model_dump()
    wrong_missing["missing_required_evidence_roles"] = ("metric_comparison",)
    with pytest.raises(ValidationError, match="do not match visible evidence"):
        EvidenceBundle.model_validate(wrong_missing)


def test_wrong_content_checksum_is_rejected() -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["content_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="content_sha256"):
        EvidenceItem.model_validate(data)


@pytest.mark.parametrize("visibility", ["hidden", "diagnoser", ""])
def test_invalid_visibility_is_rejected(visibility: str) -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["visibility"] = visibility
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(data)


@pytest.mark.parametrize("state", ["secret", "removed", ""])
def test_invalid_redaction_state_is_rejected(state: str) -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["redaction_state"] = state
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(data)


def test_withheld_item_must_be_evaluator_only() -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["redaction_state"] = "withheld"
    with pytest.raises(ValidationError, match="evaluator-only"):
        EvidenceItem.model_validate(data)


@pytest.mark.parametrize(
    "path",
    [
        "/private/metrics.json",
        "../metrics.json",
        "observations/../metrics.json",
        "./metrics.json",
        "observations\\metrics.json",
        "C:\\metrics.json",
    ],
)
def test_noncanonical_or_unsafe_source_path_is_rejected(path: str) -> None:
    with pytest.raises(ValidationError, match="source_path"):
        _item("metric-001", ("metric_comparison",), source_path=path)


def test_source_path_is_required_in_serialized_evidence() -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data.pop("source_path")

    with pytest.raises(ValidationError, match="source_path"):
        EvidenceItem.model_validate(data)


def test_evidence_item_requires_at_least_one_role() -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["evidence_roles"] = ()

    with pytest.raises(ValidationError, match="at least one observable role"):
        EvidenceItem.model_validate(data)


def test_naive_timestamp_and_nonfinite_metadata_are_rejected() -> None:
    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["collected_at"] = "2026-07-17T00:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        EvidenceItem.model_validate(data)

    data = _item("metric-001", ("metric_comparison",)).model_dump()
    data["metadata"] = ({"key": "psi", "value": float("nan")},)
    with pytest.raises(ValidationError, match="finite"):
        EvidenceItem.model_validate(data)


def test_unknown_provenance_link_is_rejected(p1_manifest_factory) -> None:
    manifest = _full_manifest(p1_manifest_factory)
    items = list(_items_for("full"))
    data = items[0].model_dump()
    data["provenance_links"] = ("missing-item",)
    items[0] = EvidenceItem.model_validate(data)
    with pytest.raises(ValidationError, match="unknown evidence IDs"):
        _bundle_for_manifest(manifest, items=tuple(items))


@pytest.mark.parametrize("condition", ["full", "noisy"])
def test_sufficient_condition_cannot_sync_away_missing_decisive_evidence(
    p1_manifest_factory, condition: EvidenceCondition
) -> None:
    slug = "full" if condition == "full" else "noisy"
    manifest = CaseManifest.model_validate(
        p1_manifest_factory(
            case_id=f"p1-data-drift-01-{slug}",
            public_id=f"p1-case-01-{slug}",
            condition=condition,
        )
    )
    bundle = _bundle_for_manifest(manifest)
    data = bundle.model_dump()
    data["items"] = tuple(
        item for item in data["items"] if "metric_comparison" not in item["evidence_roles"]
    )
    data["missing_required_evidence_roles"] = ("metric_comparison",)

    with pytest.raises(ValidationError, match="canonical controlled withholding"):
        EvidenceBundle.model_validate(data)


def test_missing_key_must_materialize_evaluator_held_counterparts(
    p1_manifest_factory,
) -> None:
    manifest = CaseManifest.model_validate(
        p1_manifest_factory(
            case_id="p1-data-drift-01-missing",
            public_id="p1-case-01-missing",
            condition="missing_key",
        )
    )
    bundle = _bundle_for_manifest(manifest)
    data = bundle.model_dump()
    data["items"] = tuple(
        item
        for item in data["items"]
        if not (item["visibility"] == "evaluator" and item["redaction_state"] == "withheld")
    )

    with pytest.raises(ValidationError, match="materialized as evaluator-only"):
        EvidenceBundle.model_validate(data)


def test_diagnosis_visible_id_cannot_encode_evidence_condition(
    p1_manifest_factory,
) -> None:
    manifest = _full_manifest(p1_manifest_factory)
    leaked = _item(
        "missing_key-001",
        (
            "symptom",
            "candidate_distribution_reference",
            "candidate_distribution_observed",
            "candidate_psi",
            "metric_comparison",
        ),
    )

    with pytest.raises(ValidationError, match="exposes a condition label"):
        _bundle_for_manifest(manifest, items=(leaked,))

    assert contains_condition_or_rubric_label(
        {
            "schema_version": "diagnosis-evidence-view/1",
            "diagnosis_context_id": "p1-context-" + "a" * 64,
            "items": [{"evidence_id": "missing_key-001"}],
        }
    )


def test_evaluator_and_withheld_items_do_not_enter_diagnosis_projection(
    p1_manifest_factory,
) -> None:
    manifest = CaseManifest.model_validate(
        p1_manifest_factory(
            case_id="p1-data-drift-01-missing",
            public_id="p1-case-01-missing",
            condition="missing_key",
        )
    )
    bundle = _bundle_for_manifest(manifest)
    view = project_diagnosis_evidence(bundle)
    visible_ids = {item.evidence_id for item in view.items}

    assert visible_ids == {"observed-001"}
    assert {"reference-001", "psi-001", "metric-001"}.isdisjoint(visible_ids)
    assert not contains_condition_or_rubric_label(view.model_dump(mode="json"))
    assert "case_id" not in view.model_dump()
    assert "evidence_bundle_id" not in view.model_dump()
    assert "source_path" not in view.items[0].model_dump()
    assert "provenance_links" not in view.items[0].model_dump()
    assert "visibility" not in view.items[0].model_dump()
    assert "redaction_state" not in view.items[0].model_dump()


@pytest.mark.parametrize(
    ("content", "path"),
    [
        ("The answer key identifies x.", "observations/item.json"),
        ("Observed value.", "ground_truth.json"),
        ("The hidden_failure_cause is x.", "observations/item.json"),
        ("The cause is data drift.", "observations/item.json"),
    ],
)
def test_hidden_ground_truth_cannot_be_diagnosis_visible(
    p1_manifest_factory, content: str, path: str
) -> None:
    manifest = _full_manifest(p1_manifest_factory)
    unsafe = _item(
        "unsafe-001",
        (
            "symptom",
            "candidate_distribution_reference",
            "candidate_distribution_observed",
            "candidate_psi",
            "metric_comparison",
        ),
        content=content,
        source_path=path,
    )
    with pytest.raises(ValidationError, match="hidden marker"):
        _bundle_for_manifest(manifest, items=(unsafe,))


def test_same_payload_hash_is_order_invariant(p1_manifest_factory) -> None:
    manifest = _full_manifest(p1_manifest_factory)
    items = _items_for("full")
    first = _bundle_for_manifest(manifest, items=items)
    second = _bundle_for_manifest(manifest, items=tuple(reversed(items)))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.canonical_sha256() == second.canonical_sha256()
    assert project_diagnosis_evidence(first).canonical_sha256() == project_diagnosis_evidence(
        second
    ).canonical_sha256()


def test_bundle_identity_cross_checks_case_artifacts(p1_manifest_factory) -> None:
    manifest = _full_manifest(p1_manifest_factory)
    bundle = _bundle_for_manifest(manifest)
    diagnosis_input = project_diagnosis_input(manifest)

    validate_bundle_for_case(bundle, manifest, diagnosis_input)

    for field, replacement in (
        ("case_id", "p1-data-drift-99-full"),
        ("case_family_id", "p1-family-" + "f" * 64),
        ("diagnosis_context_id", "p1-context-" + "f" * 64),
        ("evidence_condition", "noisy"),
    ):
        data = bundle.model_dump()
        data[field] = replacement
        if field == "evidence_condition":
            noisy_rubric = condition_rubric_for("noisy")
            data["required_evidence_roles"] = noisy_rubric.required_evidence_roles
            data["intentionally_withheld_evidence_roles"] = ()
            data["missing_required_evidence_roles"] = ()
            data["items"] = (
                *data["items"],
                _item("distractor-001", ("distractor_comparison",)).model_dump(),
            )
        tampered = EvidenceBundle.model_validate(data)
        with pytest.raises(ValueError, match="does not match case"):
            validate_bundle_for_case(tampered, manifest, diagnosis_input)


def test_sibling_bundles_share_family_but_have_unique_contexts(p1_manifest_factory) -> None:
    slugs = {"full": "full", "missing_key": "missing", "noisy": "noisy"}
    bundles = []
    for condition in EVIDENCE_CONDITIONS:
        slug = slugs[condition]
        manifest = CaseManifest.model_validate(
            p1_manifest_factory(
                case_id=f"p1-data-drift-01-{slug}",
                public_id=f"p1-case-01-{slug}",
                condition=condition,
            )
        )
        bundles.append(_bundle_for_manifest(manifest))

    validate_sibling_bundles(bundles)
    assert len({bundle.case_family_id for bundle in bundles}) == 1
    assert len({bundle.case_id for bundle in bundles}) == 3
    assert len({bundle.diagnosis_context_id for bundle in bundles}) == 3
    assert len({bundle.evidence_bundle_id for bundle in bundles}) == 3


def test_bundle_hash_is_stable_across_python_hash_seeds() -> None:
    script = r'''
from aletheia_lab.evidence.schema import EvidenceBundle, EvidenceItem

roles = tuple({"metric_comparison", "candidate_psi", "candidate_distribution_reference",
               "candidate_distribution_observed", "symptom"})
item = EvidenceItem.from_content(
    evidence_id="item-001", kind="metric", evidence_roles=roles,
    title="Observed metrics", content="Observed metric and profile values.",
    source_path="observations/item.json", collector_version="test/1",
    visibility="diagnosis", metadata={"z": 1, "a": 2},
)
bundle = EvidenceBundle(
    schema_version="evidence-bundle/2",
    evidence_bundle_id="bundle-001", case_id="case-001",
    case_family_id="p1-family-" + "1" * 64,
    diagnosis_context_id="p1-context-" + "2" * 64,
    evidence_condition="full", dataset_id="dataset-001",
    dataset_sha256="a" * 64, split_manifest_sha256="b" * 64,
    items=(item,), required_evidence_roles=roles,
    missing_required_evidence_roles=(), intentionally_withheld_evidence_roles=(),
)
print(bundle.canonical_sha256())
'''
    outputs = []
    for seed in ("1", "999"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = "src"
        outputs.append(
            subprocess.check_output(  # noqa: S603 - fixed interpreter and local test script
                [sys.executable, "-c", script],
                cwd=os.getcwd(),
                env=env,
                text=True,
            ).strip()
        )
    assert outputs[0] == outputs[1]
