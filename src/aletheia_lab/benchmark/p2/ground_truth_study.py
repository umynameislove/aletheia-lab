"""Bounded local method checks on public bundled ML data, never old P2 runs."""

from __future__ import annotations

import copy
import time
from collections import Counter
from dataclasses import asdict
from typing import Any

import numpy as np
import sklearn  # type: ignore[import-untyped]
from sklearn.datasets import load_digits, load_iris, load_wine  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import log_loss  # type: ignore[import-untyped]
from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.ground_truth_validity import (
    assess_ground_truth,
    empirical_mmd_squared,
    empirical_total_variation,
    fixed_pool_resampling_test,
    iid_mmd_permutation,
)
from aletheia_lab.project.identity import content_sha256

SEEDS = (31001, 31002, 31003, 31004, 31005)
BASE_CASES = (
    "sham",
    "input_sign",
    "resident_sign",
    "unused_metadata",
    "double_sign",
    "not_applied",
    "wrong_repair",
)
TRANSFER_CASES = (*BASE_CASES, "training_target_roll")


def study_design() -> dict[str, Any]:
    return {
        "study": "ground-truth-validity-method-audit/1",
        "scope": "prospectively_selected_controlled_method_transfer_not_confirmatory_mechanism_admission",
        "development": ["iris"],
        "transfer": ["wine", "digits"],
        "source_exposure": "public_docs_read_before_forecast_not_source_blind",
        "seeds": list(SEEDS),
        "development_cases": list(BASE_CASES),
        "transfer_cases": list(TRANSFER_CASES),
        "source_roles": "reserved_for_this_new_method_not_claimed_never_used_elsewhere",
        "split": {"test_size": 0.4, "stratified": True, "shuffle": True},
        "model": {"type": "LogisticRegression", "C": 1.0, "max_iter": 2000, "solver": "lbfgs"},
        "preprocessing": "StandardScaler_train_only",
        "threads": 1,
        "harm": "paired_log_loss_delta_greater_or_equal_0.01",
        "control_tolerance": 1e-10,
        "compatible_set": "all_cases_in_same_source_seed_with_identical_entire_visible_payload",
        "views": ["prediction_only", "configured_pipeline_lineage"],
        "lineage": "actual_input_coefficients_and_training_target_hashes_no_hidden_case_label",
        "baselines": [
            "injection_label_only",
            "fidelity_plus_consequence",
            "ordinary_sufficient_lineage",
        ],
        "sampling": {
            "trials": 100,
            "draws_per_arm": 24,
            "resamples": 199,
            "bandwidth_squared": 1.0,
        },
        "sampling_alternative": "all_target_draws_from_first_pool_atom",
        "simulation_rng": "SeedSequence_domain_separated_data_iid_MC_pool_MC",
        "forecasts": [
            {
                "id": "measure",
                "source": "theory_derived",
                "prediction": "uniform_duplication_zero_measure_distance_unequal_mass_nonzero",
            },
            {
                "id": "sampling",
                "source": "theory_derived",
                "prediction": "fixed_pool_draws_may_change_empirical_measure_without_changing_declared_law",
            },
            {
                "id": "twins",
                "source": "model_inferred",
                "prediction": "input_sign_and_resident_sign_same_prediction_payload_different_lineage",
            },
            {
                "id": "effect",
                "source": "empirical_risk",
                "prediction": "both_sign_faults_harmful_on_each_reserved_source_seed",
            },
            {
                "id": "transfer",
                "source": "empirical_risk",
                "prediction": "withheld_training_target_roll_harmful_and_repaired_on_each_reserved_source_seed",
            },
        ],
        "non_goals": [
            "old_result_re_admission",
            "LLM_efficacy",
            "population_causal_identification",
            "new_MMD_algorithm",
        ],
    }


def _array_hash(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values, dtype="<f8")
    return content_sha256(str(array.shape).encode("ascii") + array.tobytes())


def _fit(x: np.ndarray, y: np.ndarray, seed: int) -> LogisticRegression:
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000, random_state=seed)
    model.fit(x, y)
    if np.max(model.n_iter_) >= 2000:
        raise ValueError("model did not converge within frozen budget")
    return model


