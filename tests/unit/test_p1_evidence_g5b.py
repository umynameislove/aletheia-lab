"""P1-G5B real collector, immutable store and leakage-review regression tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.case_writer import load_case_dir_schema_only
from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.evidence.collectors import EvidenceCollectionError, collect_p1_bundles
from aletheia_lab.evidence.leakage import (
    HUMAN_REVIEW_ATTESTATION,
    HUMAN_REVIEW_RECORD_SCHEMA_VERSION,
    LEAKAGE_AUDIT_SCHEMA_VERSION,
    BlindReviewPacket,
    HumanFamilyDecision,
    HumanReviewDecision,
    HumanReviewRecord,
    LeakageAuditReport,
    ReviewMappingPacket,
    audit_bundle_leakage,
    build_human_review_packets,
    validate_human_review,
    validate_review_packets,
)
from aletheia_lab.evidence.p1 import (
    BLIND_REVIEW_PACKET_PATH,
    MACHINE_AUDIT_PATH,
    REVIEW_MAPPING_PACKET_PATH,
    generate_p1_evidence_store,
    validate_p1_evidence_store,
)
from aletheia_lab.evidence.schema import EvidenceBundle, EvidenceItem, canonical_json
from aletheia_lab.evidence.store import load_bundle_store, save_bundle_store


@pytest.fixture
def p1_cases(p1_generator_config: Path, tmp_path: Path) -> Path:
    cases = tmp_path / "cases"
    generate_p1(p1_generator_config, cases)
    return cases


def _replace_visible_content(
    bundle: EvidenceBundle, evidence_id: str, content: str
) -> EvidenceBundle:
    replacements = []
    for item in bundle.items:
        if item.evidence_id != evidence_id:
            replacements.append(item)
            continue
        replacements.append(
            EvidenceItem.from_content(
                evidence_id=item.evidence_id,
                kind=item.kind,
                evidence_roles=item.evidence_roles,
                title=item.title,
                content=content,
                source_path=item.source_path,
                collector_version=item.collector_version,
                collected_at=item.collected_at,
                visibility=item.visibility,
                redaction_state=item.redaction_state,
                metadata={entry.key: entry.value for entry in item.metadata},
                provenance_links=item.provenance_links,
            )
        )
    data = bundle.model_dump(mode="json")
    data["items"] = [item.model_dump(mode="json") for item in replacements]
    return EvidenceBundle.model_validate_json(json.dumps(data))


def _audit_artifacts(bundles: tuple[EvidenceBundle, ...]) -> dict[str, tuple[str, object]]:
    audit = audit_bundle_leakage(bundles)
    blind_packet, mapping_packet = build_human_review_packets(bundles)
    return {
        MACHINE_AUDIT_PATH: ("machine-leakage-audit", audit.model_dump(mode="json")),
        BLIND_REVIEW_PACKET_PATH: (
            "human-review-blind-packet",
            blind_packet.model_dump(mode="json"),
        ),
        REVIEW_MAPPING_PACKET_PATH: (
            "human-review-mapping-packet",
            mapping_packet.model_dump(mode="json"),
        ),
    }


def test_collector_builds_canonical_15_bundle_matrix_from_real_case_artifacts(
    p1_cases: Path,
) -> None:
    bundles = collect_p1_bundles(p1_cases)

    assert len(bundles) == 15
    assert Counter(bundle.evidence_condition for bundle in bundles) == {
        "full": 5,
        "missing_key": 5,
        "noisy": 5,
    }
    assert len({bundle.case_family_id for bundle in bundles}) == 5
    context_dirs = {}
    for case_dir in p1_cases.iterdir():
        if case_dir.is_dir():
            context = load_case_dir_schema_only(case_dir).diagnosis_input.diagnosis_context_id
            context_dirs[context] = case_dir
    for bundle in bundles:
        for item in bundle.items:
            metadata = {entry.key: entry.value for entry in item.metadata}
            source_context = metadata["source_context_id"]
            assert isinstance(source_context, str)
            assert (context_dirs[source_context] / item.source_path).is_file()

    by_family: dict[str, dict[str, EvidenceBundle]] = {}
    for bundle in bundles:
        by_family.setdefault(bundle.case_family_id, {})[bundle.evidence_condition] = bundle
    for siblings in by_family.values():
        full_view = {
            item.evidence_id: item.content for item in siblings["full"].diagnosis_visible_items
        }
        noisy_view = {
            item.evidence_id: item.content for item in siblings["noisy"].diagnosis_visible_items
        }
        missing_view = {
            item.evidence_id for item in siblings["missing_key"].diagnosis_visible_items
        }
        assert missing_view == {"candidate-observed"}
        assert siblings["missing_key"].intentionally_withheld_evidence_roles == (
            "candidate_distribution_reference",
            "candidate_psi",
            "metric_comparison",
        )
        assert {
            key: value for key, value in noisy_view.items() if key != "secondary-comparison"
        } == full_view


def test_collector_fails_closed_when_source_case_checksum_is_tampered(p1_cases: Path) -> None:
    diagnosis_path = p1_cases / "p1-data-drift-01-full" / "diagnosis_input.json"
    payload = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    payload["observable_signals"]["sample_size"] += 1
    diagnosis_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceCollectionError, match="integrity gate failed"):
        collect_p1_bundles(p1_cases)


def test_collector_rejects_symlinked_source_even_when_bytes_and_checksum_match(
    p1_cases: Path,
) -> None:
    case_dir = p1_cases / "p1-data-drift-01-full"
    source = case_dir / "diagnosis_input.json"
    moved = case_dir / "diagnosis_input.real.json"
    source.rename(moved)
    source.symlink_to(moved.name)

    with pytest.raises(EvidenceCollectionError, match="must not be symlinks"):
        collect_p1_bundles(p1_cases)


def test_store_roundtrip_is_complete_idempotent_and_byte_reproducible(
    p1_cases: Path, tmp_path: Path
) -> None:
    bundles = collect_p1_bundles(p1_cases)
    artifacts = _audit_artifacts(bundles)
    first = tmp_path / "store-a"
    second = tmp_path / "store-b"

    manifest_a = save_bundle_store(bundles, first, artifacts=artifacts)
    manifest_b = save_bundle_store(bundles, second, artifacts=artifacts)
    save_bundle_store(bundles, first, artifacts=artifacts)
    loaded = load_bundle_store(first)

    assert manifest_a == manifest_b == loaded.manifest
    assert loaded.bundles == bundles
    assert {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    } == {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }


def test_store_rejects_file_tamper_and_unexpected_files(p1_cases: Path, tmp_path: Path) -> None:
    bundles = collect_p1_bundles(p1_cases)
    store = tmp_path / "store"
    manifest = save_bundle_store(bundles, store, artifacts=_audit_artifacts(bundles))
    bundle_path = store / manifest.entries[0].relative_path
    bundle_path.write_bytes(bundle_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="file hash mismatch"):
        load_bundle_store(store)

    clean_store = tmp_path / "clean-store"
    save_bundle_store(bundles, clean_store, artifacts=_audit_artifacts(bundles))
    (clean_store / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="file set differs"):
        load_bundle_store(clean_store)
    with pytest.raises(FileExistsError, match="non-identical"):
        save_bundle_store(bundles, clean_store, artifacts=_audit_artifacts(bundles))

    traversal_store = tmp_path / "traversal-store"
    save_bundle_store(bundles, traversal_store, artifacts=_audit_artifacts(bundles))
    manifest_path = traversal_store / "store-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["entries"][0]["relative_path"] = "../escape.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="store paths"):
        load_bundle_store(traversal_store)


def test_coordinated_rehash_of_neutral_evidence_is_caught_by_recollection(
    p1_cases: Path, tmp_path: Path
) -> None:
    bundles = list(collect_p1_bundles(p1_cases))
    original = bundles[0]
    bundles[0] = _replace_visible_content(
        original,
        "candidate-observed",
        canonical_json(
            {
                "feature": "Contract",
                "sample_size": 1,
                "window": "candidate",
                "distribution": {"fabricated": 1.0},
            }
        ),
    )
    tampered = tuple(bundles)
    store = tmp_path / "tampered-store"
    save_bundle_store(tampered, store, artifacts=_audit_artifacts(tampered))

    report = validate_p1_evidence_store(store, p1_cases)

    assert report.passed is False
    assert report.checks["store_integrity_valid"] is True
    assert report.checks["bundles_match_canonical_collector"] is False


def test_semantic_answer_cue_is_caught_even_when_forged_report_says_pass(
    p1_cases: Path, tmp_path: Path
) -> None:
    bundles = list(collect_p1_bundles(p1_cases))
    bundles[0] = _replace_visible_content(
        bundles[0],
        "candidate-observed",
        "The root cause is an input population change.",
    )
    tampered = tuple(bundles)
    recomputed = audit_bundle_leakage(tampered)
    assert recomputed.passed is False
    assert {finding.rule_id for finding in recomputed.findings} >= {"explicit-causal-answer"}

    forged = LeakageAuditReport(
        schema_version=LEAKAGE_AUDIT_SCHEMA_VERSION,
        bundle_count=15,
        visible_item_count=recomputed.visible_item_count,
        findings=(),
        passed=True,
    )
    blind_packet, mapping_packet = build_human_review_packets(tampered)
    store = tmp_path / "forged-audit-store"
    save_bundle_store(
        tampered,
        store,
        artifacts={
            MACHINE_AUDIT_PATH: (
                "machine-leakage-audit",
                forged.model_dump(mode="json"),
            ),
            BLIND_REVIEW_PACKET_PATH: (
                "human-review-blind-packet",
                blind_packet.model_dump(mode="json"),
            ),
            REVIEW_MAPPING_PACKET_PATH: (
                "human-review-mapping-packet",
                mapping_packet.model_dump(mode="json"),
            ),
        },
    )
    report = validate_p1_evidence_store(store, p1_cases)
    assert report.checks["machine_leakage_audit_recomputed"] is False
    assert report.passed is False


def _passing_review_record(
    blind_packet: BlindReviewPacket, mapping_packet: ReviewMappingPacket
) -> HumanReviewRecord:
    decisions = tuple(
        HumanReviewDecision(
            review_id=entry.review_id,
            diagnosis_view_sha256=entry.diagnosis_view_sha256,
            answer_revealing_cue_found="no",
            design_or_expectation_cue_found="no",
            unsupported_causal_wording_found="no",
            bounded_claim_policy_matches_rubric="yes",
            rationale="No diagnosis-facing leakage or unsupported conclusion found.",
        )
        for entry in mapping_packet.entries
    )
    family_decisions = tuple(
        HumanFamilyDecision(
            family_review_id=family_id,
            core_observations_match="yes",
            declared_omissions_match="yes",
            secondary_comparison_only_addition="yes",
            noise_design_is_hidden="yes",
            mapping_hashes_match="yes",
            rationale="The mapped three-entry family satisfies every paired audit check.",
        )
        for family_id in sorted({entry.family_review_id for entry in mapping_packet.entries})
    )
    return HumanReviewRecord(
        schema_version=HUMAN_REVIEW_RECORD_SCHEMA_VERSION,
        reviewer_kind="human",
        reviewer_id="independent-reviewer",
        started_at="2026-07-17T12:00:00+07:00",
        completed_at="2026-07-17T13:00:00+07:00",
        independent_from_implementation=True,
        prohibited_sources_consulted=False,
        ai_assistance_used=False,
        blind_stage_completed_before_mapping_opened=True,
        attestation=HUMAN_REVIEW_ATTESTATION,
        signature="Independent Reviewer",
        blind_packet_sha256=blind_packet.canonical_sha256(),
        mapping_packet_sha256=mapping_packet.canonical_sha256(),
        decisions=decisions,
        family_decisions=family_decisions,
    )


def test_blind_packet_contains_only_opaque_id_and_diagnosis_view(
    p1_cases: Path,
) -> None:
    blind_packet, mapping_packet = build_human_review_packets(collect_p1_bundles(p1_cases))

    assert len(blind_packet.entries) == len(mapping_packet.entries) == 15
    assert len({entry.family_review_id for entry in mapping_packet.entries}) == 5
    for entry in blind_packet.model_dump(mode="json")["entries"]:
        assert set(entry) == {"review_id", "diagnosis_view"}
    blind_text = canonical_json(blind_packet.model_dump(mode="json")).casefold()
    for forbidden in (
        "distractor",
        "evidence_condition",
        "expected_sufficiency",
        "expected_diagnosis_behavior",
        "expected behavior",
        "bounded_hypothesis_supported",
        "bounded_hypothesis_tentative_only",
        "missing_key",
        '"full"',
        '"noisy"',
    ):
        assert forbidden not in blind_text


def test_mapping_hash_tamper_and_coordinated_entry_swap_fail(
    p1_cases: Path,
) -> None:
    blind_packet, mapping_packet = build_human_review_packets(collect_p1_bundles(p1_cases))
    tampered = mapping_packet.model_dump(mode="json")
    tampered["entries"][0]["binding_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="binding mismatch"):
        ReviewMappingPacket.model_validate_json(json.dumps(tampered))

    swapped = mapping_packet.model_dump(mode="json")
    first = swapped["entries"][0]
    second = swapped["entries"][1]
    first["review_id"], second["review_id"] = second["review_id"], first["review_id"]
    for entry in (first, second):
        binding_payload = {key: value for key, value in entry.items() if key != "binding_sha256"}
        entry["binding_sha256"] = hashlib.sha256(
            canonical_json(binding_payload).encode("utf-8")
        ).hexdigest()
    coordinated_swap = ReviewMappingPacket.model_validate_json(json.dumps(swapped))
    with pytest.raises(ValueError, match="exactly cover"):
        validate_review_packets(blind_packet, coordinated_swap)


def test_human_review_requires_complete_signed_attested_rationalized_record(
    p1_cases: Path,
) -> None:
    blind_packet, mapping_packet = build_human_review_packets(collect_p1_bundles(p1_cases))
    record = _passing_review_record(blind_packet, mapping_packet)
    validate_human_review(blind_packet, mapping_packet, record)

    incomplete = record.model_copy(update={"decisions": record.decisions[:-1]})
    with pytest.raises(ValueError, match="exactly cover"):
        validate_human_review(blind_packet, mapping_packet, incomplete)

    for field in ("attestation", "signature"):
        invalid = record.model_dump(mode="json")
        invalid.pop(field)
        with pytest.raises(ValidationError):
            HumanReviewRecord.model_validate_json(json.dumps(invalid))

    missing_rationale = record.model_dump(mode="json")
    missing_rationale["decisions"][0]["rationale"] = ""
    with pytest.raises(ValidationError, match="requires a rationale"):
        HumanReviewRecord.model_validate_json(json.dumps(missing_rationale))


def test_uncertain_or_blocking_review_cannot_be_promoted_to_pass(
    p1_cases: Path,
) -> None:
    blind_packet, mapping_packet = build_human_review_packets(collect_p1_bundles(p1_cases))
    record = _passing_review_record(blind_packet, mapping_packet)
    uncertain = record.decisions[0].model_copy(
        update={"bounded_claim_policy_matches_rubric": "uncertain"}
    )
    uncertain_record = record.model_copy(update={"decisions": (uncertain, *record.decisions[1:])})
    with pytest.raises(ValueError, match="blocker or uncertain"):
        validate_human_review(blind_packet, mapping_packet, uncertain_record)

    blocked = record.family_decisions[0].model_copy(update={"noise_design_is_hidden": "no"})
    blocked_record = record.model_copy(
        update={"family_decisions": (blocked, *record.family_decisions[1:])}
    )
    with pytest.raises(ValueError, match="blocker or uncertain"):
        validate_human_review(blind_packet, mapping_packet, blocked_record)


def test_generate_and_validate_g5b_store_marks_only_human_step_pending(
    p1_cases: Path, tmp_path: Path
) -> None:
    store = tmp_path / "g5b-store"
    manifest = generate_p1_evidence_store(p1_cases, store)
    report = validate_p1_evidence_store(store, p1_cases)

    assert manifest.bundle_count == 15
    assert report.passed is True
    assert report.machine_leakage_findings == 0
    assert report.human_review_status == "pending"
    assert all(report.checks.values())


def test_evidence_store_is_byte_identical_across_python_hash_seeds(
    p1_cases: Path, tmp_path: Path
) -> None:
    outputs = [tmp_path / "seed-1", tmp_path / "seed-999"]
    script = (
        "from aletheia_lab.evidence.p1 import generate_p1_evidence_store; "
        "import sys; generate_p1_evidence_store(sys.argv[1], sys.argv[2])"
    )
    for seed, output in zip(("1", "999"), outputs, strict=True):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = "src"
        subprocess.run(  # noqa: S603 - fixed interpreter and local test script
            [sys.executable, "-c", script, str(p1_cases), str(output)],
            check=True,
            cwd=Path.cwd(),
            env=env,
        )
    first = {
        path.relative_to(outputs[0]).as_posix(): path.read_bytes()
        for path in outputs[0].rglob("*")
        if path.is_file()
    }
    second = {
        path.relative_to(outputs[1]).as_posix(): path.read_bytes()
        for path in outputs[1].rglob("*")
        if path.is_file()
    }
    assert first == second
