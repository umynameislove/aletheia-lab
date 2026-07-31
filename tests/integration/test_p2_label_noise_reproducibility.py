"""Cross-process reproducibility for deterministic label corruption.

A selection that depended on dictionary iteration order or interpreter start-up
randomness would still look correct inside one process. Proving independence
requires separate interpreters started with different hash seeds.
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

from aletheia_lab.benchmark.p2.identity import LabelNoiseParameters
from aletheia_lab.benchmark.p2.label_noise import (
    LABEL_SOURCE_SCHEMA_VERSION,
    LabelCorruptionSpec,
    LabelNoiseSource,
    apply_label_corruption,
    validate_label_corruption,
)

# Interleave the identifiers so a position-dependent implementation would
# produce a different selection here than in a sorted source.
pairs = [
    (f"{record_id:05d}-SYNTH", position % 2)
    for position, record_id in enumerate([*range(0, 400, 2), *range(1, 400, 2)])
]
if os.environ.get("P2_REVERSE_ROWS") == "1":
    pairs.reverse()
record_ids = tuple(record_id for record_id, _ in pairs)
targets = tuple(target for _, target in pairs)

source = LabelNoiseSource(
    schema_version=LABEL_SOURCE_SCHEMA_VERSION,
    split="train",
    record_ids=record_ids,
    targets=targets,
    attested_feature_matrix_sha256="f" * 64,
    attested_preprocessing_specification_sha256="d" * 64,
    attested_model_specification_sha256="c" * 64,
)
spec = LabelCorruptionSpec(
    parameters=LabelNoiseParameters(
        flip_rate=0.05,
        flip_direction="symmetric",
        selection_policy="seeded_record_hash",
        scope="train",
    ),
    seed=202,
)

result = apply_label_corruption(source=source, spec=spec)
validate_label_corruption(result, source=source, spec=spec)

payload = {
    "artifact_sha256": result.artifact_sha256(),
    "semantic_sha256": result.semantic_sha256(),
    "source_record_ids_sha256": result.provenance.source_record_ids_sha256,
    "mutation_map_sha256": result.provenance.mutation_map_sha256,
    "mutated_targets_sha256": result.provenance.mutated_targets_sha256,
    "mutation_count": result.provenance.mutation_count,
    "selected": sorted(result.mutation_map.record_ids()),
}
sys.stdout.write(json.dumps(payload, sort_keys=True))
"""


def _run(seed: str, repo_root: Path, *, reverse_rows: bool = False) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = str(repo_root / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["P2_REVERSE_ROWS"] = "1" if reverse_rows else "0"
    completed = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        check=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    return completed.stdout


def test_corruption_is_byte_identical_across_hash_seeds() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    first = _run("1", repo_root)
    second = _run("999", repo_root)
    assert first == second


def test_corruption_survives_three_independent_processes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    outputs = {_run(seed, repo_root) for seed in ("0", "12345", "65535")}
    assert len(outputs) == 1


def test_semantic_identity_survives_process_and_row_order_changes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ordered = json.loads(_run("1", repo_root))
    reversed_rows = json.loads(_run("999", repo_root, reverse_rows=True))

    assert ordered["semantic_sha256"] == reversed_rows["semantic_sha256"]
    assert ordered["artifact_sha256"] != reversed_rows["artifact_sha256"]
    assert ordered["source_record_ids_sha256"] != (reversed_rows["source_record_ids_sha256"])
    assert ordered["selected"] == reversed_rows["selected"]


def test_cross_process_result_matches_the_in_process_result() -> None:
    """The subprocess and the test interpreter must agree on the selection."""

    import json

    from aletheia_lab.benchmark.p2.identity import LabelNoiseParameters
    from aletheia_lab.benchmark.p2.label_noise import (
        LABEL_SOURCE_SCHEMA_VERSION,
        LabelCorruptionSpec,
        LabelNoiseSource,
        apply_label_corruption,
    )

    repo_root = Path(__file__).resolve().parents[2]
    external = json.loads(_run("7", repo_root))

    record_ids = tuple(f"{index:05d}-SYNTH" for index in [*range(0, 400, 2), *range(1, 400, 2)])
    source = LabelNoiseSource(
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
            flip_rate=0.05,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
        seed=202,
    )
    local = apply_label_corruption(source=source, spec=spec)

    assert external["artifact_sha256"] == local.artifact_sha256()
    assert external["semantic_sha256"] == local.semantic_sha256()
    assert external["mutation_map_sha256"] == local.provenance.mutation_map_sha256
    assert external["mutation_count"] == local.provenance.mutation_count
    assert external["selected"] == sorted(local.mutation_map.record_ids())
