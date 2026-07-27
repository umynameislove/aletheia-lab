"""Cross-process reproducibility for the two predeclared label controls.

A repair or a round trip that depended on dictionary iteration order or on
interpreter start-up randomness would still look correct inside one process.
Proving independence requires separate interpreters started with different hash
seeds.
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

from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import FamilyIdentity, LabelNoiseParameters
from aletheia_lab.benchmark.p2.label_controls import (
    SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION,
    SemanticTargetSource,
    SerializationControlSpec,
    apply_label_repair,
    apply_serialization_roundtrip,
    codec_sha256,
    validate_label_repair,
    validate_serialization_roundtrip,
)
from aletheia_lab.benchmark.p2.label_noise import (
    LABEL_SOURCE_SCHEMA_VERSION,
    LabelCorruptionSpec,
    LabelNoiseSource,
    apply_label_corruption,
)

def control_slot(kind):
    repair = kind == "repair"
    seed = 204 if repair else 205
    rate = 0.20 if repair else 0.0
    identity = FamilyIdentity(
        dataset_snapshot_id="telco_customer_churn@2026-07",
        dataset_sha256="a" * 64,
        model_data_split_manifest_sha256="b" * 64,
        fault_type="label_noise",
        intervention_type=(
            "training_target_label_repair"
            if repair else "target_label_serialization_roundtrip"
        ),
        canonical_intervention_parameters=LabelNoiseParameters(
            flip_rate=rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
        seed=seed,
        reference_construction_id="clean-test-reference/v1",
        injector_contract_version="label-control/v1",
        model_specification_sha256="c" * 64,
        preprocessing_specification_sha256="d" * 64,
        identity_schema_version="p2-family-identity/v1",
    )
    return CandidateSlot(
        slot_id="M2-I1" if repair else "M2-B1",
        fault_type="label_noise",
        slot_kind="primary",
        role=(
            "designed_improvement_control"
            if repair else "designed_benign_control"
        ),
        identity=identity,
    )

# Interleave the identifiers so a position-dependent implementation would
# produce a different selection here than in a sorted source.
pairs = [
    (f"{record_id:05d}-SYNTH", position % 2)
    for position, record_id in enumerate([*range(0, 400, 2), *range(1, 400, 2)])
]
record_ids = tuple(record_id for record_id, _ in pairs)
targets = tuple(target for _, target in pairs)
semantic = tuple("Yes" if target else "No" for target in targets)

clean = LabelNoiseSource(
    schema_version=LABEL_SOURCE_SCHEMA_VERSION,
    split="train",
    record_ids=record_ids,
    targets=targets,
    attested_feature_matrix_sha256="f" * 64,
    attested_preprocessing_specification_sha256="d" * 64,
    attested_model_specification_sha256="c" * 64,
)
corruption_spec = LabelCorruptionSpec(
    parameters=LabelNoiseParameters(
        flip_rate=0.20,
        flip_direction="symmetric",
        selection_policy="seeded_record_hash",
        scope="train",
    ),
    seed=204,
)
corrupted = apply_label_corruption(source=clean, spec=corruption_spec)
repair_slot = control_slot("repair")
repaired = apply_label_repair(
    source=clean, corrupted=corrupted, spec=corruption_spec, slot=repair_slot
)
validate_label_repair(
    repaired, source=clean, corrupted=corrupted, spec=corruption_spec, slot=repair_slot
)

semantic_source = SemanticTargetSource(
    schema_version=SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION,
    split="train",
    record_ids=record_ids,
    targets=semantic,
    attested_feature_matrix_sha256="f" * 64,
    attested_preprocessing_specification_sha256="d" * 64,
    attested_model_specification_sha256="c" * 64,
)
serialization_spec = SerializationControlSpec(
    parameters=LabelNoiseParameters(
        flip_rate=0.0,
        flip_direction="symmetric",
        selection_policy="seeded_record_hash",
        scope="train",
    ),
    seed=205,
)
roundtrip = apply_serialization_roundtrip(
    source=semantic_source, spec=serialization_spec, slot=control_slot("serialization")
)
validate_serialization_roundtrip(
    roundtrip,
    source=semantic_source,
    spec=serialization_spec,
    slot=control_slot("serialization"),
)

payload = {
    "repair_artifact_sha256": repaired.artifact_sha256(),
    "restored_record_ids_sha256": repaired.provenance.restored_record_ids_sha256,
    "repaired_targets_sha256": repaired.provenance.repaired_targets_sha256,
    "restored_count": repaired.provenance.restored_count,
    "restored": list(repaired.restored_record_ids),
    "roundtrip_artifact_sha256": roundtrip.artifact_sha256(),
    "codec_sha256": codec_sha256(),
    "decoded_targets_sha256": roundtrip.provenance.decoded_targets_sha256,
}
sys.stdout.write(json.dumps(payload, sort_keys=True))
"""