def _model_hash(model: LogisticRegression) -> str:
    return canonical_sha256(
        {
            "coef": _array_hash(model.coef_),
            "intercept": _array_hash(model.intercept_),
            "classes": _array_hash(model.classes_),
        }
    )


def run_source(source: str, *, phase: str, seeds: tuple[int, ...] = SEEDS) -> dict[str, Any]:
    loaders = {"iris": load_iris, "wine": load_wine, "digits": load_digits}
    if phase not in ("development", "transfer") or source not in study_design()[phase]:
        raise ValueError("source is not eligible for this phase")
    started = time.perf_counter()
    dataset = loaders[source]()
    x, y = np.asarray(dataset.data, dtype=np.float64), np.asarray(dataset.target, dtype=int)
    cases = BASE_CASES if phase == "development" else TRANSFER_CASES
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    source_sha256 = canonical_sha256({"x": _array_hash(x), "y": _array_hash(y)})
    for seed in seeds:
        try:
            with threadpool_limits(limits=1):
                cell = _run_seed(x, y, source, seed, cases)
            records.extend(cell)
        except Exception as error:
            # Failed source/seed is kept with every planned slot, never omitted.
            failures.append(
                {
                    "source": source,
                    "seed": seed,
                    "cases": list(cases),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    return {
        "source": source,
        "phase": phase,
        "source_sha256": source_sha256,
        "records": records,
        "failures": failures,
        "planned": len(seeds) * len(cases),
        "duration_seconds": time.perf_counter() - started,
        "source_unit": "fixed_public_dataset_one_source_not_five_independent_sources",
    }


def _repair_execution(
    case: str,
    model: LogisticRegression,
    inputs: np.ndarray,
    train_x: np.ndarray,
    train_y: np.ndarray,
    seed: int,
) -> tuple[LogisticRegression, np.ndarray]:
    """Execute declared repair; never substitute precomputed clean predictions."""
    repaired_model, repaired_inputs = copy.deepcopy(model), inputs.copy()
    if case in ("input_sign", "double_sign"):
        repaired_inputs *= -1
    if case in ("resident_sign", "double_sign"):
        repaired_model.coef_ *= -1
    if case in ("training_target_roll", "wrong_repair"):
        # Same-model reload is deliberately ineffective for the wrong input.
        repaired_model = _fit(train_x, train_y, seed)
    return repaired_model, repaired_inputs


def _run_seed(
    x: np.ndarray,
    y: np.ndarray,
    source: str,
    seed: int,
    cases: tuple[str, ...],
) -> list[dict[str, Any]]:
    train, test = train_test_split(np.arange(len(y)), test_size=0.4, stratify=y, random_state=seed)
    scaler = StandardScaler().fit(x[train])
    train_x, test_x = scaler.transform(x[train]), scaler.transform(x[test])
    clean = _fit(train_x, y[train], seed)
    baseline = clean.predict_proba(test_x)
    clean_loss = float(log_loss(y[test], baseline, labels=clean.classes_))
    # An actual independently repeated fit provides sham, not saved predictions.
    sham = _fit(train_x, y[train], seed)
    sham_loss = float(log_loss(y[test], sham.predict_proba(test_x), labels=sham.classes_))
    losses = -np.log(np.clip(baseline[np.arange(len(test)), y[test]], 1e-15, 1))
    class_losses = {
        int(label): float(np.mean(losses[y[test] == label])) for label in clean.classes_
    }
    standardized_loss = float(np.mean(list(class_losses.values())))
    # Ordinary exact weighting: prior changes, within-class empirical mass does not.
    prior_checks = []
    for odds in (0.25, 4.0):
        weights = np.where(y[test] == 0, odds, 1.0)
        prior_checks.append(
            {
                "class_zero_weight": odds,
                "raw_weighted_loss_delta": float(np.average(losses, weights=weights)) - clean_loss,
                "standardized_loss_delta": float(
                    np.mean(
                        [
                            np.average(losses[y[test] == label], weights=weights[y[test] == label])
                            for label in clean.classes_
                        ]
                    )
                )
                - standardized_loss,
                "conditional_empirical_mass_changed": False,
                "cause": "evaluation_prior_only_not_model_defect",
            }
        )
    records: list[dict[str, Any]] = []
    probability_buffers: dict[str, bytes] = {}
    for case in cases:
        model, inputs, training_y = copy.deepcopy(clean), test_x.copy(), y[train].copy()
        metadata = {"unused_version": 0}
        if case in ("input_sign", "double_sign", "wrong_repair"):
            inputs *= -1
        if case in ("resident_sign", "double_sign"):
            model.coef_ *= -1
        if case == "unused_metadata":
            metadata["unused_version"] = 1
        if case == "training_target_roll":
            training_y = (training_y + 1) % len(clean.classes_)
            model = _fit(train_x, training_y, seed)
        actual_changes = {
            "input": not np.array_equal(inputs, test_x),
            "coefficients": not np.array_equal(model.coef_, clean.coef_),
            "training_targets": not np.array_equal(training_y, y[train]),
            "unused_metadata": metadata["unused_version"] != 0,
        }
        expected = {
            "sham": set(),
            "input_sign": {"input"},
            "resident_sign": {"coefficients"},
            "unused_metadata": {"unused_metadata"},
            "double_sign": {"input", "coefficients"},
            "not_applied": {"input"},
            "wrong_repair": {"input"},
            "training_target_roll": {"coefficients", "training_targets"},
        }[case]
        fidelity = {name for name, changed in actual_changes.items() if changed} == expected
        tick = time.perf_counter()
        probabilities = model.predict_proba(inputs)
        predict_seconds = time.perf_counter() - tick
        loss = float(log_loss(y[test], probabilities, labels=model.classes_))
        # Repair is execution: inverse transform/state or clean-label refit.
        repaired_model, repaired_inputs = _repair_execution(
            case, model, inputs, train_x, y[train], seed
        )
        repaired = repaired_model.predict_proba(repaired_inputs)
        repair_loss = float(log_loss(y[test], repaired, labels=repaired_model.classes_))
        tick = time.perf_counter()
        prediction_view = {
            "ordered_evaluation_rows": train_test_binding(test, y[test]),
            "probabilities": _array_hash(probabilities),
            "clean_loss": clean_loss,
            "observed_loss": loss,
            "classes": clean.classes_.tolist(),
        }
        lineage_view = {
            **prediction_view,
            "actual_input": _array_hash(inputs),
            "actual_model": _model_hash(model),
            "training_targets": _array_hash(training_y),
            "reference_input": _array_hash(test_x),
            "reference_model": _model_hash(clean),
            "reference_training_targets": _array_hash(y[train]),
        }
        capture_seconds = time.perf_counter() - tick
        records.append(
            {
                "source": source,
                "seed": seed,
                "case": case,
                "split_sha256": canonical_sha256({"train": train.tolist(), "test": test.tolist()}),
                "actual_changes": actual_changes,
                "fidelity_verified": fidelity,
                "changed_training_label_fraction": float(np.mean(training_y != y[train])),
                "harmful_loss_delta": loss - clean_loss,
                "repair_residual": repair_loss - clean_loss,
                "sham_residual": sham_loss - clean_loss,
                "prediction_payload": prediction_view,
                "lineage_payload": lineage_view,
                "prediction_sha256": canonical_sha256(prediction_view),
                "lineage_sha256": canonical_sha256(lineage_view),
                "predict_seconds": predict_seconds,
                "capture_seconds": capture_seconds,
                "prior_nuisance_checks": prior_checks,
            }
        )
        probability_buffers[case] = probabilities.tobytes(order="C")
    for record in records:
        record["exact_probability_buffer_equal_to_input_sign"] = (
            probability_buffers[record["case"]] == probability_buffers["input_sign"]
        )
        views: dict[str, Any] = {}
        for view, key, payload in (
            ("prediction_only", "prediction_sha256", "prediction_payload"),
            ("configured_pipeline_lineage", "lineage_sha256", "lineage_payload"),
        ):
            if any(
                (rival[key] == record[key]) != (rival[payload] == record[payload])
                for rival in records
            ):
                raise ValueError("digest and complete observation equality differ")
            compatible = sorted(
                {
                    # wrong_repair is the same intervention, not a distinct mechanism.
                    "input_sign" if rival["case"] == "wrong_repair" else rival["case"]
                    for rival in records
                    if rival[payload] == record[payload]
                }
            )
            decision = assess_ground_truth(
                fidelity_verified=record["fidelity_verified"],
                harmful_loss_delta=record["harmful_loss_delta"],
                repair_residual=record["repair_residual"],
                sham_residual=record["sham_residual"],
                compatible_mechanisms=compatible,
            )
            views[view] = {"compatible": compatible, **asdict(decision)}
        record["views"] = views
    return records


def train_test_binding(indices: np.ndarray, labels: np.ndarray) -> str:
    return canonical_sha256({"row_order": indices.tolist(), "labels": labels.tolist()})


def sampling_checks(*, trials: int = 100) -> dict[str, Any]:
    """Known laws, including shared ancestry and iid content collisions."""
    pool = np.arange(12, dtype=float)[:, None] / 4
    equal = np.repeat(pool, 2, axis=0)
    unequal = np.concatenate((pool, pool[:4]))
    structural = {
        "uniform_duplication": {
            "tv": empirical_total_variation(pool, equal),
            "mmd2": empirical_mmd_squared(pool, equal),
        },
        "unequal_duplication": {
            "tv": empirical_total_variation(pool, unequal),
            "mmd2": empirical_mmd_squared(pool, unequal),
        },
        "weights_compensate": {
            "tv": empirical_total_variation(
                pool, unequal, target_weights=np.array([0.5] * 4 + [1.0] * 8 + [0.5] * 4)
            )
        },
    }
    records: list[dict[str, Any]] = []
    for seed in range(41000, 41000 + trials):
        streams = np.random.SeedSequence(seed).spawn(3)
        rng = np.random.default_rng(streams[0])
        iid_seed, pool_seed = (int(stream.generate_state(1)[0]) for stream in streams[1:])
        x, y = rng.normal(size=(24, 2)), rng.normal(size=(24, 2))
        origins = tuple(f"source-{index}" for index in range(24))
        targets = tuple(f"target-{index}" for index in range(24))
        for condition, target in (("iid_null", y), ("iid_shift", y + 1.5)):
            result = iid_mmd_permutation(
                x,
                target,
                source_origins=origins,
                target_origins=targets,
                independent_draws_declared=True,
                seed=iid_seed,
            )
            records.append({"seed": seed, "condition": condition, **asdict(result)})
        indices = rng.integers(0, len(pool), size=24)
        result = fixed_pool_resampling_test(
            pool, pool[indices], ancestor_indices=indices.tolist(), seed=pool_seed
        )
        guard = iid_mmd_permutation(
            pool,
            pool[indices],
            source_origins=[str(i) for i in range(len(pool))],
            target_origins=[str(i) for i in indices],
            independent_draws_declared=True,
            seed=pool_seed,
        )
        records.append(
            {
                "seed": seed,
                "condition": "fixed_pool_null",
                "tv": empirical_total_variation(pool, pool[indices]),
                **asdict(result),
            }
        )
        records.append({"seed": seed, "condition": "shared_ancestry_iid_guard", **asdict(guard)})
        biased = np.zeros(24, dtype=int)
        alternative = fixed_pool_resampling_test(
            pool, pool[biased], ancestor_indices=biased.tolist(), seed=pool_seed
        )
        records.append(
            {"seed": seed, "condition": "fixed_pool_biased_alternative", **asdict(alternative)}
        )
    return {
        "structural": structural,
        "records": records,
        "units": "100_independent_simulator_seeds_not_public_dataset_replications",
    }


def summarize(sources: list[dict[str, Any]]) -> dict[str, Any]:
    records = [record for source in sources for record in source["records"]]
    counts = {
        view: dict(Counter(record["views"][view]["status"] for record in records))
        for view in study_design()["views"]
    }
    return {
        "environment": {"numpy": np.__version__, "sklearn": sklearn.__version__},
        "planned": sum(source["planned"] for source in sources),
        "completed": len(records),
        "failures": [failure for source in sources for failure in source["failures"]],
        "by_view": counts,
        "injection_only_unsupported_positive_labels": sum(
            record["case"] != "sham"
            and not record["views"]["configured_pipeline_lineage"]["verified_consequence"]
            for record in records
        ),
        "harmful_but_ambiguous": sum(
            record["views"]["prediction_only"]["status"] == "ambiguous" for record in records
        ),
        "new_legacy_admissions": 0,
        "provider_calls": 0,
    }
