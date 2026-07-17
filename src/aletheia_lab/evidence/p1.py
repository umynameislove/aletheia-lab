"""End-to-end P1-G5B evidence collection and independent validation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from aletheia_lab.benchmark.case_validation import EXPECTED_CASE_COUNT, validate_p1_cases
from aletheia_lab.benchmark.case_writer import LoadedCase, load_case_dir_schema_only
from aletheia_lab.evidence.collectors import EvidenceCollectionError, collect_p1_bundles
from aletheia_lab.evidence.leakage import (
    BlindReviewPacket,
    HumanReviewRecord,
    LeakageAuditReport,
    ReviewMappingPacket,
    audit_bundle_leakage,
    build_human_review_packets,
    validate_human_review,
    validate_review_packets,
)
from aletheia_lab.evidence.schema import EvidenceBundle
from aletheia_lab.evidence.store import (
    EvidenceStoreManifest,
    load_bundle_store,
    save_bundle_store,
)
from aletheia_lab.evidence.validation import validate_bundle_for_case, validate_sibling_bundles

MACHINE_AUDIT_PATH = "audit/machine-leakage-report.json"
BLIND_REVIEW_PACKET_PATH = "audit/human-review-blind-packet.json"
REVIEW_MAPPING_PACKET_PATH = "audit/human-review-mapping-packet.json"


@dataclass
class P1EvidenceValidationReport:
    """Independent validation result; no caller-controlled PASS shortcut."""

    passed: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    bundle_count: int = 0
    machine_leakage_findings: int = 0
    human_review_status: Literal["pending", "passed", "failed"] = "pending"

    def record(self, name: str, ok: bool, error: str | None = None) -> None:
        self.checks[name] = ok
        if not ok:
            self.passed = False
            self.errors.append(error or name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "errors": self.errors[:10],
            "bundle_count": self.bundle_count,
            "machine_leakage_findings": self.machine_leakage_findings,
            "human_review_status": self.human_review_status,
        }


def _case_map(cases_dir: Path) -> dict[str, LoadedCase]:
    result: dict[str, LoadedCase] = {}
    for path in sorted(cases_dir.iterdir()):
        if path.is_dir() and (path / "manifest.json").is_file():
            case = load_case_dir_schema_only(path)
            result[case.manifest.evidence_bundle_id] = case
    return result


def generate_p1_evidence_store(
    cases_dir: str | Path, output_dir: str | Path
) -> EvidenceStoreManifest:
    """Collect 15 real bundles, audit them and persist one immutable store."""

    bundles = collect_p1_bundles(cases_dir)
    machine_audit = audit_bundle_leakage(bundles)
    if not machine_audit.passed:
        raise ValueError(
            f"refusing to persist evidence with leakage findings: {machine_audit.findings}"
        )
    blind_packet, mapping_packet = build_human_review_packets(bundles)
    if len(blind_packet.entries) != EXPECTED_CASE_COUNT:
        raise ValueError("human review packets must cover all 15 contexts")
    manifest = save_bundle_store(
        bundles,
        output_dir,
        artifacts={
            MACHINE_AUDIT_PATH: (
                "machine-leakage-audit",
                machine_audit.model_dump(mode="json"),
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
    report = validate_p1_evidence_store(output_dir, cases_dir)
    if not report.passed:
        raise ValueError(f"persisted P1 evidence store failed validation: {report.as_dict()}")
    return manifest


def validate_p1_evidence_store(
    store_dir: str | Path,
    cases_dir: str | Path,
    *,
    human_review_path: str | Path | None = None,
) -> P1EvidenceValidationReport:
    """Recompute the full G5B technical gate and optionally verify human sign-off."""

    report = P1EvidenceValidationReport()
    cases_root = Path(cases_dir)
    case_report = validate_p1_cases(cases_root)
    report.record(
        "source_cases_valid",
        case_report.passed,
        f"source P1 cases failed validation: {case_report.as_dict()}",
    )
    if not case_report.passed:
        return report

    try:
        loaded_store = load_bundle_store(store_dir)
    except Exception as exc:  # noqa: BLE001 - integrity gate reports all invalid stores
        report.record("store_integrity_valid", False, f"invalid evidence store: {exc}")
        return report
    report.record("store_integrity_valid", True)
    bundles = loaded_store.bundles
    report.bundle_count = len(bundles)
    report.record(
        "exactly_15_bundles",
        len(bundles) == EXPECTED_CASE_COUNT,
        f"expected {EXPECTED_CASE_COUNT} bundles, got {len(bundles)}",
    )
    condition_counts = Counter(bundle.evidence_condition for bundle in bundles)
    report.record(
        "five_bundles_per_condition",
        condition_counts == {"full": 5, "missing_key": 5, "noisy": 5},
        f"condition counts differ from 5/5/5: {dict(condition_counts)}",
    )

    cases = _case_map(cases_root)
    cross_errors: list[str] = []
    family_bundles: dict[str, list[EvidenceBundle]] = defaultdict(list)
    for bundle in bundles:
        case = cases.get(bundle.evidence_bundle_id)
        if case is None:
            cross_errors.append(f"no source case for {bundle.evidence_bundle_id}")
            continue
        try:
            validate_bundle_for_case(bundle, case.manifest, case.diagnosis_input)
        except ValueError as exc:
            cross_errors.append(f"{bundle.evidence_bundle_id}: {exc}")
        family_bundles[bundle.case_family_id].append(bundle)
    for family_id, siblings in family_bundles.items():
        try:
            validate_sibling_bundles(siblings)
        except ValueError as exc:
            cross_errors.append(f"{family_id}: {exc}")
    report.record(
        "bundles_match_source_cases",
        not cross_errors and len(cases) == EXPECTED_CASE_COUNT,
        "; ".join(cross_errors[:5]) if cross_errors else "source case count differs from 15",
    )

    try:
        canonical_bundles = collect_p1_bundles(cases_root)
        canonical_by_id = {
            bundle.evidence_bundle_id: bundle.model_dump(mode="json")
            for bundle in canonical_bundles
        }
        persisted_by_id = {
            bundle.evidence_bundle_id: bundle.model_dump(mode="json") for bundle in bundles
        }
        collector_match = persisted_by_id == canonical_by_id
    except (EvidenceCollectionError, OSError, ValueError) as exc:
        collector_match = False
        report.errors.append(f"canonical evidence recollection failed: {exc}")
    report.record(
        "bundles_match_canonical_collector",
        collector_match,
        "persisted evidence differs from deterministic recollection of source cases",
    )

    artifacts = loaded_store.artifact_payloads
    try:
        recorded_audit = LeakageAuditReport.model_validate_json(
            json.dumps(artifacts[MACHINE_AUDIT_PATH])
        )
        recomputed_audit = audit_bundle_leakage(bundles)
        machine_ok = recorded_audit == recomputed_audit and recomputed_audit.passed
        report.machine_leakage_findings = len(recomputed_audit.findings)
    except (KeyError, ValueError) as exc:
        machine_ok = False
        report.errors.append(f"invalid machine leakage artifact: {exc}")
    report.record(
        "machine_leakage_audit_recomputed",
        machine_ok,
        "recorded leakage report differs from recomputation or contains findings",
    )

    try:
        recorded_blind_packet = BlindReviewPacket.model_validate_json(
            json.dumps(artifacts[BLIND_REVIEW_PACKET_PATH])
        )
        recorded_mapping_packet = ReviewMappingPacket.model_validate_json(
            json.dumps(artifacts[REVIEW_MAPPING_PACKET_PATH])
        )
        rebuilt_blind_packet, rebuilt_mapping_packet = build_human_review_packets(bundles)
        validate_review_packets(recorded_blind_packet, recorded_mapping_packet)
        packet_ok = (
            recorded_blind_packet == rebuilt_blind_packet
            and recorded_mapping_packet == rebuilt_mapping_packet
            and len(recorded_blind_packet.entries) == 15
        )
    except (KeyError, ValueError) as exc:
        packet_ok = False
        recorded_blind_packet = None
        recorded_mapping_packet = None
        report.errors.append(f"invalid human review packets: {exc}")
    report.record(
        "human_review_packets_are_bound_15_context_census",
        packet_ok,
        "recorded human review packets differ from the bound 15-context census",
    )

    if human_review_path is None:
        report.human_review_status = "pending"
    elif not packet_ok or recorded_blind_packet is None or recorded_mapping_packet is None:
        report.human_review_status = "failed"
        report.record("human_review_passed", False, "cannot review an invalid packet")
    else:
        try:
            record = HumanReviewRecord.model_validate_json(
                Path(human_review_path).read_text(encoding="utf-8")
            )
            validate_human_review(
                recorded_blind_packet,
                recorded_mapping_packet,
                record,
            )
        except (OSError, ValueError) as exc:
            report.human_review_status = "failed"
            report.record("human_review_passed", False, f"human review failed: {exc}")
        else:
            report.human_review_status = "passed"
            report.record("human_review_passed", True)
    return report
