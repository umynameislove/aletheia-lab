"""Cross-process reproducibility and real-data smoke for the drift package.

A candidate identifier, a family fingerprint or a package digest that depended on
dictionary iteration order or on interpreter start-up randomness would still look
correct inside one process. Proving independence requires separate interpreters
started with different hash seeds.

The real-data smoke builds and validates a package from the project's own
processed Telco split and its own fitted baseline pipeline. It writes nothing:
every digest it needs is computed in memory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from aletheia_lab.baseline.artifacts import sha256_file
from aletheia_lab.baseline.loader import load_processed, split_dataset
from aletheia_lab.baseline.model import build_pipeline
from aletheia_lab.baseline.run import resolve_settings
from aletheia_lab.baseline.schema import FEATURE_COLUMNS
from aletheia_lab.benchmark.p2.binary_evaluation import (
    CLEAN_TEST_SET_SCHEMA_VERSION,
    PREDICTION_VECTOR_SCHEMA_VERSION,
    CleanTestSet,
    PredictionVector,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.data_drift import (
    DRIFT_INTERVENTION_TYPE,
    DRIFT_SOURCE_SCHEMA_VERSION,
    RESAMPLING_CONTROL_INTERVENTION_TYPE,
    CategoricalDriftSpec,
    DriftEvaluationSource,
    apply_categorical_drift,
    apply_empirical_resampling_control,
    build_drift_observed_evaluation_set,
)
from aletheia_lab.benchmark.p2.data_drift_family import (
    DriftBenignControlInputs,
    DriftFaultDirectedInputs,
    DriftPredictionEvidence,
    build_drift_candidate_package,
    build_drift_prediction_run,
    validate_drift_candidate_package,
)
from aletheia_lab.benchmark.p2.identity import DataDriftParameters, FamilyIdentity

_SCRIPT = """
import json
import os
import sys

from aletheia_lab.benchmark.p2.binary_evaluation import (
    CLEAN_TEST_SET_SCHEMA_VERSION,
    PREDICTION_VECTOR_SCHEMA_VERSION,
    CleanTestSet,
    PredictionVector,
)
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.data_drift import (
    DRIFT_SOURCE_SCHEMA_VERSION,
    CategoricalDriftSpec,
    DriftEvaluationSource,
    apply_categorical_drift,
    apply_empirical_resampling_control,
    build_drift_observed_evaluation_set,
)
from aletheia_lab.benchmark.p2.data_drift_family import (
    DriftBenignControlInputs,
    DriftFaultDirectedInputs,
    DriftPredictionEvidence,
    build_drift_candidate_package,
    build_drift_prediction_run,
    validate_drift_candidate_package,
)
from aletheia_lab.benchmark.p2.identity import DataDriftParameters, FamilyIdentity

H = {letter: letter * 64 for letter in "abcdef"}
SNAPSHOT = "telco_customer_churn@2026-07"
CATEGORIES = ("Month-to-month", "One year", "Two year")

positions = [*range(0, 120, 2), *range(1, 120, 2)]
if os.environ.get("P2_SORTED_ROWS") == "1":
    positions = sorted(positions)
RECORD_IDS = tuple("%05d-EVAL" % index for index in positions)
VALUES = tuple(CATEGORIES[index % 3] for index in positions)
LABELS = tuple(1 if index >= 80 else 0 for index in range(120))

source = DriftEvaluationSource(
    schema_version=DRIFT_SOURCE_SCHEMA_VERSION,
    split="test",
    dataset_snapshot_id=SNAPSHOT,
    dataset_sha256=H["a"],
    model_data_split_manifest_sha256=H["b"],
    feature="Contract",
    record_ids=RECORD_IDS,
    feature_values=VALUES,
    attested_raw_feature_matrix_sha256=H["e"],
    attested_raw_target_sha256=H["f"],
    attested_model_sha256=H["c"],
    attested_preprocessing_specification_sha256=H["d"],
)
test_set = CleanTestSet(
    schema_version=CLEAN_TEST_SET_SCHEMA_VERSION,
    split="test",
    record_ids=RECORD_IDS,
    attested_true_labels=LABELS,
    attested_test_feature_matrix_sha256=H["e"],
    attested_target_sha256=H["f"],
    attested_split_manifest_sha256=H["b"],
    attested_model_sha256=H["c"],
)


