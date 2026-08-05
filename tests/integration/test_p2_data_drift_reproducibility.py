"""Cross-process reproducibility for Phase 2 native categorical drift.

Row selection that depended on dictionary iteration order or on interpreter
start-up randomness would still look correct inside one process. Proving
independence requires separate interpreters started with different hash seeds.

The subprocess is launched with ``sys.executable`` so the test runs under
whatever interpreter is running pytest, on any platform.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

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
    CategoricalDriftSpec,
    DriftEvaluationSource,
    apply_categorical_drift,
    build_drift_observed_evaluation_set,
    measure_drift_candidate,
    validate_drift_measurement,
)
from aletheia_lab.benchmark.p2.identity import DataDriftParameters, FamilyIdentity

_SCRIPT = """
import json
import os
import sys

from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.data_drift import (
    DRIFT_SOURCE_SCHEMA_VERSION,
    CategoricalDriftSpec,
    DriftEvaluationSource,
    apply_categorical_drift,
    apply_empirical_resampling_control,
    validate_categorical_drift,
)
from aletheia_lab.benchmark.p2.identity import (
    DataDriftParameters,
    FamilyIdentity,
    proposed_family_sha256,
)

H = {letter: letter * 64 for letter in "abcd"}
CATEGORIES = ("Month-to-month", "One year", "Two year")

# Interleave the identifiers so a position-dependent implementation would draw a
# different batch here than from a sorted source.
positions = [*range(0, 120, 2), *range(1, 120, 2)]
if os.environ.get("P2_SORTED_ROWS") == "1":
    positions = sorted(positions)
RECORD_IDS = tuple("%05d-EVAL" % index for index in positions)
VALUES = tuple(CATEGORIES[index % 3] for index in positions)