def _control_slot(kind: str):  # type: ignore[no-untyped-def]
    from aletheia_lab.benchmark.p2.contracts import CandidateSlot
    from aletheia_lab.benchmark.p2.identity import FamilyIdentity, LabelNoiseParameters

    repair = kind == "repair"
    seed = 204 if repair else 205
    rate = 0.20 if repair else 0.0
    return CandidateSlot(
        slot_id="M2-I1" if repair else "M2-B1",
        fault_type="label_noise",
        slot_kind="primary",
        role=("designed_improvement_control" if repair else "designed_benign_control"),
        identity=FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256="a" * 64,
            model_data_split_manifest_sha256="b" * 64,
            fault_type="label_noise",
            intervention_type=(
                "training_target_label_repair" if repair else "target_label_serialization_roundtrip"
            ),
            canonical_intervention_parameters=LabelNoiseParameters(
                flip_rate=rate,
                flip_direction="symmetric",
                selection_policy="seeded_record_hash",
                scope="train",
            ),
            seed=seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="label-control/v1",
            model_specification_sha256="c" * 64,
            preprocessing_specification_sha256="d" * 64,
            identity_schema_version="p2-family-identity/v1",
        ),
    )


def _run(seed: str, repo_root: Path) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
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


def test_controls_are_byte_identical_across_hash_seeds() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert _run("1", repo_root) == _run("999", repo_root)


def test_controls_survive_three_independent_processes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    outputs = {_run(seed, repo_root) for seed in ("0", "12345", "65535")}
    assert len(outputs) == 1


def test_cross_process_repair_matches_the_in_process_repair() -> None:
    """The subprocess and the test interpreter must agree on the restoration."""

    from aletheia_lab.benchmark.p2.identity import LabelNoiseParameters
    from aletheia_lab.benchmark.p2.label_controls import apply_label_repair
    from aletheia_lab.benchmark.p2.label_noise import (
        LABEL_SOURCE_SCHEMA_VERSION,
        LabelCorruptionSpec,
        LabelNoiseSource,
        apply_label_corruption,
    )

    repo_root = Path(__file__).resolve().parents[2]
    external = json.loads(_run("7", repo_root))

    record_ids = tuple(f"{index:05d}-SYNTH" for index in [*range(0, 400, 2), *range(1, 400, 2)])
    clean = LabelNoiseSource(
        schema_version=LABEL_SOURCE_SCHEMA_VERSION,
        split="train",
        record_ids=record_ids,
        targets=tuple(index % 2 for index in range(len(record_ids))),
        attested_feature_matrix_sha256="f" * 64,
        attested_preprocessing_specification_sha256="d" * 64,
        attested_model_specification_sha256="c" * 64,
    )
    spec = LabelCorruptionSpec(
        parameters=LabelNoiseParameters(
            flip_rate=0.20,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
        seed=204,
    )
    corrupted = apply_label_corruption(source=clean, spec=spec)
    local = apply_label_repair(
        source=clean, corrupted=corrupted, spec=spec, slot=_control_slot("repair")
    )

    assert external["repair_artifact_sha256"] == local.artifact_sha256()
    assert external["restored_record_ids_sha256"] == (local.provenance.restored_record_ids_sha256)
    assert external["restored_count"] == local.provenance.restored_count
    assert external["restored"] == list(local.restored_record_ids)


def test_cross_process_roundtrip_matches_the_in_process_roundtrip() -> None:
    """The codec digest and the round-trip artifact must not depend on the process."""

    from aletheia_lab.benchmark.p2.identity import LabelNoiseParameters
    from aletheia_lab.benchmark.p2.label_controls import (
        SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION,
        SemanticTargetSource,
        SerializationControlSpec,
        apply_serialization_roundtrip,
        codec_sha256,
    )

    repo_root = Path(__file__).resolve().parents[2]
    external = json.loads(_run("31", repo_root))

    record_ids = tuple(f"{index:05d}-SYNTH" for index in [*range(0, 400, 2), *range(1, 400, 2)])
    targets = tuple("Yes" if index % 2 else "No" for index in range(len(record_ids)))
    source = SemanticTargetSource(
        schema_version=SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION,
        split="train",
        record_ids=record_ids,
        targets=targets,
        attested_feature_matrix_sha256="f" * 64,
        attested_preprocessing_specification_sha256="d" * 64,
        attested_model_specification_sha256="c" * 64,
    )
    spec = SerializationControlSpec(
        parameters=LabelNoiseParameters(
            flip_rate=0.0,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
        seed=205,
    )
    local = apply_serialization_roundtrip(
        source=source, spec=spec, slot=_control_slot("serialization")
    )

    assert external["roundtrip_artifact_sha256"] == local.artifact_sha256()
    assert external["codec_sha256"] == codec_sha256()
    assert external["decoded_targets_sha256"] == local.provenance.decoded_targets_sha256