def parameters(target, output_size):
    return DataDriftParameters(
        feature="Contract", target_distribution=target, output_size=output_size
    )


def slot(slot_id, params, seed, role, intervention_type):
    return CandidateSlot(
        slot_id=slot_id,
        fault_type="data_drift",
        slot_kind="primary",
        role=role,
        identity=FamilyIdentity(
            dataset_snapshot_id=SNAPSHOT,
            dataset_sha256=H["a"],
            model_data_split_manifest_sha256=H["b"],
            fault_type="data_drift",
            intervention_type=intervention_type,
            canonical_intervention_parameters=params,
            seed=seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="drift/v1",
            model_specification_sha256=H["c"],
            preprocessing_specification_sha256=H["d"],
            identity_schema_version="p2-family-identity/v1",
        ),
    )


def vector(values, role):
    return PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role=role,
        predictions=tuple(int(value) for value in values),
    )


def package_for(slot_id, target, size, seed, role, intervention_type, bundle, apply):
    params = parameters(target, size)
    frozen = slot(slot_id, params, seed, role, intervention_type)
    spec = CategoricalDriftSpec(injection_id=slot_id, parameters=params, seed=seed)
    result = apply(source=source, spec=spec, slot=frozen)
    observed_set = build_drift_observed_evaluation_set(
        result=result,
        source=source,
        test_set=test_set,
        attested_drifted_feature_matrix_sha256=H["a"],
    )
    inputs = bundle(
        source=source,
        spec=spec,
        result=result,
        test_set=test_set,
        observed_set=observed_set,
        predictions=DriftPredictionEvidence(
            reference_run=build_drift_prediction_run(
                role="reference",
                model_specification_sha256=H["c"],
                evaluation_source_sha256=test_set.artifact_sha256(),
                predictions=vector(LABELS, "reference"),
            ),
            observed_run=build_drift_prediction_run(
                role="observed",
                model_specification_sha256=H["c"],
                evaluation_source_sha256=observed_set.artifact_sha256(),
                predictions=vector(observed_set.true_labels, "observed"),
            ),
        ),
    )
    package = build_drift_candidate_package(slot=frozen, inputs=inputs)
    validate_drift_candidate_package(package, slot=frozen, inputs=inputs)
    return package


