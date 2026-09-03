"""Materialize the measured, outcome-blind evidence census for claim evaluation.

The materializer executes only registered development interventions against the
existing Telco split and fitted baseline.  It creates no diagnosis, support
label, human judgment, main result, or sealed result.  Evaluator-only family
and condition metadata stay in :class:`ClaimEvidenceBinding`; model-visible
items contain measured facts and content identities only.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import numpy as np
import pandas as pd
from pydantic import ValidationError
from sklearn.base import clone  # type: ignore[import-untyped]
from sklearn.metrics import accuracy_score, f1_score, recall_score  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.alpha_execution import AlphaRuntime, prepare_alpha_runtime
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.data_drift import apportion, select_category_rows
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusFamily,
    ClaimCorpusFamilyInventory,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
    EvidenceCondition,
)
from aletheia_lab.evaluation.claim_evidence_census import (
    ObservedEvidenceCensus,
    build_observed_evidence_census,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimEvidenceBinding,
    ModelVisibleEvidenceItem,
    build_evidence_binding,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import (
    DiagnosisVariantFairnessFreeze,
    load_diagnosis_variant_freeze,
)
from aletheia_lab.project.identity import content_sha256

FAMILY_INVENTORY_PATH: Final = "configs/evaluation/claim_support_family_inventory.json"
REQUEST_CENSUS_PATH: Final = "configs/evaluation/claim_support_request_census.json"
FAIRNESS_FREEZE_PATH: Final = "configs/evaluation/diagnosis_variant_fairness_freeze.json"
OBSERVED_EVIDENCE_CENSUS_PATH: Final = (
    "configs/evaluation/claim_support_observed_evidence_census.json"
)
OBSERVED_EVIDENCE_RECEIPT_PATH: Final = (
    "configs/evaluation/claim_support_observed_evidence_receipt.json"
)


class ObservedEvidenceMaterializationError(ValueError):
    """Raised when measured evidence cannot be reproduced or reconciled."""


@dataclass(frozen=True)
class _MeasuredProjection:
    family: ClaimCorpusFamily
    source_projection_sha256: str
    common_items: tuple[ModelVisibleEvidenceItem, ...]
    key_item: ModelVisibleEvidenceItem
    noise_item: ModelVisibleEvidenceItem


def _file_sha256(path: Path) -> str:
    return content_sha256(path.read_bytes())


def load_observed_evidence_inputs(
    root: Path,
) -> tuple[ClaimCorpusFamilyInventory, ClaimCorpusRequestCensus, DiagnosisVariantFairnessFreeze]:
    try:
        inventory = ClaimCorpusFamilyInventory.model_validate_json(
            (root / FAMILY_INVENTORY_PATH).read_bytes()
        )
        request_census = ClaimCorpusRequestCensus.model_validate_json(
            (root / REQUEST_CENSUS_PATH).read_bytes()
        )
        freeze = load_diagnosis_variant_freeze(root / FAIRNESS_FREEZE_PATH)
    except (OSError, ValidationError, ValueError) as exc:
        raise ObservedEvidenceMaterializationError(
            "frozen observed-evidence inputs are unavailable or invalid"
        ) from exc
    if request_census.family_inventory_sha256 != inventory.inventory_sha256:
        raise ObservedEvidenceMaterializationError("request census belongs to another inventory")
    for family in inventory.families:
        source = root / family.source_artifact.path
        if not source.is_file() or source.is_symlink():
            raise ObservedEvidenceMaterializationError("registered source artifact is unavailable")
        if _file_sha256(source) != family.source_artifact.content_sha256:
            raise ObservedEvidenceMaterializationError("registered source artifact was modified")
    return inventory, request_census, freeze


def _round(value: float) -> float:
    if not math.isfinite(value):
        raise ObservedEvidenceMaterializationError("measured evidence contains a non-finite value")
    return round(float(value), 8)


def _required_number(values: Mapping[str, object], key: str) -> float:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObservedEvidenceMaterializationError(f"registered {key} is not numeric")
    return float(value)


def _required_int(values: Mapping[str, object], key: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ObservedEvidenceMaterializationError(f"registered {key} is not an integer")
    return value


def _metric_payload(y_true: Sequence[int], clean: Sequence[int], observed: Sequence[int]) -> dict[str, object]:
    return _paired_metric_payload(y_true, clean, y_true, observed)


def _paired_metric_payload(
    clean_y_true: Sequence[int],
    clean: Sequence[int],
    observed_y_true: Sequence[int],
    observed: Sequence[int],
    *,
    aligned_rows: bool = True,
) -> dict[str, object]:
    clean_true_values = np.asarray(tuple(clean_y_true), dtype=int)
    observed_true_values = np.asarray(tuple(observed_y_true), dtype=int)
    clean_values = np.asarray(tuple(clean), dtype=int)
    observed_values = np.asarray(tuple(observed), dtype=int)
    if len(clean_true_values) != len(clean_values) or len(observed_true_values) != len(observed_values):
        raise ObservedEvidenceMaterializationError("prediction vectors have different lengths")

    def metrics(true_values: np.ndarray, values: np.ndarray) -> dict[str, float]:
        return {
            "accuracy": _round(accuracy_score(true_values, values)),
            "macro_f1": _round(f1_score(true_values, values, average="macro", zero_division=0)),
            "minority_recall": _round(recall_score(true_values, values, pos_label=1, zero_division=0)),
        }

    clean_metrics = metrics(clean_true_values, clean_values)
    observed_metrics = metrics(observed_true_values, observed_values)
    return {
        "clean_row_count": int(len(clean_true_values)),
        "observed_row_count": int(len(observed_true_values)),
        "clean": clean_metrics,
        "observed": observed_metrics,
        "delta": {
            name: _round(observed_metrics[name] - clean_metrics[name])
            for name in clean_metrics
        },
        "prediction_changes_on_aligned_rows": (
            int(np.count_nonzero(clean_values != observed_values))
            if aligned_rows and len(clean_values) == len(observed_values)
            else None
        ),
    }


def _stable_rank(seed: int, family_id: str, record_id: str) -> str:
    return canonical_sha256(
        {"policy": "claim-observed-selection/v1", "seed": seed, "family": family_id, "id": record_id}
    )


def _projection_items(
    family: ClaimCorpusFamily,
    runtime: AlphaRuntime,
    *,
    key_title: str,
    key_payload: Mapping[str, object],
    metric_payload: Mapping[str, object],
    extra_projection: Mapping[str, object],
) -> _MeasuredProjection:
    source_paths = (
        family.source_artifact.path,
        "data/processed/telco_customer_churn.csv",
        "configs/project.yaml",
    )
    projection_payload = {
        "schema_version": "claim-observed-source-projection/v1",
        "family_id": family.family_id,
        "family_sha256": family.family_sha256,
        "source_artifacts": source_paths,
        "dataset_sha256": runtime.dataset_sha256,
        "split_sha256": runtime.split_sha256,
        "model_sha256": runtime.model_sha256,
        "key_measurement": dict(key_payload),
        "performance_measurement": dict(metric_payload),
        "additional_measurement": dict(extra_projection),
        "provider_calls_executed": False,
        "outcomes_generated": False,
    }
    projection_sha = canonical_execution_sha256(projection_payload)
    provenance_content = canonical_execution_json(
        {
            "model_visible_source_artifacts": (
                "data/processed/telco_customer_churn.csv",
                "configs/project.yaml",
            ),
            "dataset_sha256": runtime.dataset_sha256,
            "split_sha256": runtime.split_sha256,
            "model_sha256": runtime.model_sha256,
            "projection_sha256": projection_sha,
        }
    )
    performance_content = canonical_execution_json(metric_payload)
    key_content = canonical_execution_json(key_payload)
    noise_content = canonical_execution_json(extra_projection)
    provenance = build_visible_evidence_item(
        evidence_id="ev-source-provenance",
        kind="lineage",
        title="Observed source and execution provenance",
        content=provenance_content,
        source_content_sha256=projection_sha,
    )
    performance = build_visible_evidence_item(
        evidence_id="ev-performance-summary",
        kind="metric",
        title="Measured prediction performance",
        content=performance_content,
        source_content_sha256=projection_sha,
    )
    key_item = build_visible_evidence_item(
        evidence_id="ev-key-measurement",
        kind="metric",
        title=key_title,
        content=key_content,
        source_content_sha256=projection_sha,
    )
    noise_item = build_visible_evidence_item(
        evidence_id="ev-secondary-observation",
        kind="dataset_profile",
        title="Measured secondary structural observation",
        content=noise_content,
        source_content_sha256=projection_sha,
    )
    return _MeasuredProjection(
        family=family,
        source_projection_sha256=projection_sha,
        common_items=(performance, provenance),
        key_item=key_item,
        noise_item=noise_item,
    )


def _drift_projection(family: ClaimCorpusFamily, runtime: AlphaRuntime) -> _MeasuredProjection:
    feature = str(family.intervention_parameters["feature"])
    dominant = _required_number(family.intervention_parameters, "month_to_month")
    categories = tuple(sorted(str(value) for value in runtime.splits.test.features[feature].unique()))
    if categories != ("Month-to-month", "One year", "Two year"):
        raise ObservedEvidenceMaterializationError("Contract categories differ from the registered source")
    target = {
        "Month-to-month": dominant,
        "One year": (1.0 - dominant) / 2.0,
        "Two year": (1.0 - dominant) / 2.0,
    }
    counts = apportion(target_distribution=target, output_size=len(runtime.splits.test.features))
    identifiers = tuple(str(value) for value in runtime.splits.test.ids)
    values = tuple(str(value) for value in runtime.splits.test.features[feature])
    pools = {
        category: tuple(record_id for record_id, value in zip(identifiers, values, strict=True) if value == category)
        for category in categories
    }
    selected = tuple(
        record_id
        for category in categories
        for record_id in select_category_rows(
            pool_record_ids=pools[category],
            count=counts[category],
            seed=family.seed,
            injection_id=family.family_id,
            category=category,
        )
    )
    position = {record_id: index for index, record_id in enumerate(identifiers)}
    selected_positions = [position[record_id] for record_id in selected]
    frame = runtime.splits.test.features.iloc[selected_positions].reset_index(drop=True)
    targets = tuple(int(runtime.splits.test.target.iloc[index]) for index in selected_positions)
    clean_for_observed = tuple(int(value) for value in runtime.clean_pipeline.predict(frame))
    observed = clean_for_observed
    reference_counts = Counter(values)
    observed_counts = Counter(str(value) for value in frame[feature])
    n = len(values)
    key = {
        "feature": feature,
        "reference_shares": {name: _round(reference_counts[name] / n) for name in categories},
        "observed_shares": {name: _round(observed_counts[name] / n) for name in categories},
        "total_variation": _round(
            0.5 * sum(abs(reference_counts[name] - observed_counts[name]) / n for name in categories)
        ),
        "observed_row_count": n,
    }
    metrics = _paired_metric_payload(
        tuple(int(value) for value in runtime.splits.test.target),
        runtime.clean_predictions,
        targets,
        observed,
        aligned_rows=False,
    )
    extra = {
        "feature_column_count": int(frame.shape[1]),
        "row_count": int(frame.shape[0]),
        "schema_unchanged": tuple(frame.columns) == tuple(runtime.splits.test.features.columns),
        "selected_membership_sha256": canonical_sha256({"record_ids": list(selected)}),
    }
    return _projection_items(
        family,
        runtime,
        key_title="Observed categorical distribution comparison",
        key_payload=key,
        metric_payload=metrics,
        extra_projection=extra,
    )


def _label_projection(family: ClaimCorpusFamily, runtime: AlphaRuntime) -> _MeasuredProjection:
    labels = np.asarray(tuple(int(value) for value in runtime.splits.train.target), dtype=int)
    identifiers = tuple(str(value) for value in runtime.splits.train.ids)
    rate = _required_number(family.intervention_parameters, "rate")
    noise = str(family.intervention_parameters["noise"])
    count = int(round(rate * len(labels)))
    if noise == "class_conditional":
        source_label = _required_int(family.intervention_parameters, "from")
        candidates = [index for index, value in enumerate(labels) if value == source_label]
        count = int(round(rate * len(candidates)))
        selected = sorted(
            candidates,
            key=lambda index: _stable_rank(family.seed, family.family_id, identifiers[index]),
        )[:count]
    elif noise == "boundary_targeted":
        probabilities = np.asarray(runtime.clean_pipeline.predict_proba(runtime.splits.train.features))[:, 1]
        selected = sorted(
            range(len(labels)),
            key=lambda index: (
                abs(float(probabilities[index]) - 0.5),
                _stable_rank(family.seed, family.family_id, identifiers[index]),
            ),
        )[:count]
    elif noise == "symmetric":
        selected = sorted(
            range(len(labels)),
            key=lambda index: _stable_rank(family.seed, family.family_id, identifiers[index]),
        )[:count]
    else:
        raise ObservedEvidenceMaterializationError("unsupported registered target mutation")
    mutated = labels.copy()
    mutated[selected] = 1 - mutated[selected]
    model = cast(Pipeline, clone(runtime.clean_pipeline))
    model.fit(runtime.splits.train.features, pd.Series(mutated))
    observed_predictions = tuple(int(value) for value in model.predict(runtime.splits.test.features))
    key = {
        "training_row_count": int(len(labels)),
        "changed_target_count": int(np.count_nonzero(labels != mutated)),
        "changed_target_rate": _round(np.count_nonzero(labels != mutated) / len(labels)),
        "reference_positive_count": int(labels.sum()),
        "observed_positive_count": int(mutated.sum()),
        "reference_target_sha256": canonical_sha256({"ids": identifiers, "targets": labels.tolist()}),
        "observed_target_sha256": canonical_sha256({"ids": identifiers, "targets": mutated.tolist()}),
    }
    metrics = _metric_payload(
        tuple(int(value) for value in runtime.splits.test.target),
        runtime.clean_predictions,
        observed_predictions,
    )
    extra = {
        "training_feature_row_count": int(runtime.splits.train.features.shape[0]),
        "test_feature_row_count": int(runtime.splits.test.features.shape[0]),
        "feature_column_count": int(runtime.splits.train.features.shape[1]),
        "test_partition_sha256": runtime.test_feature_sha256,
    }
    return _projection_items(
        family,
        runtime,
        key_title="Observed training-target comparison",
        key_payload=key,
        metric_payload=metrics,
        extra_projection=extra,
    )


def _preprocessing_projection(family: ClaimCorpusFamily, runtime: AlphaRuntime) -> _MeasuredProjection:
    preprocessor = runtime.clean_pipeline.named_steps["preprocess"]
    classifier = runtime.clean_pipeline.named_steps["model"]
    frame = runtime.splits.test.features
    clean = np.asarray(preprocessor.transform(frame), dtype=float)
    observed = clean.copy()
    change = str(family.intervention_parameters["change"])
    numeric_pipeline = preprocessor.named_transformers_["numeric"]
    numeric_columns = tuple(preprocessor.transformers_[0][2])
    imputer = numeric_pipeline.named_steps["impute"]
    scaler = numeric_pipeline.named_steps["scale"]
    imputed = np.asarray(imputer.transform(frame[list(numeric_columns)]), dtype=float)
    if change == "with_mean_false":
        observed[:, : len(numeric_columns)] = imputed / np.asarray(scaler.scale_)
    elif change == "with_std_false":
        observed[:, : len(numeric_columns)] = imputed - np.asarray(scaler.mean_)
    elif change == "category_order_permuted":
        names = tuple(str(value) for value in preprocessor.get_feature_names_out())
        first = names.index("Contract_Month-to-month")
        second = names.index("Contract_One year")
        observed[:, [first, second]] = observed[:, [second, first]]
    elif change == "median_to_mean":
        raw = frame[list(numeric_columns)].to_numpy(dtype=float)
        means = runtime.splits.train.features[list(numeric_columns)].mean().to_numpy(dtype=float)
        alternative = raw.copy()
        missing = np.isnan(alternative)
        alternative[missing] = np.take(means, np.where(missing)[1])
        observed[:, : len(numeric_columns)] = (alternative - np.asarray(scaler.mean_)) / np.asarray(
            scaler.scale_
        )
    elif change == "numeric_order_swapped":
        observed[:, [0, 1]] = observed[:, [1, 0]]
    else:
        raise ObservedEvidenceMaterializationError("unsupported registered feature transformation")
    clean_predictions = tuple(int(value) for value in classifier.predict(clean))
    observed_predictions = tuple(int(value) for value in classifier.predict(observed))
    difference = np.abs(observed - clean)
    key = {
        "input_row_count": int(clean.shape[0]),
        "transformed_column_count": int(clean.shape[1]),
        "changed_row_count": int(np.count_nonzero(np.any(difference > 0.0, axis=1))),
        "changed_cell_count": int(np.count_nonzero(difference > 0.0)),
        "maximum_absolute_difference": _round(float(difference.max(initial=0.0))),
        "reference_transform_sha256": canonical_sha256(clean.tolist()),
        "observed_transform_sha256": canonical_sha256(observed.tolist()),
    }
    metrics = _metric_payload(
        tuple(int(value) for value in runtime.splits.test.target),
        clean_predictions,
        observed_predictions,
    )
    extra = {
        "input_missing_value_count": int(frame.isna().sum().sum()),
        "input_feature_column_count": int(frame.shape[1]),
        "output_column_count_unchanged": clean.shape[1] == observed.shape[1],
        "test_partition_sha256": runtime.test_feature_sha256,
    }
    return _projection_items(
        family,
        runtime,
        key_title="Observed feature-transformation comparison",
        key_payload=key,
        metric_payload=metrics,
        extra_projection=extra,
    )


def _projection_for(family: ClaimCorpusFamily, runtime: AlphaRuntime) -> _MeasuredProjection:
    if family.mechanism == "data_drift":
        return _drift_projection(family, runtime)
    if family.mechanism == "preprocessing_mismatch":
        return _preprocessing_projection(family, runtime)
    return _label_projection(family, runtime)


def _representative_requests(census: ClaimCorpusRequestCensus) -> dict[tuple[str, EvidenceCondition], ClaimCorpusRequest]:
    result: dict[tuple[str, EvidenceCondition], ClaimCorpusRequest] = {}
    for request in census.primary_requests:
        result.setdefault((request.family_id, request.evidence_condition), request)
    if len(result) != 45:
        raise ObservedEvidenceMaterializationError("request census does not contain 45 contexts")
    return result


def _bindings_for(
    projection: _MeasuredProjection,
    requests: Mapping[tuple[str, EvidenceCondition], ClaimCorpusRequest],
) -> tuple[ClaimEvidenceBinding, ...]:
    full_items = (*projection.common_items, projection.key_item)
    missing_items = projection.common_items
    noisy_items = (*full_items, projection.noise_item)
    by_condition: dict[EvidenceCondition, tuple[ModelVisibleEvidenceItem, ...]] = {
        "full": full_items,
        "missing_key": missing_items,
        "noisy": noisy_items,
    }
    return tuple(
        build_evidence_binding(
            requests[(projection.family.family_id, condition)],
            items=by_condition[condition],
            source_projection_sha256=projection.source_projection_sha256,
        )
        for condition in ("full", "missing_key", "noisy")
    )


def _validate_family_conditions(
    family: str,
    conditions: Mapping[EvidenceCondition, ClaimEvidenceBinding],
) -> tuple[str, str, str]:
    if set(conditions) != {"full", "missing_key", "noisy"}:
        raise ClaimCorpusContractError(f"family {family} does not have all three conditions")
    full = conditions["full"]
    missing = conditions["missing_key"]
    noisy = conditions["noisy"]
    if len({item.source_projection_sha256 for item in conditions.values()}) != 1:
        raise ClaimCorpusContractError("condition variants do not share one measured projection")
    full_by_id = {item.evidence_id: item for item in full.visible_context.items}
    missing_by_id = {item.evidence_id: item for item in missing.visible_context.items}
    noisy_by_id = {item.evidence_id: item for item in noisy.visible_context.items}
    expected_missing = set(full_by_id) - {"ev-key-measurement"}
    if "ev-key-measurement" not in full_by_id or "ev-key-measurement" in missing_by_id:
        raise ClaimCorpusContractError("missing-key condition did not remove the decisive item")
    if set(missing_by_id) != expected_missing or any(
        missing_by_id[key] != full_by_id[key] for key in expected_missing
    ):
        raise ClaimCorpusContractError("missing-key condition changed non-key evidence")
    if set(noisy_by_id) != set(full_by_id) | {"ev-secondary-observation"} or any(
        noisy_by_id[key] != full_by_id[key] for key in full_by_id
    ):
        raise ClaimCorpusContractError("noisy condition is not full evidence plus one observation")
    visible_text = canonical_execution_json(
        tuple(item.model_payload() for item in conditions.values())
    ).casefold()
    forbidden = (
        family.casefold(),
        "data_drift",
        "data-drift",
        "preprocessing_mismatch",
        "preprocessing-mismatch",
        "label_noise",
        "label-noise",
        "placeholder",
        "synthetic_fixture",
        "hidden_ground_truth",
    )
    if any(fragment in visible_text for fragment in forbidden):
        raise ClaimCorpusContractError("model-visible evidence exposes hidden metadata")
    return (
        full.visible_context.context_sha256,
        missing.visible_context.context_sha256,
        noisy.visible_context.context_sha256,
    )


def validate_condition_semantics(census: ObservedEvidenceCensus) -> ObservedEvidenceCensus:
    """Prove missing-key and noisy are exact transformations of measured full evidence."""

    checked = ObservedEvidenceCensus.model_validate(census.model_dump(mode="python"))
    groups: dict[str, dict[EvidenceCondition, ClaimEvidenceBinding]] = {}
    for binding in checked.bindings:
        groups.setdefault(binding.family_id, {})[binding.evidence_condition] = binding
    if len(groups) != 15:
        raise ClaimCorpusContractError("observed evidence must contain 15 primary families")
    context_hashes = tuple(
        context_hash
        for family, conditions in groups.items()
        for context_hash in _validate_family_conditions(family, conditions)
    )
    if len(context_hashes) != len(set(context_hashes)):
        raise ClaimCorpusContractError("observed evidence contains duplicate visible contexts")
    return checked


def materialize_observed_evidence(root: Path) -> ObservedEvidenceCensus:
    """Execute the 15 development projections and build exactly 45 contexts."""

    checked_root = root.resolve()
    inventory, requests, _ = load_observed_evidence_inputs(checked_root)
    runtime = prepare_alpha_runtime(checked_root / "configs/project.yaml")
    representatives = _representative_requests(requests)
    bindings: list[ClaimEvidenceBinding] = []
    for family in inventory.families:
        if family.role == "primary":
            bindings.extend(_bindings_for(_projection_for(family, runtime), representatives))
    observed = build_observed_evidence_census(requests, bindings)
    return validate_condition_semantics(observed)


__all__ = [
    "OBSERVED_EVIDENCE_CENSUS_PATH",
    "OBSERVED_EVIDENCE_RECEIPT_PATH",
    "ObservedEvidenceMaterializationError",
    "load_observed_evidence_inputs",
    "materialize_observed_evidence",
    "validate_condition_semantics",
]
