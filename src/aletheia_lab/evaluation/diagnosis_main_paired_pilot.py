"""Family-paired, outcome-preserving human-label pilot for the P5 recovery.

The frozen P5 result is never edited. Sampling is over its 32 families, not
over claims; every scored B1/A3 claim in all three core conditions of a drawn
family is assigned to both blind raters.
"""

from __future__ import annotations

import hmac
import io
import json
import math
import zipfile
from collections import Counter
from typing import Any

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    CORE_CONDITIONS,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import classify_output
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    DiagnosisMainScoringPreparation,
)
from aletheia_lab.evaluation.diagnosis_main_measurement_impact import (
    ARMS,
    LABELS,
    ImpactFrame,
    _build_frame,
    _harm,
    _validate_recovery,
)
from aletheia_lab.project.identity import content_sha256

PILOT_FAMILY_COUNT = 8
POPULATION_FAMILY_COUNT = 32
TARGET_HALF_WIDTH = 0.05


class PairedPilotError(ValueError):
    """The pilot could not preserve the frozen frame or blind rating rules."""


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def sha256(data: bytes) -> str:
    return content_sha256(data)


def _rank(key: bytes, domain: str, identity: str) -> bytes:
    return hmac.digest(key, f"{domain}:{identity}".encode(), "sha256")


def select_families(family_ids: tuple[str, ...], seed: bytes) -> tuple[str, ...]:
    """An equal-probability SRSWOR of whole families; no outcome enters ranking."""

    if len(seed) != 32 or len(family_ids) != POPULATION_FAMILY_COUNT or len(set(family_ids)) != 32:
        raise PairedPilotError("the family frame or sampling seed is invalid")
    ranked = sorted(family_ids, key=lambda family: (_rank(seed, "family", family), family))
    return tuple(sorted(ranked[:PILOT_FAMILY_COUNT]))


def _zip(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 9, 29, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, body)
    result = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        if set(archive.namelist()) != set(files) or any(
            archive.read(name) != body for name, body in files.items()
        ):
            raise PairedPilotError("blind ZIP did not round-trip")
    return result


def _blind_files(
    *,
    slot: str,
    rows: list[dict[str, Any]],
    key: bytes,
    guide: str,
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: _rank(key, slot, row["claim_id"]))
    packet_id = "p5-impact-" + _rank(key, "packet", slot).hex()
    items: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for index, row in enumerate(ordered, 1):
        number = f"M{index:03d}"
        blind_id = "blind-" + _rank(key, slot + ":item", row["claim_id"]).hex()
        items.append(
            {
                "number": number,
                "blind_claim_id": blind_id,
                "claim_text": row["claim_text"],
                "visible_evidence": row["visible_evidence"],
            }
        )
        mapping.append(
            {
                "slot": slot,
                "number": number,
                "blind_claim_id": blind_id,
                "source_claim_id": row["claim_id"],
                "evidence_ids": [item["evidence_id"] for item in row["visible_evidence"]],
            }
        )
        decisions.append(
            {
                "number": number,
                "blind_claim_id": blind_id,
                "support_label": None,
                "evidence_ids_used": [],
                "rationale": None,
            }
        )
    packet = {
        "schema_version": "p5-impact-family-paired-blind/v1",
        "packet_id": packet_id,
        "rater_slot": slot,
        "items": items,
    }
    template = {
        "schema_version": "p5-impact-family-paired-submission/v1",
        "packet_id": packet_id,
        "rater_slot": slot,
        "decisions": decisions,
        "attestation": {
            "completed_by_assigned_human": None,
            "completed_without_ai_assistance": None,
            "completed_independently": None,
            "did_not_view_machine_labels_or_other_rater": None,
        },
    }
    files = {
        "GUIDE.md": guide.encode(),
        "JOB.md": (
            b"# P5 family-paired claim audit\n\n"
            b"Read GUIDE.md, then rate every numbered item in items.json. "
            b"Copy submission-template.json to completed-submission.json and fill only "
            b"the label, evidence IDs, rationale, and truthful attestation fields. "
            b"Keep IDs and order unchanged. Work independently; do not use AI, "
            b"look up machine labels, or discuss answers with the other rater. "
            b"Return only the completed JSON to the coordinator. This packet "
            b"is a new P5 sensitivity pilot, not the earlier 220-claim audit.\n"
        ),
        "items.json": json_bytes(packet),
        "submission-template.json": json_bytes(template),
    }
    for body in files.values():
        if any(marker in body for marker in (b"/Users/", b"OPENAI_API_KEY", b"sk-proj-")):
            raise PairedPilotError("a path or secret marker appears in a blind file")
    return files, mapping