fault = package_for(
    "M1-F1",
    {"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
    300,
    1,
    "fault_directed",
    "categorical_distribution_shift",
    DriftFaultDirectedInputs,
    apply_categorical_drift,
)
control = package_for(
    "M1-B1",
    source.observed_distribution(),
    120,
    105,
    "designed_benign_control",
    "empirical_distribution_resampling_control",
    DriftBenignControlInputs,
    apply_empirical_resampling_control,
)

payload = {
    "fault_candidate_id": fault.candidate_id,
    "fault_family_id": fault.family_id,
    "fault_family_sha256": fault.proposed_family_sha256,
    "fault_package_sha256": fault.artifact_package_sha256(),
    "fault_status": fault.status,
    "fault_psi": fault.measurement.population_stability_index,
    "control_candidate_id": control.candidate_id,
    "control_package_sha256": control.artifact_package_sha256(),
    "control_status": control.status,
}
sys.stdout.write(json.dumps(payload, sort_keys=True))
"""


def _run(seed: str, repo_root: Path, *, sorted_rows: bool = False) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["P2_SORTED_ROWS"] = "1" if sorted_rows else "0"
    env["PYTHONPATH"] = str(repo_root / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        check=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    return completed.stdout


def test_packages_are_byte_identical_across_hash_seeds() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert _run("1", repo_root) == _run("999", repo_root)


def test_packages_survive_three_independent_processes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    outputs = {_run(seed, repo_root) for seed in ("0", "12345", "65535")}
    assert len(outputs) == 1


def test_identity_is_independent_of_source_row_order() -> None:
    """Identity is twelve frozen fields; a row ordering is not one of them.

    The package digest binds the exact source listing, so it must move when the
    listing does. Both claims are asserted together so neither can quietly
    become the other.
    """

    repo_root = Path(__file__).resolve().parents[2]
    interleaved = json.loads(_run("11", repo_root))
    sorted_rows = json.loads(_run("11", repo_root, sorted_rows=True))

    assert interleaved["fault_candidate_id"] == sorted_rows["fault_candidate_id"]
    assert interleaved["fault_family_id"] == sorted_rows["fault_family_id"]
    assert interleaved["fault_family_sha256"] == sorted_rows["fault_family_sha256"]
    assert interleaved["fault_package_sha256"] != sorted_rows["fault_package_sha256"]


def test_the_two_roles_reach_their_own_terminal_status() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    payload = json.loads(_run("5", repo_root))

    assert payload["fault_status"] == "validity_review_required"
    assert payload["control_status"] == "equivalence_verified_pending_admission"
    assert payload["fault_psi"] > 0.9
    assert payload["fault_candidate_id"] != payload["control_candidate_id"]


# --------------------------------------------------------------------------- #
# Real-data smoke
# --------------------------------------------------------------------------- #

_PROCESSED = Path("data/processed/telco_customer_churn.csv")


def _frame_sha256(frame: pd.DataFrame) -> str:
    """Attest the complete ordered feature matrix used by this smoke test."""

    return canonical_sha256(
        {
            "columns": [str(column) for column in frame.columns],
            "rows": frame.astype(object).where(pd.notna(frame), None).values.tolist(),
        }
    )


@pytest.mark.skipif(not _PROCESSED.exists(), reason="processed Telco split is not present")
def test_a_real_telco_package_builds_and_validates() -> None:
    """Build a package from the real split and the project's own fitted pipeline."""

    settings = resolve_settings("configs/project.yaml")
    frame = load_processed(settings.processed_path)
    dataset_digest = sha256_file(settings.processed_path)
    splits = split_dataset(
        frame,
        dataset_id=settings.dataset_id,
        dataset_sha256=dataset_digest,
        seed=settings.seed,
        ratios=settings.ratios,
        stratified=settings.stratified,
    )
    test = splits.test
    record_ids = tuple(str(value) for value in test.ids)
    labels = tuple(int(value) for value in test.target)
    split_digest = canonical_sha256(splits.manifest.model_dump(mode="json"))
    model_digest = canonical_sha256(asdict(settings.model))
    preprocessing_digest = canonical_sha256(
        {"protocol": "baseline-preprocessing/v1", "features": list(FEATURE_COLUMNS)}
    )
    feature_digest = _frame_sha256(test.features)
    target_digest = canonical_sha256({"record_ids": list(record_ids), "labels": list(labels)})

    source = DriftEvaluationSource(
        schema_version=DRIFT_SOURCE_SCHEMA_VERSION,
        split="test",
        dataset_snapshot_id="telco_customer_churn@2026-07",
        dataset_sha256=dataset_digest,
        model_data_split_manifest_sha256=split_digest,
        feature="Contract",
        record_ids=record_ids,
        feature_values=tuple(str(value) for value in test.features["Contract"]),
        attested_raw_feature_matrix_sha256=feature_digest,
        attested_raw_target_sha256=target_digest,
        attested_model_sha256=model_digest,
        attested_preprocessing_specification_sha256=preprocessing_digest,
    )
    test_set = CleanTestSet(
        schema_version=CLEAN_TEST_SET_SCHEMA_VERSION,
        split="test",
        record_ids=record_ids,
        attested_true_labels=labels,
        attested_test_feature_matrix_sha256=feature_digest,
        attested_target_sha256=target_digest,
        attested_split_manifest_sha256=split_digest,
        attested_model_sha256=model_digest,
    )

    def identity(params: DataDriftParameters, seed: int, intervention: str) -> FamilyIdentity:
        return FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256=dataset_digest,
            model_data_split_manifest_sha256=split_digest,
            fault_type="data_drift",
            intervention_type=intervention,
            canonical_intervention_parameters=params,
            seed=seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="drift/v1",
            model_specification_sha256=model_digest,
            preprocessing_specification_sha256=preprocessing_digest,
            identity_schema_version="p2-family-identity/v1",
        )

    pipeline = build_pipeline(settings.model)
    pipeline.fit(splits.train.features, splits.train.target)
    indexed_test_features = test.features.copy()
    indexed_test_features.index = list(record_ids)

    def selected_features(record_id_batch: tuple[str, ...]) -> pd.DataFrame:
        return indexed_test_features.loc[list(record_id_batch)].reset_index(drop=True)

    def predict(record_id_batch: tuple[str, ...]) -> tuple[int, ...]:
        position = {record_id: index for index, record_id in enumerate(record_ids)}
        if len(position) != len(record_ids):
            raise AssertionError("the canonical test split must have unique record IDs")
        rows = selected_features(record_id_batch)
        return tuple(int(value) for value in pipeline.predict(rows))

    clean_predictions = predict(record_ids)

    def build(
        slot_id: str,
        target_distribution: dict[str, float],
        size: int,
        seed: int,
        role: str,
        intervention: str,
        bundle: Any,
        apply: Any,
    ) -> Any:
        params = DataDriftParameters(
            feature="Contract", target_distribution=target_distribution, output_size=size
        )
        slot = CandidateSlot(
            slot_id=slot_id,
            fault_type="data_drift",
            slot_kind="primary",
            role=role,  # type: ignore[arg-type]
            identity=identity(params, seed, intervention),
        )
        spec = CategoricalDriftSpec(injection_id=slot_id, parameters=params, seed=seed)
        result = apply(source=source, spec=spec, slot=slot)
        observed_set = build_drift_observed_evaluation_set(
            result=result,
            source=source,
            test_set=test_set,
            attested_drifted_feature_matrix_sha256=_frame_sha256(
                selected_features(result.selected_record_ids)
            ),
        )
        reference_vector = PredictionVector(
            schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
            role="reference",
            predictions=clean_predictions,
        )
        observed_vector = PredictionVector(
            schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
            role="observed",
            predictions=predict(result.selected_record_ids),
        )
        inputs = bundle(
            source=source,
            spec=spec,
            result=result,
            test_set=test_set,
            observed_set=observed_set,
            predictions=DriftPredictionEvidence(
                reference_run=build_drift_prediction_run(
                    role="reference",
                    model_specification_sha256=model_digest,
                    evaluation_source_sha256=test_set.artifact_sha256(),
                    predictions=reference_vector,
                ),
                observed_run=build_drift_prediction_run(
                    role="observed",
                    model_specification_sha256=model_digest,
                    evaluation_source_sha256=observed_set.artifact_sha256(),
                    predictions=observed_vector,
                ),
            ),
        )
        package = build_drift_candidate_package(slot=slot, inputs=inputs)
        validate_drift_candidate_package(package, slot=slot, inputs=inputs)
        return package

    fault = build(
        "M1-F1",
        {"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
        len(record_ids),
        1,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
        DriftFaultDirectedInputs,
        apply_categorical_drift,
    )
    assert fault.status == "validity_review_required"
    assert fault.measurement.population_stability_index > 0.1
    assert fault.family_id.startswith("p2-family-")
    assert "p1-" not in fault.model_dump_json().lower()

    control = build(
        "M1-B1",
        source.observed_distribution(),
        len(record_ids),
        105,
        "designed_benign_control",
        RESAMPLING_CONTROL_INTERVENTION_TYPE,
        DriftBenignControlInputs,
        apply_empirical_resampling_control,
    )
    assert control.measurement.distribution_total_variation == 0.0
    assert control.measurement.population_stability_index == 0.0
    assert control.measurement.comparison.accuracy_delta == 0.0
    assert control.measurement.comparison.macro_f1_delta == 0.0
    assert control.measurement.comparison.minority_recall_delta == 0.0
    assert control.status == "equivalence_verified_pending_admission"
    assert fault.proposed_family_sha256 != control.proposed_family_sha256
