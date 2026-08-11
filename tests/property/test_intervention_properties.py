"""Property tests for label-corruption intervention invariants.

Covered invariants:

  1.  Same source/spec/seed produces byte-identical artifact
  2.  Changing seed or flip_rate changes semantic identity
  3.  Record membership and order preserved after corruption
  4.  Only targets change — no feature field exists on source or result (structural)
  5.  Mutation map matches exactly the records that were actually changed
  6.  Reported count/hash/rate validated by model validators
  7.  Zero flip_rate (no-op) is rejected by spec validator
  8.  model_copy / model_construct forge fails at re-validation
  9.  Malformed label/rate and non-finite input fail closed
  10. Output does not self-declare outcome, eligibility or causal conclusion
  11. Deterministic result does not depend on PYTHONHASHSEED
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.identity import LabelNoiseParameters
from aletheia_lab.benchmark.p2.label_noise import (
    LABEL_MUTATION_SCHEMA_VERSION,
    LABEL_SOURCE_SCHEMA_VERSION,
    LabelCorruptionProvenance,
    LabelCorruptionResult,
    LabelCorruptionSpec,
    LabelNoiseSource,
    MutationEntry,
    MutationMap,
    TargetQualityAudit,
    apply_label_corruption,
    mutation_count,
    wilson_interval,
)

# ---------------------------------------------------------------------------
# Constants and fixed fixtures
# ---------------------------------------------------------------------------

# 10 records: 5 positives (odd indices), 5 negatives (even indices).
# With flip_rate=0.3 → count=3, class erasure is impossible (3 < 5).
_RECORD_IDS_10: tuple[str, ...] = tuple(f"rec-{i:03d}" for i in range(10))
_TARGETS_10: tuple[int, ...] = tuple(i % 2 for i in range(10))

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64

#: Fields that must not appear on any intervention output model.
_FORBIDDEN_OUTCOME_FIELDS = frozenset(
    {"passed", "eligible", "verdict", "outcome", "expected_behavior"}
)

#: Evaluator-facing vocabulary that must not appear as field names on the
#: Diagnosis-facing fields must not reveal evaluator vocabulary.
_EVALUATOR_VOCAB = frozenset(
    {
        "label_noise",
        "flip",
        "corrupt",
        "mutation",
        "mutated",
        "ground_truth",
        "answer_key",
        "expected_behavior",
        "eligible_failure",
        "seed",
        "injection",
        "evaluator",
        "hidden_cause",
        "cause_label",
        "original_label",
    }
)


def _make_source(
    record_ids: tuple[str, ...] = _RECORD_IDS_10,
    targets: tuple[int, ...] = _TARGETS_10,
) -> LabelNoiseSource:
    """Build a minimal valid LabelNoiseSource."""
    return LabelNoiseSource(
        schema_version=LABEL_SOURCE_SCHEMA_VERSION,
        split="train",
        record_ids=record_ids,
        targets=targets,
        attested_feature_matrix_sha256=_SHA_A,
        attested_preprocessing_specification_sha256=_SHA_B,
        attested_model_specification_sha256=_SHA_C,
    )


def _make_params(flip_rate: float = 0.3) -> LabelNoiseParameters:
    return LabelNoiseParameters(
        flip_rate=flip_rate,
        flip_direction="symmetric",
        selection_policy="seeded_record_hash",
        scope="train",
    )


def _make_spec(flip_rate: float = 0.3, seed: int = 42) -> LabelCorruptionSpec:
    return LabelCorruptionSpec(parameters=_make_params(flip_rate=flip_rate), seed=seed)


# Module-level fixed source and spec used by determinism tests.
_FIXED_SOURCE = _make_source()
_FIXED_SPEC = _make_spec(flip_rate=0.3, seed=42)

# ---------------------------------------------------------------------------
# Identical source, specification, and seed produce identical artifacts
# ---------------------------------------------------------------------------


def test_fixed_source_and_spec_are_deterministic() -> None:
    """apply_label_corruption returns the same artifact SHA for the same inputs."""
    result_a = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    result_b = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    assert result_a.artifact_sha256() == result_b.artifact_sha256()
    assert result_a.semantic_sha256() == result_b.semantic_sha256()


@given(seed=st.integers(min_value=0, max_value=50_000))
@settings(max_examples=40)
@example(seed=0)
@example(seed=1)
@example(seed=42)
def test_hypothesis_determinism_across_seeds(seed: int) -> None:
    """Calling apply_label_corruption twice with the same seed is idempotent.

    max_examples=40: each call invokes the injector; fixture cost is higher
    than pure schema properties.
    """
    spec = _make_spec(flip_rate=0.3, seed=seed)
    result_a = apply_label_corruption(source=_FIXED_SOURCE, spec=spec)
    result_b = apply_label_corruption(source=_FIXED_SOURCE, spec=spec)
    assert result_a.artifact_sha256() == result_b.artifact_sha256()


# ---------------------------------------------------------------------------
# Changing seed or flip rate changes semantic identity
# ---------------------------------------------------------------------------


@given(
    seed_a=st.integers(min_value=0, max_value=9_000),
    seed_delta=st.integers(min_value=1, max_value=1_000),
)
@settings(max_examples=40)
@example(seed_a=0, seed_delta=1)
@example(seed_a=42, seed_delta=57)
def test_different_seed_changes_artifact_identity(seed_a: int, seed_delta: int) -> None:
    """Different seeds produce results with different provenance seeds.

    The seed is embedded in the provenance and the artifact SHA, so different
    seeds must produce different artifact SHAs regardless of whether the
    randomly selected records overlap.
    """
    seed_b = seed_a + seed_delta
    spec_a = _make_spec(flip_rate=0.3, seed=seed_a)
    spec_b = _make_spec(flip_rate=0.3, seed=seed_b)
    result_a = apply_label_corruption(source=_FIXED_SOURCE, spec=spec_a)
    result_b = apply_label_corruption(source=_FIXED_SOURCE, spec=spec_b)
    # Seeds are embedded in provenance, so artifact SHA must differ
    assert result_a.provenance.seed != result_b.provenance.seed
    assert result_a.artifact_sha256() != result_b.artifact_sha256()


def test_different_flip_rate_changes_semantic_identity() -> None:
    """Different flip rates produce different semantic identity."""
    spec_low = _make_spec(flip_rate=0.1, seed=42)
    spec_high = _make_spec(flip_rate=0.4, seed=42)
    result_low = apply_label_corruption(source=_FIXED_SOURCE, spec=spec_low)
    result_high = apply_label_corruption(source=_FIXED_SOURCE, spec=spec_high)
    assert result_low.semantic_sha256() != result_high.semantic_sha256()
    assert result_low.provenance.mutation_count != result_high.provenance.mutation_count


# ---------------------------------------------------------------------------
# Record membership and order are preserved
# ---------------------------------------------------------------------------


def test_record_membership_preserved_after_corruption() -> None:
    """apply_label_corruption preserves record IDs and their order."""
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    assert result.record_ids == _FIXED_SOURCE.record_ids
    assert len(result.record_ids) == len(_FIXED_SOURCE.record_ids)
    assert set(result.record_ids) == set(_FIXED_SOURCE.record_ids)


@given(seed=st.integers(min_value=0, max_value=50_000))
@settings(max_examples=40)
def test_hypothesis_membership_preserved(seed: int) -> None:
    """Membership and record count are preserved for all tested seeds."""
    spec = _make_spec(flip_rate=0.3, seed=seed)
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=spec)
    assert set(result.record_ids) == set(_FIXED_SOURCE.record_ids)
    assert len(result.record_ids) == _FIXED_SOURCE.record_count


# ---------------------------------------------------------------------------
# Only targets change; feature fields are structurally unavailable
# ---------------------------------------------------------------------------


def test_label_noise_source_has_no_feature_field() -> None:
    """LabelNoiseSource has no feature or feature_matrix field.

    The injector cannot alter features it never receives; this is enforced
    by the type: if the source has no feature field, the injector cannot
    modify one regardless of implementation.
    """
    assert "features" not in LabelNoiseSource.model_fields
    assert "feature_matrix" not in LabelNoiseSource.model_fields


def test_label_corruption_result_has_no_feature_field() -> None:
    """LabelCorruptionResult carries no feature matrix."""
    assert "features" not in LabelCorruptionResult.model_fields
    assert "feature_matrix" not in LabelCorruptionResult.model_fields


def test_only_targets_differ_record_ids_unchanged() -> None:
    """After corruption, record_ids are byte-identical to the source."""
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    assert result.record_ids == _FIXED_SOURCE.record_ids  # exact order and content


# ---------------------------------------------------------------------------
# Mutation map matches exactly the mutated records
# ---------------------------------------------------------------------------


def test_mutation_map_matches_exactly_changed_records() -> None:
    """mutation_map contains exactly the records where labels changed.

    Independent oracle: compare source vs mutated targets record-by-record
    without using the production mutation_map logic.
    """
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)

    # Oracle: independently compute which records changed
    original_by_id = dict(zip(_FIXED_SOURCE.record_ids, _FIXED_SOURCE.targets, strict=True))
    mutated_by_id = dict(zip(result.record_ids, result.mutated_targets, strict=True))

    oracle_changed = frozenset(
        rid
        for rid in _FIXED_SOURCE.record_ids
        if original_by_id[rid] != mutated_by_id[rid]
    )

    assert result.mutation_map.record_ids() == oracle_changed


@given(seed=st.integers(min_value=0, max_value=50_000))
@settings(max_examples=40)
def test_hypothesis_mutation_map_count_matches_provenance(seed: int) -> None:
    """mutation_map.count always equals provenance.mutation_count."""
    spec = _make_spec(flip_rate=0.3, seed=seed)
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=spec)
    assert result.mutation_map.count == result.provenance.mutation_count


def test_mutation_count_oracle_matches_decimal_rule() -> None:
    """mutation_count uses decimal round-half-up, not Python float round.

    Independent oracle: recompute with decimal arithmetic directly.
    """
    from decimal import ROUND_HALF_UP, Decimal

    for flip_rate, n_records, expected in [
        (0.3, 10, 3),    # 0.3 * 10 = 3.0 → 3
        (0.25, 10, 3),   # 0.25 * 10 = 2.5 → 3 (round half up, not banker's)
        (0.1, 10, 1),    # 0.1 * 10 = 1.0 → 1
        (0.05, 10, 1),   # 0.05 * 10 = 0.5 → 1 (round half up)
        (0.05, 20, 1),   # 0.05 * 20 = 1.0 → 1
    ]:
        oracle = int(
            (Decimal(str(flip_rate)) * Decimal(n_records)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        assert mutation_count(flip_rate=flip_rate, record_count=n_records) == oracle == expected, (
            f"flip_rate={flip_rate}, n_records={n_records}: "
            f"expected {expected}, oracle {oracle}"
        )


# ---------------------------------------------------------------------------
# Model validators recompute reported counts, hashes, and rates
# ---------------------------------------------------------------------------


def test_forged_mutation_count_rejected_by_validator() -> None:
    """A mutation_count that does not follow the declared rate is rejected."""
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    prov = result.provenance.model_dump(warnings=False)
    prov["mutation_count"] = prov["mutation_count"] + 1  # wrong count
    with pytest.raises(ValidationError, match="mutation_count"):
        LabelCorruptionProvenance.model_validate(prov)


def test_forged_achieved_flip_rate_rejected_by_validator() -> None:
    """An achieved_flip_rate not derived from counts is rejected."""
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    prov = result.provenance.model_dump(warnings=False)
    prov["achieved_flip_rate"] = 0.999  # not derived from mutation_count / record_count
    with pytest.raises(ValidationError, match="achieved_flip_rate"):
        LabelCorruptionProvenance.model_validate(prov)


def test_forged_target_quality_audit_rate_rejected() -> None:
    """TargetQualityAudit rejects a disagreement rate not derived from counts."""
    lower, upper = wilson_interval(successes=3, trials=10)
    with pytest.raises(ValidationError, match="disagreement_rate"):
        TargetQualityAudit(
            schema_version="p2-target-quality-audit/v1",
            audited_record_count=10,
            disagreeing_record_count=3,
            disagreement_rate=0.999,  # should be 3/10 = 0.3
            disagreement_rate_lower_bound=lower,
            disagreement_rate_upper_bound=upper,
            interval_method="wilson-score/95",
            protocol_version="target-quality-audit/v1",
        )


# ---------------------------------------------------------------------------
# A zero flip rate is rejected as a no-op
# ---------------------------------------------------------------------------


def test_zero_flip_rate_rejected_by_spec_validator() -> None:
    """LabelCorruptionSpec rejects flip_rate=0.0 as a no-op."""
    with pytest.raises(ValidationError, match="positive flip rate"):
        LabelCorruptionSpec(
            parameters=LabelNoiseParameters(
                flip_rate=0.0,
                flip_direction="symmetric",
                selection_policy="seeded_record_hash",
                scope="train",
            ),
            seed=42,
        )


# ---------------------------------------------------------------------------
# Model-copy and model-construct forgeries fail revalidation
# ---------------------------------------------------------------------------


def test_model_copy_forge_of_mutation_count_fails_revalidation() -> None:
    """A forged mutation_count via model_copy fails at model_validate.

    model_copy skips validators; the model_validate call re-runs them.
    """
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    forged_prov = result.provenance.model_copy(
        update={"mutation_count": result.provenance.mutation_count + 5}
    )
    with pytest.raises(ValidationError, match="mutation_count"):
        LabelCorruptionProvenance.model_validate(forged_prov.model_dump(warnings=False))


def test_model_construct_forge_of_targets_sha256_fails_revalidation() -> None:
    """A forged source_targets_sha256 equal to mutated_targets_sha256 fails.

    LabelCorruptionProvenance requires source_targets != mutated_targets.
    model_construct skips validators; model_validate re-runs them.
    """
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    prov_data = result.provenance.model_dump(warnings=False)
    # Make mutated_targets_sha256 equal to source_targets_sha256 → forged no-change
    prov_data["mutated_targets_sha256"] = prov_data["source_targets_sha256"]
    forged = LabelCorruptionProvenance.model_construct(**prov_data)
    with pytest.raises(ValidationError, match="corruption must change"):
        LabelCorruptionProvenance.model_validate(forged.model_dump(warnings=False))


# ---------------------------------------------------------------------------
# Malformed labels, rates, and non-finite inputs fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "bad_label"),
    [
        ("label 2", 2),
        ("label -1", -1),
        ("label 99", 99),
    ],
)
def test_non_binary_target_rejected(description: str, bad_label: int) -> None:
    """LabelNoiseSource rejects non-binary target labels."""
    with pytest.raises(ValidationError, match="binary"):
        LabelNoiseSource(
            schema_version=LABEL_SOURCE_SCHEMA_VERSION,
            split="train",
            record_ids=("rec-001",),
            targets=(bad_label,),
            attested_feature_matrix_sha256=_SHA_A,
            attested_preprocessing_specification_sha256=_SHA_B,
            attested_model_specification_sha256=_SHA_C,
        )


def test_empty_record_id_rejected() -> None:
    """LabelNoiseSource rejects empty or blank record IDs."""
    with pytest.raises(ValidationError, match="blank"):
        LabelNoiseSource(
            schema_version=LABEL_SOURCE_SCHEMA_VERSION,
            split="train",
            record_ids=("rec-001", ""),  # empty string not allowed
            targets=(0, 1),
            attested_feature_matrix_sha256=_SHA_A,
            attested_preprocessing_specification_sha256=_SHA_B,
            attested_model_specification_sha256=_SHA_C,
        )


def test_duplicate_record_id_rejected() -> None:
    """LabelNoiseSource rejects duplicate record IDs."""
    with pytest.raises(ValidationError, match="unique"):
        LabelNoiseSource(
            schema_version=LABEL_SOURCE_SCHEMA_VERSION,
            split="train",
            record_ids=("rec-001", "rec-001"),  # duplicate
            targets=(0, 1),
            attested_feature_matrix_sha256=_SHA_A,
            attested_preprocessing_specification_sha256=_SHA_B,
            attested_model_specification_sha256=_SHA_C,
        )


@pytest.mark.parametrize(
    ("description", "bad_rate"),
    [
        ("NaN", float("nan")),
        ("positive inf", float("inf")),
        ("negative inf", float("-inf")),
    ],
)
def test_non_finite_flip_rate_rejected(description: str, bad_rate: float) -> None:
    """LabelNoiseParameters rejects non-finite flip rates."""
    with pytest.raises(ValidationError):
        LabelNoiseParameters(
            flip_rate=bad_rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        )


@given(
    bad_rate=st.one_of(
        st.floats(min_value=0.5001, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False),
    )
)
def test_out_of_range_flip_rate_rejected(bad_rate: float) -> None:
    """LabelNoiseParameters rejects a flip rate outside its valid interval."""
    with pytest.raises(ValidationError):
        LabelNoiseParameters(
            flip_rate=bad_rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        )


def test_mutation_map_with_duplicate_entry_rejected() -> None:
    """MutationMap rejects duplicate record_id entries."""
    entry = MutationEntry(record_id="rec-001", original_label=0, mutated_label=1)
    with pytest.raises(ValidationError, match="mutated at most once"):
        MutationMap(
            schema_version=LABEL_MUTATION_SCHEMA_VERSION,
            entries=(entry, entry),  # same record twice
        )


def test_mutation_entry_no_actual_change_rejected() -> None:
    """MutationEntry rejects an entry with no actual label change."""
    with pytest.raises(ValidationError, match="actual change"):
        MutationEntry(record_id="rec-001", original_label=0, mutated_label=0)


# ---------------------------------------------------------------------------
# Outputs do not self-declare outcomes or causal conclusions
# ---------------------------------------------------------------------------


def test_label_corruption_result_has_no_outcome_fields() -> None:
    """LabelCorruptionResult has no self-declared outcome fields."""
    assert not (set(LabelCorruptionResult.model_fields) & _FORBIDDEN_OUTCOME_FIELDS), (
        f"Forbidden fields found: {set(LabelCorruptionResult.model_fields) & _FORBIDDEN_OUTCOME_FIELDS}"
    )


def test_label_corruption_provenance_has_no_outcome_fields() -> None:
    """LabelCorruptionProvenance has no self-declared outcome fields."""
    assert not (set(LabelCorruptionProvenance.model_fields) & _FORBIDDEN_OUTCOME_FIELDS)


def test_target_quality_audit_has_no_evaluator_vocabulary_fields() -> None:
    """TargetQualityAudit field names exclude evaluator-facing vocabulary.

    The boundary is structural: a model with no 'seed', 'flip', 'mutation',
    'label_noise' etc. fields cannot carry those values to a diagnoser.
    """
    audit_fields = frozenset(TargetQualityAudit.model_fields.keys())
    assert not (audit_fields & _EVALUATOR_VOCAB), (
        f"Evaluator vocabulary found in TargetQualityAudit fields: "
        f"{audit_fields & _EVALUATOR_VOCAB}"
    )


def test_diagnosis_facing_audit_contains_no_raw_record_ids() -> None:
    """Diagnosis-facing TargetQualityAudit contains no raw record IDs.

    LabelCorruptionResult (evaluator-only) intentionally stores raw IDs.
    The diagnosis boundary is enforced by type: TargetQualityAudit contains
    only counts, rates and protocol identifiers — no field can carry a
    record identifier or label value.
    """
    result = apply_label_corruption(source=_FIXED_SOURCE, spec=_FIXED_SPEC)
    audit_json = json.dumps(
        result.projection.target_quality_audit.model_dump(mode="json")
    )
    for record_id in _RECORD_IDS_10:
        assert record_id not in audit_json, (
            f"Raw record ID {record_id!r} found in diagnosis-facing TargetQualityAudit"
        )



# ---------------------------------------------------------------------------
# Deterministic results do not depend on the interpreter hash seed
# ---------------------------------------------------------------------------

_HASH_SEED_SCRIPT = "\n".join([
    "import json",
    "from aletheia_lab.benchmark.p2.label_noise import (",
    "    LabelNoiseSource, LabelCorruptionSpec, apply_label_corruption,",
    "    LABEL_SOURCE_SCHEMA_VERSION,",
    ")",
    "from aletheia_lab.benchmark.p2.identity import LabelNoiseParameters",
    f"_IDS = {tuple(f'rec-{i:03d}' for i in range(10))!r}",
    f"_TGTS = {tuple(i % 2 for i in range(10))!r}",
    f"_SHA = {'a' * 64!r}",
    "source = LabelNoiseSource(",
    "    schema_version=LABEL_SOURCE_SCHEMA_VERSION,",
    '    split="train",',
    "    record_ids=_IDS,",
    "    targets=_TGTS,",
    "    attested_feature_matrix_sha256=_SHA,",
    "    attested_preprocessing_specification_sha256=_SHA,",
    "    attested_model_specification_sha256=_SHA,",
    ")",
    "params = LabelNoiseParameters(",
    "    flip_rate=0.3,",
    '    flip_direction="symmetric",',
    '    selection_policy="seeded_record_hash",',
    '    scope="train",',
    ")",
    "spec = LabelCorruptionSpec(parameters=params, seed=42)",
    "result = apply_label_corruption(source=source, spec=spec)",
    'print(json.dumps({"artifact": result.artifact_sha256(), "semantic": result.semantic_sha256()}))',
])


def test_pythonhashseed_invariance() -> None:
    """Artifact and semantic SHA are identical across interpreter hash seeds.

    Subprocess uses sys.executable — not hard-coded python or python3.
    """

    def _run(seed: str) -> str:
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return result.stdout.strip()

    out_1 = _run("1")
    out_999 = _run("999")

    assert out_1 == out_999, (
        f"Output differs between PYTHONHASHSEED=1 and PYTHONHASHSEED=999.\n"
        f"seed=1  : {out_1[:120]}\n"
        f"seed=999: {out_999[:120]}"
    )