def _selected_records(
    plan: DiagnosisMainAnalysisPlan,
    recovery_input: DiagnosisMainAnalysisInput,
    preparation: DiagnosisMainScoringPreparation,
    frame: ImpactFrame,
    selected: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prepared_by_request = {record.request_id: record for record in preparation.records}
    observed_by_request = {record.request_id: record for record in recovery_input.records}
    if len(prepared_by_request) != 1024 or set(prepared_by_request) != set(observed_by_request):
        raise PairedPilotError("prepared and analyzed request frames differ")
    rows: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for family_id in selected:
        for condition in CORE_CONDITIONS:
            for arm in ARMS:
                request_id = frame.requests_by_cell[(family_id, condition, arm)]
                observed = observed_by_request[request_id]
                prepared = prepared_by_request[request_id]
                if prepared.request_sha256 != observed.request_sha256 or {
                    claim.claim_id for claim in prepared.claims
                } != {claim.claim_id for claim in observed.claims}:
                    raise PairedPilotError("prepared claim identity differs from analyzed output")
                category, primary = classify_output(
                    observed, condition, plan.partial_support_primary_weight
                )
                _, sensitivity = classify_output(
                    observed, condition, plan.partial_support_sensitivity_weight
                )
                claims = (
                    [claim.claim_id for claim in observed.claims]
                    if category == "claim_scored"
                    else []
                )
                outputs.append(
                    {
                        "family_id": family_id,
                        "condition": condition,
                        "arm": arm,
                        "request_id": request_id,
                        "category": category,
                        "claim_ids": claims,
                        "automatic_primary_loss": primary,
                        "automatic_partial_half_loss": sensitivity,
                    }
                )
                if category != "claim_scored":
                    continue
                for claim in prepared.claims:
                    relation = claim.relation_request
                    rows.append(
                        {
                            "claim_id": claim.claim_id,
                            "request_id": request_id,
                            "automatic_label": frame.units[claim.claim_id].auto_label,
                            "claim_text": relation.claim_text,
                            "visible_evidence": [
                                {
                                    "evidence_id": item.evidence_id,
                                    "kind": item.kind,
                                    "title": item.title,
                                    "content": item.content,
                                }
                                for item in relation.visible_evidence
                            ],
                        }
                    )
    if len(outputs) != PILOT_FAMILY_COUNT * len(CORE_CONDITIONS) * len(ARMS):
        raise PairedPilotError("pilot output pairs are incomplete")
    if len(rows) != len({row["claim_id"] for row in rows}) or not rows:
        raise PairedPilotError("pilot claim census is duplicate or empty")
    return rows, outputs


def _frozen_effect(frame: ImpactFrame, weight: float) -> float:
    return math.fsum(
        frame.output_losses[(family_id, condition, "B1")][weight]
        - frame.output_losses[(family_id, condition, "A3")][weight]
        for family_id in frame.family_ids
        for condition in CORE_CONDITIONS
    ) / (POPULATION_FAMILY_COUNT * len(CORE_CONDITIONS))


def build_pilot(
    *,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    recovery_input: DiagnosisMainAnalysisInput,
    recovery_report: DiagnosisMainAnalysisReport,
    preparation: DiagnosisMainScoringPreparation,
    seed: bytes,
    blind_key: bytes,
    guide: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Bind an eight-family pilot and two blind deliveries to locked sources."""

    if len(blind_key) != 32:
        raise PairedPilotError("blind key must be 32 bytes")
    primary_report, sensitivity_report, _ = _validate_recovery(
        plan, census, recovery_input, recovery_report
    )
    frame = _build_frame(plan, census, recovery_input, recovery_report)
    if (
        preparation.preparation_sha256
        != ("47238f44d8959a60e0f5c3b4dc56693691760ee354c8bcd473cda94bdec8189d")
        or preparation.analysis_plan_sha256 != plan.plan_sha256
    ):
        raise PairedPilotError("materialized claim source differs from frozen recovery")
    selected = select_families(frame.family_ids, seed)
    rows, outputs = _selected_records(plan, recovery_input, preparation, frame, selected)
    primary_full = _frozen_effect(frame, plan.partial_support_primary_weight)
    if not math.isclose(primary_full, primary_report.estimate, abs_tol=1e-12, rel_tol=0):
        raise PairedPilotError("frozen primary contrast does not reproduce")
    sensitivity_full = _frozen_effect(frame, plan.partial_support_sensitivity_weight)
    if not math.isclose(sensitivity_full, sensitivity_report.estimate, abs_tol=1e-12, rel_tol=0):
        raise PairedPilotError("frozen partial-half contrast does not reproduce")
    deliveries: dict[str, bytes] = {}
    maps: list[dict[str, Any]] = []
    packet_ids: dict[str, str] = {}
    for slot in ("rater_1", "rater_2"):
        files, mapping = _blind_files(slot=slot, rows=rows, key=blind_key, guide=guide)
        packet_ids[slot] = json.loads(files["items.json"])["packet_id"]
        deliveries[f"{slot}.zip"] = _zip(files)
        maps.extend(mapping)
    if Counter(item["source_claim_id"] for item in maps) != Counter(
        {row["claim_id"]: 2 for row in rows}
    ):
        raise PairedPilotError("rater packets do not contain the same claim set")
    coordinator = {
        "schema_version": "p5-impact-family-paired-pilot/v1",
        "status": "blind_pilot_prepared_not_rated",
        "scientific_scope": "secondary_finite_32_family_recovery_sensitivity",
        "family_selection": "equal_probability_srswor_without_replacement",
        "population_family_count": POPULATION_FAMILY_COUNT,
        "pilot_family_count": PILOT_FAMILY_COUNT,
        "family_inclusion_probability": PILOT_FAMILY_COUNT / POPULATION_FAMILY_COUNT,
        "sampling_seed_hex_coordinator_only": seed.hex(),
        "selected_family_ids": list(selected),
        "target_descriptive_half_width_for_later_sizing": TARGET_HALF_WIDTH,
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "recovery_input_sha256": recovery_input.input_sha256,
        "recovery_report_sha256": recovery_report.report_sha256,
        "preparation_sha256": preparation.preparation_sha256,
        "frozen_primary_B1_minus_A3": primary_report.estimate,
        "frozen_partial_half_B1_minus_A3": sensitivity_report.estimate,
        "packet_ids": packet_ids,
        "outputs": outputs,
        "claims": [
            {
                "claim_id": row["claim_id"],
                "request_id": row["request_id"],
                "automatic_label": row["automatic_label"],
            }
            for row in rows
        ],
        "mapping": maps,
        "blind_zip_sha256": {name: sha256(body) for name, body in deliveries.items()},
        "frozen_outcomes_mutated": False,
        "provider_calls": 0,
    }
    return coordinator, deliveries


def _read_submission(
    payload: dict[str, Any], coordinator: dict[str, Any], slot: str
) -> dict[str, str]:
    attestation = payload.get("attestation")
    if (
        payload.get("schema_version") != "p5-impact-family-paired-submission/v1"
        or payload.get("packet_id") != coordinator["packet_ids"][slot]
        or payload.get("rater_slot") != slot
        or not isinstance(payload.get("decisions"), list)
        or not isinstance(attestation, dict)
        or set(attestation.values()) != {True}
        or set(attestation)
        != {
            "completed_by_assigned_human",
            "completed_without_ai_assistance",
            "completed_independently",
            "did_not_view_machine_labels_or_other_rater",
        }
    ):
        raise PairedPilotError("independent human submission is invalid")
    expected = {
        item["blind_claim_id"]: item for item in coordinator["mapping"] if item["slot"] == slot
    }
    observed: dict[str, str] = {}
    for row in payload["decisions"]:
        if not isinstance(row, dict):
            raise PairedPilotError("human decision must be an object")
        blind_id = row.get("blind_claim_id")
        mapped = expected.get(blind_id)
        evidence_used = row.get("evidence_ids_used")
        if (
            mapped is None
            or blind_id in observed
            or set(row)
            != {
                "number",
                "blind_claim_id",
                "support_label",
                "evidence_ids_used",
                "rationale",
            }
            or row.get("number") != mapped["number"]
            or row.get("support_label") not in LABELS
            or not isinstance(row.get("rationale"), str)
            or len(row["rationale"].strip()) < 20
            or not isinstance(evidence_used, list)
            or not all(isinstance(e, str) for e in evidence_used)
            or len(evidence_used) != len(set(evidence_used))
            or any(e not in mapped["evidence_ids"] for e in evidence_used)
            or (row["support_label"] != "unsupported" and not evidence_used)
        ):
            raise PairedPilotError("human decisions are incomplete or changed")
        observed[mapped["source_claim_id"]] = row["support_label"]
    if len(observed) != len(expected):
        raise PairedPilotError("human submission does not cover the selected claim census")
    return observed


def disagreements(
    coordinator: dict[str, Any], first: dict[str, Any], second: dict[str, Any]
) -> dict[str, tuple[str, str]]:
    a = _read_submission(first, coordinator, "rater_1")
    b = _read_submission(second, coordinator, "rater_2")
    return {claim: (a[claim], b[claim]) for claim in sorted(a) if a[claim] != b[claim]}


def blind_adjudication_packet(
    coordinator: dict[str, Any],
    first_zip: bytes,
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, bytes]:
    """Give the third human only disputed claim/evidence and two blind labels."""

    if sha256(first_zip) != coordinator["blind_zip_sha256"]["rater_1.zip"]:
        raise PairedPilotError("first blind packet does not match coordinator")
    disputed = disagreements(coordinator, first, second)
    with zipfile.ZipFile(io.BytesIO(first_zip)) as archive:
        packet = json.loads(archive.read("items.json"))
    mapped = {
        row["source_claim_id"]: row["blind_claim_id"]
        for row in coordinator["mapping"]
        if row["slot"] == "rater_1"
    }
    items = {item["blind_claim_id"]: item for item in packet["items"]}
    selected = [
        {
            **items[mapped[claim_id]],
            "independent_labels": list(disputed[claim_id]),
        }
        for claim_id in disputed
    ]
    template = {
        "schema_version": "p5-impact-blind-adjudication/v1",
        "decisions": [
            {"blind_claim_id": item["blind_claim_id"], "support_label": None, "rationale": None}
            for item in selected
        ],
        "attestation": (
            {
                "same_human_completed_all_disagreements": None,
                "automatic_labels_not_viewed": None,
                "arm_and_mapping_not_viewed": None,
            }
            if selected
            else None
        ),
    }
    return {
        "GUIDE.md": (
            b"# Independent resolution of disagreements\n\n"
            b"Read only the claim and visible evidence; the two human labels are "
            b"not an answer key. Do not access automatic labels, arm, family, "
            b"source mapping, AI, or the original raters' rationales. Choose "
            b"one of fully_supported, partially_supported, unsupported, or "
            b"contradicted for each item, and explain the evidence. Complete "
            b"the attestation truthfully.\n"
        ),
        "items.json": json_bytes(
            {"schema_version": "p5-impact-blind-adjudication-packet/v1", "items": selected}
        ),
        "adjudication-template.json": json_bytes(template),
    }


def _family_delta(
    coordinator: dict[str, Any], final: dict[str, str], partial_weight: float
) -> dict[str, float]:
    deltas: dict[str, float] = {}
    outputs = coordinator["outputs"]
    for family_id in coordinator["selected_family_ids"]:
        terms: list[float] = []
        for condition in CORE_CONDITIONS:
            for arm in ARMS:
                output = next(
                    item
                    for item in outputs
                    if (item["family_id"], item["condition"], item["arm"])
                    == (family_id, condition, arm)
                )
                if output["category"] != "claim_scored":
                    continue
                auto = output[
                    "automatic_primary_loss"
                    if partial_weight == 0.0
                    else "automatic_partial_half_loss"
                ]
                human = math.fsum(
                    _harm(final[claim_id], partial_weight) for claim_id in output["claim_ids"]
                ) / len(output["claim_ids"])
                terms.append((1.0 if arm == "B1" else -1.0) * (human - auto) / len(CORE_CONDITIONS))
        deltas[family_id] = math.fsum(terms)
    return deltas


def _estimate(coordinator: dict[str, Any], final: dict[str, str], weight: float) -> dict[str, Any]:
    deltas = _family_delta(coordinator, final, weight)
    values = list(deltas.values())
    n = len(values)
    mean = math.fsum(values) / n
    variance = math.fsum((value - mean) ** 2 for value in values) / (n - 1)
    se = math.sqrt((1.0 - n / POPULATION_FAMILY_COUNT) * variance / n)
    baseline = coordinator[
        "frozen_primary_B1_minus_A3" if weight == 0.0 else "frozen_partial_half_B1_minus_A3"
    ]
    if variance == 0.0:
        suggested = None
    else:
        z2s2 = 1.96**2 * variance
        suggested = min(
            POPULATION_FAMILY_COUNT,
            max(
                n,
                math.ceil(
                    POPULATION_FAMILY_COUNT
                    * z2s2
                    / (POPULATION_FAMILY_COUNT * TARGET_HALF_WIDTH**2 + z2s2)
                ),
            ),
        )
    return {
        "frozen_automatic_effect": baseline,
        "estimated_family_mean_relabel_correction": mean,
        "pilot_estimated_human_relabel_effect": baseline + mean,
        "pilot_design_standard_error": se,
        "pilot_variance_of_paired_family_correction": variance,
        "normal_approx_95_half_width_for_planning_only": 1.96 * se if variance else None,
        "plug_in_total_family_count_for_0_05_half_width": suggested,
        "zero_pilot_variance_requires_more_families_not_zero_uncertainty": variance == 0.0,
        "family_count": n,
    }


def analyze_pilot(
    coordinator: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    """Use locked independent ratings and blind resolution; never edit P5."""

    if coordinator.get("schema_version") != "p5-impact-family-paired-pilot/v1":
        raise PairedPilotError("pilot coordinator version is unknown")
    a = _read_submission(first, coordinator, "rater_1")
    b = _read_submission(second, coordinator, "rater_2")
    disputed = {claim for claim in a if a[claim] != b[claim]}
    expected_attestation = {
        "same_human_completed_all_disagreements": True,
        "automatic_labels_not_viewed": True,
        "arm_and_mapping_not_viewed": True,
    }
    if (
        adjudication.get("schema_version") != "p5-impact-blind-adjudication/v1"
        or (
            adjudication.get("attestation") != expected_attestation
            if disputed
            else adjudication.get("attestation") is not None
        )
        or not isinstance(adjudication.get("decisions"), list)
    ):
        raise PairedPilotError("blind adjudication is missing or invalid")
    adjudicated: dict[str, str] = {}
    blind_to_source = {
        row["blind_claim_id"]: row["source_claim_id"]
        for row in coordinator["mapping"]
        if row["slot"] == "rater_1"
    }
    for decision in adjudication["decisions"]:
        claim_id = blind_to_source.get(decision.get("blind_claim_id"))
        label = decision.get("support_label")
        if (
            claim_id not in disputed
            or claim_id in adjudicated
            or label not in LABELS
            or not isinstance(decision.get("rationale"), str)
            or len(decision["rationale"].strip()) < 20
        ):
            raise PairedPilotError("adjudication does not match disagreement set")
        adjudicated[claim_id] = label
    if set(adjudicated) != disputed:
        raise PairedPilotError("not all disagreements were resolved")
    final = {claim: adjudicated[claim] if claim in disputed else a[claim] for claim in a}
    expected = {claim for item in coordinator["outputs"] for claim in item["claim_ids"]}
    if set(final) != expected:
        raise PairedPilotError("selected scored outputs are not completely labeled")
    return {
        "schema_version": "p5-impact-family-paired-pilot-analysis/v1",
        "status": "human_pilot_analyzed_secondary_only",
        "sampled_family_count": PILOT_FAMILY_COUNT,
        "scored_claim_count": len(final),
        "rater_exact_agreement": (len(final) - len(disputed)) / len(final),
        "disagreement_count": len(disputed),
        "primary": _estimate(coordinator, final, 0.0),
        "partial_half_sensitivity": _estimate(coordinator, final, 0.5),
        "frozen_outcomes_mutated": False,
        "provider_calls": 0,
        "interpretation": "Finite-32-family secondary measurement sensitivity; pilot variance is for planning, not a confirmatory interval or external generalization.",
    }
