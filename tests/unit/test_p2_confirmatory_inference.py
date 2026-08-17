"""Inference and fail-closed decision tests for the confirmatory study."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryExecutionError,
    ControlGate,
    ProbabilityMetrics,
    ProbabilityVector,
    score_probabilities,
)
from aletheia_lab.benchmark.p2.confirmatory_inference import (
    ConfirmatoryReplicate,
    DatasetAnalysis,
    InferenceRunPlan,
    analyze_dataset,
    build_replicate,
    decide_study,
    holm_adjust,
    paired_sign_flip_pvalue,
    two_way_product_weight_bootstrap,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    ConfirmatoryProtocol,
    DatasetBinding,
    load_confirmatory_protocol,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


def _protocol() -> ConfirmatoryProtocol:
    return load_confirmatory_protocol()


def _dataset(role: str) -> DatasetBinding:
    return next(item for item in _protocol().datasets if item.role == role)


def _truth() -> tuple[int, ...]:
    return tuple(index % 2 for index in range(40))


def _probabilities(
    *, direction: str, rate: float, seed: int, weak_co_primary: bool
) -> tuple[float, ...]:
    strength = 0.0 if weak_co_primary and rate == 0.3 else rate
    jitter = 0.0 if strength == 0.0 else ((seed % 5) - 2) * 0.001
    values: list[float] = []
    for truth in _truth():
        if direction == "yes_to_no" and truth == 1:
            values.append(max(0.51, 0.9 - strength + jitter))
        elif direction == "no_to_yes" and truth == 0:
            values.append(min(0.49, 0.1 + strength + jitter))
        else:
            values.append(0.9 if truth == 1 else 0.1)
    return tuple(values)


def _metrics(
    *, role: str, probabilities: tuple[float, ...], identity: object
) -> ProbabilityMetrics:
    vector = ProbabilityVector(
        role=role,
        record_ids=tuple(f"test-{index:02d}" for index in range(len(probabilities))),
        positive_probabilities=probabilities,
        model_artifact_sha256=canonical_sha256({"model": identity}),
        training_targets_sha256=canonical_sha256({"targets": identity}),
        evaluation_feature_matrix_sha256=_SHA_C,
        split_manifest_sha256=_SHA_A,
        protocol_sha256=_protocol().canonical_sha256(),
    )
    return score_probabilities(true_labels=_truth(), vector=vector)


def _controls(*, failed: bool = False) -> tuple[ControlGate, ...]:
    names = (
        "clean_reference",
        "serialization_roundtrip",
        "symmetric_matched_count",
        "label_repair",
    )
    return tuple(
        ControlGate(
            control_id=name,
            passed=not (failed and name == "label_repair"),
            detail_sha256=canonical_sha256({"control": name, "failed": failed}),
        )
        for name in names
    )


def _replicates(
    role: str,
    *,
    weak_co_primary: bool = False,
    failed_control_cell: str | None = None,
) -> tuple[ConfirmatoryReplicate, ...]:
    protocol = _protocol()
    dataset = _dataset(role)
    clean_probabilities = tuple(0.9 if value == 1 else 0.1 for value in _truth())
    clean = _metrics(
        role="clean_reference", probabilities=clean_probabilities, identity="clean"
    )
    values: list[ConfirmatoryReplicate] = []
    for cell in protocol.intervention_cells:
        seeds = (
            cell.primary_replicate_seeds
            if dataset.role == "primary"
            else cell.replication_replicate_seeds
        )
        for seed in seeds:
            probabilities = _probabilities(
                direction=cell.flip_direction,
                rate=cell.conditional_flip_rate,
                seed=seed,
                weak_co_primary=weak_co_primary,
            )
            observed = _metrics(
                role="class_conditional",
                probabilities=probabilities,
                identity=(dataset.dataset_id, cell.cell_id, seed),
            )
            values.append(
                build_replicate(
                    dataset_id=dataset.dataset_id,
                    dataset_role=dataset.role,
                    cell_id=cell.cell_id,
                    direction=cell.flip_direction,
                    conditional_rate=cell.conditional_flip_rate,
                    seed=seed,
                    protocol_sha256=protocol.canonical_sha256(),
                    split_manifest_sha256=_SHA_A if role == "primary" else _SHA_B,
                    mutation_sha256=canonical_sha256(
                        {"dataset": dataset.dataset_id, "cell": cell.cell_id, "seed": seed}
                    ),
                    clean_metrics=clean,
                    observed_metrics=observed,
                    controls=_controls(failed=cell.cell_id == failed_control_cell),
                )
            )
    return tuple(values)


def _analyze(
    role: str,
    *,
    weak_co_primary: bool = False,
    failed_control_cell: str | None = None,
) -> DatasetAnalysis:
    return analyze_dataset(
        protocol=_protocol(),
        dataset=_dataset(role),
        replicates=_replicates(
            role,
            weak_co_primary=weak_co_primary,
            failed_control_cell=failed_control_cell,
        ),
        run_plan=InferenceRunPlan.synthetic(bootstrap_resamples=200, test_resamples=500),
    )


def _promote_synthetic(analysis: DatasetAnalysis) -> DatasetAnalysis:
    dataset_pass = any(direction.direction_pass for direction in analysis.directions)
    return DatasetAnalysis.model_validate(
        {
            **analysis.model_dump(),
            "mode": "registered_confirmatory",
            "admission_authorized": True,
            "dataset_pass": dataset_pass,
        }
    )


def test_two_way_bootstrap_is_deterministic_and_resamples_both_factors() -> None:
    clean = (0.1, 0.2, 0.3, 0.4)
    observed = (
        (0.2, 0.3, 0.4, 0.5),
        (0.25, 0.35, 0.45, 0.55),
        (0.3, 0.4, 0.5, 0.6),
    )
    first = two_way_product_weight_bootstrap(
        clean_losses=clean, observed_losses_by_seed=observed, resamples=400, seed=7
    )
    second = two_way_product_weight_bootstrap(
        clean_losses=clean, observed_losses_by_seed=observed, resamples=400, seed=7
    )

    assert first == second
    assert first.factors == ("evaluation_record", "corruption_seed")
    assert first.point_estimate == pytest.approx(0.6)
    assert first.lower_bound > 0.0


@pytest.mark.parametrize(
    ("clean", "observed", "message"),
    [
        ((0.1,), ((0.2,), (0.3,)), "two evaluation records"),
        ((0.1, 0.2), ((0.2,), (0.3,)), "seed-by-record"),
        ((0.0, 0.0), ((0.1, 0.1), (0.2, 0.2)), "positive clean"),
    ],
)
def test_two_way_bootstrap_rejects_pseudoreplication_shapes(
    clean: Sequence[float], observed: Sequence[Sequence[float]], message: str
) -> None:
    with pytest.raises(ConfirmatoryExecutionError, match=message):
        two_way_product_weight_bootstrap(
            clean_losses=clean, observed_losses_by_seed=observed, resamples=100, seed=1
        )


def test_paired_sign_flip_and_holm_are_deterministic() -> None:
    pvalue = paired_sign_flip_pvalue((0.4,) * 30, resamples=5_000, seed=17)
    assert pvalue < 0.01
    assert pvalue == paired_sign_flip_pvalue((0.4,) * 30, resamples=5_000, seed=17)
    assert holm_adjust({"yes_to_no": 0.01, "no_to_yes": 0.04}) == {
        "yes_to_no": 0.02,
        "no_to_yes": 0.04,
    }


def test_holm_rejects_incomplete_family_and_invalid_pvalue() -> None:
    with pytest.raises(ConfirmatoryExecutionError, match="exactly two"):
        holm_adjust({"yes_to_no": 0.01})
    with pytest.raises(ConfirmatoryExecutionError, match="lie in"):
        holm_adjust({"yes_to_no": -0.1, "no_to_yes": 0.2})


def test_synthetic_analysis_reconciles_all_cells_but_cannot_authorize_admission() -> None:
    analysis = _analyze("primary")

    assert analysis.replicate_count == 180
    assert len(analysis.dose_summaries) == 6
    assert tuple(summary.replicate_count for summary in analysis.dose_summaries) == (30,) * 6
    assert all(direction.direction_pass for direction in analysis.directions)
    assert analysis.batch_technical_gates_pass
    assert not analysis.admission_authorized
    assert analysis.dataset_pass is None


def test_registered_plan_is_exact_and_cannot_reduce_resampling_budget() -> None:
    protocol = _protocol()
    plan = InferenceRunPlan.registered(protocol)
    plan.validate_against(protocol)
    assert plan.bootstrap_resamples == 10_000
    assert plan.hypothesis_test_resamples == 100_000

    reduced = plan.model_copy(update={"bootstrap_resamples": 100})
    with pytest.raises(ConfirmatoryExecutionError, match="complete frozen"):
        reduced.validate_against(protocol)


def test_missing_duplicate_and_wrong_provenance_replicates_fail_closed() -> None:
    protocol = _protocol()
    dataset = _dataset("primary")
    replicates = _replicates("primary")
    plan = InferenceRunPlan.synthetic()

    with pytest.raises(ConfirmatoryExecutionError, match="seed census"):
        analyze_dataset(
            protocol=protocol,
            dataset=dataset,
            replicates=replicates[:-1],
            run_plan=plan,
        )
    with pytest.raises(ConfirmatoryExecutionError, match="duplicate"):
        analyze_dataset(
            protocol=protocol,
            dataset=dataset,
            replicates=(*replicates, replicates[0]),
            run_plan=plan,
        )
    forged = replicates[0].model_copy(update={"protocol_sha256": _SHA_C})
    with pytest.raises(ConfirmatoryExecutionError, match="provenance"):
        analyze_dataset(
            protocol=protocol,
            dataset=dataset,
            replicates=(forged, *replicates[1:]),
            run_plan=plan,
        )


def test_cross_split_and_cross_clean_reference_reuse_fail_closed() -> None:
    protocol = _protocol()
    dataset = _dataset("primary")
    replicates = list(_replicates("primary"))
    plan = InferenceRunPlan.synthetic()

    replicates[1] = replicates[1].model_copy(update={"split_manifest_sha256": _SHA_C})
    with pytest.raises(ConfirmatoryExecutionError, match="one split"):
        analyze_dataset(
            protocol=protocol, dataset=dataset, replicates=replicates, run_plan=plan
        )

    replicates = list(_replicates("primary"))
    alternate_clean = _metrics(
        role="clean_reference",
        probabilities=tuple(0.8 if value else 0.2 for value in _truth()),
        identity="alternate-clean",
    )
    replicates[1] = build_replicate(
        dataset_id=replicates[1].dataset_id,
        dataset_role=replicates[1].dataset_role,
        cell_id=replicates[1].cell_id,
        direction=replicates[1].direction,
        conditional_rate=replicates[1].conditional_rate,
        seed=replicates[1].seed,
        protocol_sha256=replicates[1].protocol_sha256,
        split_manifest_sha256=replicates[1].split_manifest_sha256,
        mutation_sha256=replicates[1].mutation_sha256,
        clean_metrics=alternate_clean,
        observed_metrics=replicates[1].observed_metrics,
        controls=replicates[1].controls,
    )
    with pytest.raises(ConfirmatoryExecutionError, match="clean reference"):
        analyze_dataset(
            protocol=protocol, dataset=dataset, replicates=replicates, run_plan=plan
        )


def test_secondary_dose_response_cannot_rescue_failed_co_primary_cells() -> None:
    analysis = _analyze("primary", weak_co_primary=True)

    assert any(
        summary.conditional_rate < 0.3 and summary.mean_relative_log_loss_increase > 0.05
        for summary in analysis.dose_summaries
    )
    assert all(not direction.direction_pass for direction in analysis.directions)
    assert _promote_synthetic(analysis).dataset_pass is False


def test_any_control_failure_invalidates_the_complete_batch() -> None:
    analysis = _analyze("primary", failed_control_cell="ccn-yes-to-no-10")

    assert not analysis.batch_technical_gates_pass
    assert all(not direction.all_technical_gates_pass for direction in analysis.directions)
    assert all(not direction.direction_pass for direction in analysis.directions)


def test_external_replication_cannot_rescue_a_failed_primary() -> None:
    primary = _promote_synthetic(_analyze("primary", weak_co_primary=True))
    replication = _promote_synthetic(_analyze("external_replication"))

    decision = decide_study(
        protocol=_protocol(), primary=primary, replication=replication
    )

    assert not decision.primary_pass
    assert decision.replication_pass
    assert not decision.mechanism_admitted
    assert not decision.cross_dataset_claim_allowed
    assert decision.disposition == "retain_fail_closed_and_narrow_claim"


def test_same_direction_must_pass_both_datasets_for_cross_dataset_claim() -> None:
    primary = _promote_synthetic(_analyze("primary"))
    replication = _promote_synthetic(_analyze("external_replication"))

    decision = decide_study(
        protocol=_protocol(), primary=primary, replication=replication
    )

    assert decision.mechanism_admitted
    assert decision.cross_dataset_claim_allowed
    assert decision.cross_dataset_direction == "yes_to_no"
    assert decision.disposition == "bounded_cross_dataset_replication"


def test_synthetic_analysis_cannot_be_passed_directly_to_study_decision() -> None:
    with pytest.raises(ConfirmatoryExecutionError, match="Synthetic|synthetic"):
        decide_study(
            protocol=_protocol(),
            primary=_analyze("primary"),
            replication=_analyze("external_replication"),
        )