source = DriftEvaluationSource(
    schema_version=DRIFT_SOURCE_SCHEMA_VERSION,
    split="test",
    dataset_snapshot_id="telco_customer_churn@2026-07",
    dataset_sha256=H["a"],
    model_data_split_manifest_sha256=H["b"],
    feature="Contract",
    record_ids=RECORD_IDS,
    feature_values=VALUES,
    attested_raw_feature_matrix_sha256=H["a"],
    attested_raw_target_sha256=H["b"],
    attested_model_sha256=H["c"],
    attested_preprocessing_specification_sha256=H["d"],
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
            dataset_snapshot_id="telco_customer_churn@2026-07",
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


fault_target = {"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08}
fault_parameters = parameters(fault_target, 300)
fault_slot = slot(
    "M1-F1", fault_parameters, 1, "fault_directed", "categorical_distribution_shift"
)
fault_spec = CategoricalDriftSpec(
    injection_id="M1-F1", parameters=fault_parameters, seed=1
)
fault = apply_categorical_drift(source=source, spec=fault_spec, slot=fault_slot)
validate_categorical_drift(fault, source=source, spec=fault_spec, slot=fault_slot)

control_parameters = parameters(source.observed_distribution(), len(RECORD_IDS))
control_slot = slot(
    "M1-B1",
    control_parameters,
    105,
    "designed_benign_control",
    "empirical_distribution_resampling_control",
)
control_spec = CategoricalDriftSpec(
    injection_id="M1-B1", parameters=control_parameters, seed=105
)
control = apply_empirical_resampling_control(
    source=source, spec=control_spec, slot=control_slot
)
validate_categorical_drift(
    control, source=source, spec=control_spec, slot=control_slot
)

payload = {
    "fault_artifact_sha256": fault.artifact_sha256(),
    "fault_selected_sha256": fault.provenance.selected_record_ids_sha256,
    "fault_category_counts": [list(pair) for pair in fault.category_counts],
    "fault_achieved": [list(pair) for pair in fault.achieved_distribution],
    "fault_psi": fault.provenance.population_stability_index,
    "fault_selected": list(fault.selected_record_ids),
    "fault_family_sha256": proposed_family_sha256(fault_slot.identity),
    "control_artifact_sha256": control.artifact_sha256(),
    "control_psi": control.provenance.population_stability_index,
    "control_family_sha256": proposed_family_sha256(control_slot.identity),
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


def test_drift_batches_are_byte_identical_across_hash_seeds() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert _run("1", repo_root) == _run("999", repo_root)


def test_drift_batches_survive_three_independent_processes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    outputs = {_run(seed, repo_root) for seed in ("0", "12345", "65535")}
    assert len(outputs) == 1


def test_the_drawn_batch_does_not_depend_on_the_source_row_order() -> None:
    """Selection ranks by seeded digest, so listing order cannot change the draw.

    Three things must hold at once, and they are deliberately not the same
    thing. The drawn batch is identical, because ranking is a pure function of
    the record identifier. The family fingerprint is identical, because identity
    is twelve frozen fields and a row ordering is not one of them. The artifact
    digest *differs*, because it binds the exact source listing it was drawn
    from, and two different listings are two different artifacts.
    """

    repo_root = Path(__file__).resolve().parents[2]
    interleaved = json.loads(_run("11", repo_root))
    sorted_rows = json.loads(_run("11", repo_root, sorted_rows=True))

    assert interleaved["fault_selected"] == sorted_rows["fault_selected"]
    assert interleaved["fault_category_counts"] == sorted_rows["fault_category_counts"]
    assert interleaved["fault_achieved"] == sorted_rows["fault_achieved"]
    assert interleaved["fault_family_sha256"] == sorted_rows["fault_family_sha256"]
    assert interleaved["fault_artifact_sha256"] != sorted_rows["fault_artifact_sha256"]


def test_the_apportionment_and_psi_agree_across_processes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    first = json.loads(_run("3", repo_root))
    second = json.loads(_run("77777", repo_root))

    assert first["fault_category_counts"] == second["fault_category_counts"]
    assert first["fault_achieved"] == second["fault_achieved"]
    assert first["fault_psi"] == second["fault_psi"]
    assert first["control_psi"] == second["control_psi"] == 0.0


def test_the_two_slots_produce_different_families_and_artifacts() -> None:
    """A fault batch and its benign control are two experiments, not one."""

    repo_root = Path(__file__).resolve().parents[2]
    payload = json.loads(_run("5", repo_root))

    assert payload["fault_family_sha256"] != payload["control_family_sha256"]
    assert payload["fault_artifact_sha256"] != payload["control_artifact_sha256"]


def _frame_sha256(frame: pd.DataFrame) -> str:
    """Deterministically attest an in-memory feature matrix for this smoke test."""

    return canonical_sha256(
        {
            "columns": [str(column) for column in frame.columns],
            "rows": frame.astype(object).where(pd.notna(frame), None).values.tolist(),
        }
    )


@pytest.mark.skipif(
    not Path("data/processed/telco_customer_churn.csv").exists(),
    reason="processed Telco dataset not present (run `make data` first)",
)
def test_real_telco_drift_batch_is_scored_against_its_selected_rows() -> None:
    """End-to-end smoke with the real split, fitted model and resampled targets."""

    settings = resolve_settings("configs/project.yaml")
    frame = load_processed(settings.processed_path)
    dataset_sha256 = sha256_file(settings.processed_path)
    splits = split_dataset(
        frame,
        dataset_id=settings.dataset_id,
        dataset_sha256=dataset_sha256,
        seed=settings.seed,
        ratios=settings.ratios,
        stratified=settings.stratified,
    )
    test = splits.test
    split_sha256 = canonical_sha256(splits.manifest.model_dump(mode="json"))
    model_sha256 = canonical_sha256(asdict(settings.model))
    preprocessing_sha256 = canonical_sha256(
        {"protocol": "baseline-preprocessing/v1", "features": list(FEATURE_COLUMNS)}
    )
    feature_sha256 = _frame_sha256(test.features)
    target_sha256 = canonical_sha256(
        {
            "record_ids": [str(value) for value in test.ids],
            "labels": [int(value) for value in test.target],
        }
    )
    source = DriftEvaluationSource(
        schema_version=DRIFT_SOURCE_SCHEMA_VERSION,
        split="test",
        dataset_snapshot_id="telco_customer_churn@2026-07",
        dataset_sha256=dataset_sha256,
        model_data_split_manifest_sha256=split_sha256,
        feature="Contract",
        record_ids=tuple(str(value) for value in test.ids),
        feature_values=tuple(str(value) for value in test.features["Contract"]),
        attested_raw_feature_matrix_sha256=feature_sha256,
        attested_raw_target_sha256=target_sha256,
        attested_model_sha256=model_sha256,
        attested_preprocessing_specification_sha256=preprocessing_sha256,
    )
    parameters = DataDriftParameters(
        feature="Contract",
        target_distribution={"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
        output_size=source.record_count,
    )
    identity = FamilyIdentity(
        dataset_snapshot_id=source.dataset_snapshot_id,
        dataset_sha256=dataset_sha256,
        model_data_split_manifest_sha256=split_sha256,
        fault_type="data_drift",
        intervention_type=DRIFT_INTERVENTION_TYPE,
        canonical_intervention_parameters=parameters,
        seed=1,
        reference_construction_id="clean-test-reference/v1",
        injector_contract_version="drift/v1",
        model_specification_sha256=model_sha256,
        preprocessing_specification_sha256=preprocessing_sha256,
        identity_schema_version="p2-family-identity/v1",
    )
    slot = CandidateSlot(
        slot_id="M1-F1",
        fault_type="data_drift",
        slot_kind="primary",
        role="fault_directed",
        identity=identity,
    )
    spec = CategoricalDriftSpec(injection_id="M1-F1", parameters=parameters, seed=1)
    result = apply_categorical_drift(source=source, spec=spec, slot=slot)

    test_set = CleanTestSet(
        schema_version=CLEAN_TEST_SET_SCHEMA_VERSION,
        split="test",
        record_ids=source.record_ids,
        attested_true_labels=tuple(int(value) for value in test.target),
        attested_test_feature_matrix_sha256=feature_sha256,
        attested_target_sha256=target_sha256,
        attested_split_manifest_sha256=split_sha256,
        attested_model_sha256=model_sha256,
    )
    rows = test.features.copy()
    rows.index = [str(value) for value in test.ids]
    drifted_features = rows.loc[list(result.selected_record_ids)].reset_index(drop=True)
    observed_set = build_drift_observed_evaluation_set(
        result=result,
        source=source,
        test_set=test_set,
        attested_drifted_feature_matrix_sha256=_frame_sha256(drifted_features),
    )

    pipeline = build_pipeline(settings.model)
    pipeline.fit(splits.train.features, splits.train.target)
    clean_predictions = PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role="reference",
        predictions=tuple(int(value) for value in pipeline.predict(test.features)),
    )
    observed_predictions = PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role="observed",
        predictions=tuple(int(value) for value in pipeline.predict(drifted_features)),
    )
    measurement = measure_drift_candidate(
        result=result,
        source=source,
        spec=spec,
        slot=slot,
        test_set=test_set,
        observed_set=observed_set,
        clean_reference_predictions=clean_predictions,
        observed_predictions=observed_predictions,
    )
    validated = validate_drift_measurement(
        measurement,
        result=result,
        source=source,
        spec=spec,
        slot=slot,
        test_set=test_set,
        observed_set=observed_set,
        clean_reference_predictions=clean_predictions,
        observed_predictions=observed_predictions,
    )
    assert observed_set.true_labels == tuple(
        dict(zip(source.record_ids, test_set.attested_true_labels, strict=True))[record_id]
        for record_id in result.selected_record_ids
    )
    assert validated.comparison.reference.prediction_count == source.record_count
    assert validated.comparison.observed.prediction_count == len(result.selected_record_ids)
    assert validated.status == "validity_review_required"
