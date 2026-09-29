"""Audit the finite M5 development reader boundary, not mechanism admission.

The private predecessor summaries contain canonical hashes of projected views
and pairwise serialized-byte equality witnesses. They were produced by the
source-bound replay; this audit checks the complete paired census and derives
the finite payload-only lookup bound. It never reads sealed outcomes or sends
observations out.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from aletheia_lab.benchmark.p2.score_mapping_development import DOSES, MODEL_KINDS


class ObservationAuditError(ValueError):
    """A predecessor is incomplete or contradicts the reader contract."""


def _cells(payload: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    cells = payload.get("cells")
    if not isinstance(cells, list) or len(cells) != 2 * len(MODEL_KINDS):
        raise ObservationAuditError("the two-source development matrix is incomplete")
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ObservationAuditError("a development cell is malformed")
        source = cell.get("dataset_id")
        kind = cell.get("model_kind")
        if (
            not isinstance(source, str)
            or not isinstance(kind, str)
            or kind not in MODEL_KINDS
            or not isinstance(cell.get("development_count"), int)
            or cell["development_count"] < 2
        ):
            raise ObservationAuditError("development source or estimator identity is invalid")
        key = (source, kind)
        if key in indexed:
            raise ObservationAuditError("a development source or estimator repeats")
        indexed[key] = cell
    if len({source for source, _ in indexed}) != 2 or len(indexed) != len(cells):
        raise ObservationAuditError("development sources or estimators are missing")
    return indexed


def _measurements(cell: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    items = cell.get("measurements")
    if not isinstance(items, list) or len(items) != len(DOSES):
        raise ObservationAuditError("a development dose is missing or duplicated")
    indexed: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ObservationAuditError("a development measurement is malformed")
        dose = item.get("selected_shards")
        if type(dose) is not int or dose not in DOSES or dose in indexed:
            raise ObservationAuditError("a development dose is invalid or repeated")
        indexed[dose] = item
    return indexed


def _view_hashes(item: Mapping[str, Any], field: str) -> dict[str, str]:
    hashes = item.get(field)
    conditions = {"full", "missing_key", "noisy", "misleading"}
    if not isinstance(hashes, dict) or set(hashes) != conditions:
        raise ObservationAuditError("the four reader views are incomplete")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
        for value in hashes.values()
    ):
        raise ObservationAuditError("a reader-view hash is invalid")
    return hashes


def _canonical_lookup_accuracy(mapping_hashes: list[str], rival_hashes: list[str]) -> float:
    """Best lookup accuracy on canonical-view hashes of the finite corpus."""

    mapping_counts = Counter(mapping_hashes)
    rival_counts = Counter(rival_hashes)
    return sum(
        max(mapping_counts[key], rival_counts[key])
        for key in mapping_counts.keys() | rival_counts.keys()
    ) / (len(mapping_hashes) + len(rival_hashes))


def _check_predecessors(
    mapping: Mapping[str, Any], target: Mapping[str, Any], mapping_summary_sha256: str
) -> None:
    if (
        mapping.get("schema_version") != "score-mapping-development-symptom-study/v1"
        or mapping.get("status") != "development_candidate"
        or mapping.get("visible_metric_decimal_places") != 6
        or target.get("schema_version") != "target-binding-development/v2"
        or target.get("status") != "development_only"
        or target.get("matched_predecessor_byte_sha256") != mapping_summary_sha256
        or target.get("independently_admitted") is not False
        or any(
            payload.get("registered_attempt") is not False
            or payload.get("provider_calls") != 0
            or payload.get("sealed_predictions_or_metrics_computed") is not False
            for payload in (mapping, target)
        )
    ):
        raise ObservationAuditError("development predecessors have incompatible boundaries")


def _check_zero(source: Mapping[str, Any], bound: Mapping[str, Any]) -> None:
    if (
        source.get("status") != "zero_or_flat_control"
        or source.get("affected_rows") != 0
        or source.get("mapping_delta_log_loss") != 0
        or bound.get("changed_target_count") != 0
        or bound.get("faulty_log_loss") != bound.get("healthy_log_loss")
    ):
        raise ObservationAuditError("a zero-dose control changed its result")


def _check_positive(
    source: Mapping[str, Any],
    bound: Mapping[str, Any],
    hashes: dict[str, tuple[list[str], list[str]]],
) -> None:
    paired = bound.get("adversarial_paired_binding")
    if not isinstance(paired, dict):
        raise ObservationAuditError("an independent target-binding rival is missing")
    equality = paired.get("payload_equality")
    if (
        source.get("status") != "resolution_matched_pair"
        or source.get("source_identity_shared") is not True
        or source.get("full_witness_distinguishes") is not True
        or source.get("visible_missing_key_identical") is not True
        or source.get("twelve_decimal_missing_key_identical") is not False
        or paired.get("class_counts_preserved") is not True
        or paired.get("correction_exact") is not True
        or paired.get("selection_uses_development_mapping_metric") is not True
        or paired.get("changed_target_count") != source.get("rival_changed_target_rows")
        or paired.get("pair_count", 0) * 2 != source.get("rival_changed_target_rows")
        or not isinstance(equality, dict)
        or equality.get("6") != {"full": False, "missing_key": True}
        or equality.get("12") != {"full": False, "missing_key": False}
    ):
        raise ObservationAuditError("a paired reader or correction witness is invalid")
    mapping_views = _view_hashes(source, "mapping_view_sha256")
    rival_views = _view_hashes(source, "rival_view_sha256")
    for name, (mapping_list, rival_list) in hashes.items():
        mapping_list.append(mapping_views[name])
        rival_list.append(rival_views[name])
        if (mapping_views[name] == rival_views[name]) != (name == "missing_key"):
            raise ObservationAuditError("a paired view contradicts its visible witness")


def audit_development_observation_summaries(
    mapping: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    mapping_summary_sha256: str,
) -> dict[str, object]:
    """Reconcile four cells and quantify shortcuts on twelve constructed pairs.

    A 50% ceiling follows from the source replay's exact serialized-byte
    equality witness for every balanced pair. Distinct canonical hashes
    witness distinct payloads in the other views. This says nothing about
    new families, unpaired prevalence, the private summary as reader input,
    or twelve-decimal metrics.
    """

    _check_predecessors(mapping, target, mapping_summary_sha256)
    mapping_cells = _cells(mapping)
    target_cells = _cells(target)
    if mapping_cells.keys() != target_cells.keys():
        raise ObservationAuditError("the paired source and estimator cells differ")

    hashes: dict[str, tuple[list[str], list[str]]] = {
        name: ([], []) for name in ("full", "missing_key", "noisy", "misleading")
    }
    zero_controls = 0
    correction_checks = 0
    for key in sorted(mapping_cells):
        mapping_cell = mapping_cells[key]
        target_cell = target_cells[key]
        mapping_doses = _measurements(mapping_cell)
        target_doses = _measurements(target_cell)
        if (
            mapping_cell["development_count"] != target_cell["development_count"]
            or mapping_cell["source_target_binding_sha256"]
            != target_doses[0]["source_target_sha256"]
        ):
            raise ObservationAuditError("source scores or target lineage differ")
        # Mapping records the raw two-column score hash; target verification
        # records the calibrated two-column hash. Compare each to its own
        # zero-dose source, not to a hash of different score representations.
        calibrated_source_hash = target_doses[0].get("score_source_sha256")
        if not isinstance(calibrated_source_hash, str) or len(calibrated_source_hash) != 64:
            raise ObservationAuditError("calibrated score provenance is missing")
        for dose in DOSES:
            source = mapping_doses[dose]
            bound = target_doses[dose]
            if (
                bound.get("score_source_sha256") != calibrated_source_hash
                or bound.get("source_target_sha256") != mapping_cell["source_target_binding_sha256"]
            ):
                raise ObservationAuditError("a dose changed its pinned source lineage")
            if dose == 0:
                _check_zero(source, bound)
                zero_controls += 1
                continue
            _check_positive(source, bound, hashes)
            correction_checks += 1

    if zero_controls != 4 or correction_checks != 12:
        raise ObservationAuditError("the development controls or positive pairs are incomplete")
    ceiling = {
        name: _canonical_lookup_accuracy(mapping_hashes, rival_hashes)
        for name, (mapping_hashes, rival_hashes) in hashes.items()
    }
    # The source replay checks equal *serialized bytes* within each missing-key
    # pair, so its 0.5 bound is exact. For the other views, distinct canonical
    # hashes imply distinct serialized bytes; only a 1.0 canonical lookup may
    # be promoted to an exact byte-payload result. A canonical collision could
    # conceal a byte distinction and must not be described as an exact ceiling.
    if ceiling["missing_key"] != 0.5 or any(
        ceiling[name] != 1.0 for name in ("full", "noisy", "misleading")
    ):
        raise ObservationAuditError("the finite reader shortcut boundary is not as claimed")
    return {
        "status": "finite_development_observation_audited",
        "source_count": 2,
        "estimator_count": len(MODEL_KINDS),
        "zero_controls": zero_controls,
        "positive_matched_pairs": correction_checks,
        "reader_metric_decimal_places": 6,
        "optimal_balanced_lookup_accuracy_by_view": ceiling,
        "twelve_decimal_missing_key_pairs_distinct": correction_checks,
        "predecessors_report_no_protected_outcomes": True,
        "mechanism_admitted": False,
    }
