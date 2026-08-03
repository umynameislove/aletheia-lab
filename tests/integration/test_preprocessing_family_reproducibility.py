"""Cross-process reproducibility for the preprocessing candidate package.

A candidate identifier, a family fingerprint or a package digest that depended on
dictionary iteration order or on interpreter start-up randomness would still look
correct inside one process. Proving independence requires separate interpreters
started with different hash seeds.

The subprocess is launched with ``sys.executable`` so the test runs under
whatever interpreter is running pytest, on any platform.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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
from aletheia_lab.benchmark.p2.identity import FamilyIdentity, PreprocessingBugParameters
from aletheia_lab.benchmark.p2.preprocessing_controls import (
    EncoderMappingRepairSpec,
    apply_encoder_mapping_repair,
)
from aletheia_lab.benchmark.p2.preprocessing_family import (
    FaultDirectedInputs,
    RepairControlInputs,
    build_preprocessing_candidate_package,
    validate_preprocessing_candidate_package,
)
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    CATEGORY_RANK_RULE,
    CATEGORY_VOCABULARY_SCHEMA_VERSION,
    INFERENCE_SOURCE_SCHEMA_VERSION,
    CategoryFrequency,
    EncoderMappingMismatchSpec,
    FrozenCategoryVocabulary,
    InferenceTransformSource,
    apply_encoder_mapping_mismatch,
)

H = {letter: letter * 64 for letter in "abcdef"}
COUNTS = {"Month-to-month": 3875, "Two year": 1695, "One year": 1473}
RANK = {1: "Month-to-month", 2: "Two year", 3: "One year"}

# Interleave the identifiers so a position-dependent implementation would
# produce a different artifact here than in a sorted source.
positions = [*range(0, 12, 2), *range(1, 12, 2)]
if os.environ.get("P2_SORTED_ROWS") == "1":
    positions = sorted(positions)
RECORD_IDS = tuple("%05d-TEST" % index for index in positions)
CATEGORIES = tuple(
    ("Month-to-month", "One year", "Two year")[index % 3] for index in positions
)
LABELS = tuple(1 if index >= 8 else 0 for index in range(12))

vocabulary = FrozenCategoryVocabulary(
    schema_version=CATEGORY_VOCABULARY_SCHEMA_VERSION,
    feature="Contract",
    split="train",
    rank_rule=CATEGORY_RANK_RULE,
    frequencies=tuple(
        CategoryFrequency(category=name, count=count) for name, count in COUNTS.items()
    ),
)
source = InferenceTransformSource(
    schema_version=INFERENCE_SOURCE_SCHEMA_VERSION,
    split="test",
    feature="Contract",
    record_ids=RECORD_IDS,
    raw_categories=CATEGORIES,
    vocabulary=vocabulary,
    attested_raw_feature_matrix_sha256=H["a"],
    attested_raw_target_sha256=H["b"],
    attested_model_sha256=H["c"],
    attested_fitted_training_transform_sha256=H["d"],
    attested_other_transform_config_sha256=H["e"],
)
test_set = CleanTestSet(
    schema_version=CLEAN_TEST_SET_SCHEMA_VERSION,
    split="test",
    record_ids=RECORD_IDS,
    attested_true_labels=LABELS,
    attested_test_feature_matrix_sha256=H["a"],
    attested_target_sha256=H["b"],
    attested_split_manifest_sha256=H["f"],
    attested_model_sha256=H["c"],
)


def parameters(source_rank, mapped_rank):
    return PreprocessingBugParameters(
        target_feature="Contract",
        source_rank=source_rank,
        mapped_rank=mapped_rank,
        mode="inference_only",
        transform_name="one_hot_encoder",
    )


def slot(slot_id, params, seed, role, intervention_type):
    return CandidateSlot(
        slot_id=slot_id,
        fault_type="preprocessing_bug",
        slot_kind="primary",
        role=role,
        identity=FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256=H["a"],
            model_data_split_manifest_sha256=H["b"],
            fault_type="preprocessing_bug",
            intervention_type=intervention_type,
            canonical_intervention_parameters=params,
            seed=seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="preprocessing/v1",
            model_specification_sha256=H["c"],
            preprocessing_specification_sha256=H["d"],
            identity_schema_version="p2-family-identity/v1",
        ),
    )


def vector(values, role):
    return PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION, role=role, predictions=values
    )


wrong = tuple(1 - label if index < 6 else label for index, label in enumerate(LABELS))

fault_parameters = parameters(3, 2)
fault_slot = slot(
    "M3-F1", fault_parameters, 301, "fault_directed", "inference_encoder_mapping_mismatch"
)
fault_spec = EncoderMappingMismatchSpec(
    injection_id="M3-F1",
    parameters=fault_parameters,
    source_category=RANK[3],
    mapped_category=RANK[2],
    seed=301,
)
fault_inputs = FaultDirectedInputs(
    source=source,
    spec=fault_spec,
    result=apply_encoder_mapping_mismatch(source=source, spec=fault_spec, slot=fault_slot),
    test_set=test_set,
    clean_reference_predictions=vector(LABELS, "reference"),
    mismatched_predictions=vector(wrong, "observed"),
)
fault_package = build_preprocessing_candidate_package(slot=fault_slot, inputs=fault_inputs)
validate_preprocessing_candidate_package(fault_package, slot=fault_slot, inputs=fault_inputs)

repair_parameters = parameters(1, 3)
repair_slot = slot(
    "M3-I1",
    repair_parameters,
    304,
    "designed_improvement_control",
    "inference_encoder_mapping_repair",
)
repair_spec = EncoderMappingRepairSpec(
    injection_id="M3-I1",
    parameters=repair_parameters,
    source_category=RANK[1],
    mapped_category=RANK[3],
    seed=304,
)
repair_inputs = RepairControlInputs(
    source=source,
    spec=repair_spec,
    result=apply_encoder_mapping_repair(source=source, spec=repair_spec, slot=repair_slot),
    test_set=test_set,
    mismatched_reference_predictions=vector(wrong, "reference"),
    repaired_predictions=vector(LABELS, "observed"),
)
repair_package = build_preprocessing_candidate_package(slot=repair_slot, inputs=repair_inputs)
validate_preprocessing_candidate_package(repair_package, slot=repair_slot, inputs=repair_inputs)

payload = {
    "fault_candidate_id": fault_package.candidate_id,
    "fault_family_sha256": fault_package.proposed_family_sha256,
    "fault_slot_sha256": fault_package.slot_sha256,
    "fault_artifact_sha256": fault_package.artifact_sha256,
    "fault_package_sha256": fault_package.artifact_package_sha256(),
    "fault_outcome": fault_package.measured_primary_outcome,
    "fault_canonical": fault_package.model_dump(mode="json"),
    "repair_candidate_id": repair_package.candidate_id,
    "repair_family_sha256": repair_package.proposed_family_sha256,
    "repair_package_sha256": repair_package.artifact_package_sha256(),
    "repair_status": repair_package.repair_measurement.status,
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


def test_identity_and_digests_agree_across_two_processes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    first = json.loads(_run("1", repo_root))
    second = json.loads(_run("999", repo_root))

    for key in (
        "fault_candidate_id",
        "fault_family_sha256",
        "fault_slot_sha256",
        "fault_artifact_sha256",
        "fault_package_sha256",
        "repair_candidate_id",
        "repair_family_sha256",
        "repair_package_sha256",
    ):
        assert first[key] == second[key], key
    assert first["fault_canonical"] == second["fault_canonical"]


def test_identity_is_independent_of_row_order_but_the_artifact_is_not() -> None:
    """Two row orderings describe one family and two different artifacts.

    Family identity is twelve frozen fields and none of them is a row ordering,
    so the fingerprint, the candidate identifier and the slot digest must agree
    across the two runs. The package digest is an integrity digest over the
    exact artifact, so it must not.
    """

    repo_root = Path(__file__).resolve().parents[2]
    interleaved = json.loads(_run("11", repo_root))
    sorted_rows = json.loads(_run("11", repo_root, sorted_rows=True))

    assert interleaved["fault_candidate_id"] == sorted_rows["fault_candidate_id"]
    assert interleaved["fault_family_sha256"] == sorted_rows["fault_family_sha256"]
    assert interleaved["fault_slot_sha256"] == sorted_rows["fault_slot_sha256"]
    assert interleaved["fault_package_sha256"] != sorted_rows["fault_package_sha256"]
    assert interleaved["fault_artifact_sha256"] != sorted_rows["fault_artifact_sha256"]


def test_the_measured_outcome_survives_the_process_boundary() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    outputs = {json.loads(_run(seed, repo_root))["fault_outcome"] for seed in ("0", "65535")}
    assert outputs == {"regression"}
