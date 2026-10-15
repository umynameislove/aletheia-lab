"""Actual local probe operations for a separate, bounded diagnosis study.

The selector observes only initial measurements and probe receipts. Evaluator
references are deliberately separate. Every repair branch starts from the same
faulted snapshot; an effective branch is not a certificate of a unique cause.
This module neither changes historical A3 nor executes a language model.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
from sklearn.datasets import load_breast_cancer, load_iris  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import log_loss  # type: ignore[import-untyped]
from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.project.identity import content_sha256

PROBES: Final = (
    "read_input",
    "read_model",
    "read_training",
    "restore_input",
    "restore_model",
    "refit",
)
CONDITIONS: Final = (
    "healthy",
    "input_sign",
    "resident_sign",
    "target_roll",
    "double_sign",
    "input_permutation",
    "resident_permutation",
    "intercept_offset",
)
SOURCES: Final = ("iris", "breast_cancer")
MINIMUM_HARM: Final = 0.01
RESTORATION_TOLERANCE: Final = 1e-10
INTERCEPT_OFFSET: Final = 3.0


def _finite_array(values: np.ndarray, *, dimensions: int) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != dimensions or min(result.shape) == 0 or not np.isfinite(result).all():
        raise ValueError("native state requires non-empty finite aligned arrays")
    return result


def _array_hash(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values, dtype="<f8")
    if array.ndim not in (1, 2) or min(array.shape) == 0 or not np.isfinite(array).all():
        raise ValueError("native state hash requires a non-empty finite vector or matrix")
    return content_sha256(str(array.shape).encode("ascii") + array.tobytes(order="C"))


def _model_hash(model: LogisticRegression) -> str:
    coefficients = _finite_array(model.coef_, dimensions=2)
    intercept = _finite_array(model.intercept_, dimensions=1)
    classes = _finite_array(model.classes_, dimensions=1)
    expected_rows = 1 if len(classes) == 2 else len(classes)
    if (
        len(classes) < 2
        or coefficients.shape[0] != expected_rows
        or len(intercept) != expected_rows
    ):
        raise ValueError("native resident model arrays do not align")
    return canonical_sha256(
        {
            "coefficients": _array_hash(coefficients),
            "intercept": _array_hash(intercept),
            "classes": _array_hash(classes),
        }
    )


def _array_descriptor(values: np.ndarray) -> dict[str, Any]:
    """Describe actual finite buffer operands without storing their raw bytes."""
    array = np.asarray(values)
    _finite_array(array, dimensions=array.ndim)
    if array.ndim not in (1, 2):
        raise ValueError("native operand must be a vector or matrix")
    return {
        "shape": list(array.shape),
        "dtype": array.dtype.str,
        "bytes": int(array.nbytes),
        "sha256": _array_hash(array),
    }


def _model_descriptors(model: LogisticRegression) -> dict[str, dict[str, Any]]:
    _model_hash(model)
    return {
        "coefficients": _array_descriptor(model.coef_),
        "intercept": _array_descriptor(model.intercept_),
        "classes": _array_descriptor(model.classes_),
    }


def _descriptor_bytes(descriptors: dict[str, dict[str, Any]]) -> int:
    return sum(int(item["bytes"]) for item in descriptors.values())


def _fit(inputs: np.ndarray, targets: np.ndarray, seed: int) -> LogisticRegression:
    x = _finite_array(inputs, dimensions=2)
    y = _finite_array(targets, dimensions=1)
    if len(x) != len(y) or len(np.unique(y)) < 2 or not np.array_equal(y, np.floor(y)):
        raise ValueError("native training inputs and categorical targets do not align")
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000, random_state=seed)
    with threadpool_limits(limits=1):
        model.fit(x, y.astype(int))
    if np.max(model.n_iter_) >= 2000:
        raise ValueError("native model failed to converge within the fixed fit budget")
    _model_hash(model)
    return model


def _prediction(
    model: LogisticRegression, inputs: np.ndarray, targets: np.ndarray
) -> tuple[np.ndarray, float]:
    x = _finite_array(inputs, dimensions=2)
    y = _finite_array(targets, dimensions=1)
    _model_hash(model)
    if len(x) != len(y) or x.shape[1] != model.coef_.shape[1]:
        raise ValueError("native prediction inputs and targets do not align")
    with threadpool_limits(limits=1):
        probabilities = _finite_array(model.predict_proba(x), dimensions=2)
    if (
        probabilities.shape != (len(x), len(model.classes_))
        or (probabilities < 0).any()
        or (probabilities > 1).any()
        or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
    ):
        raise ValueError("native probability matrix is invalid")
    loss = float(log_loss(y.astype(int), probabilities, labels=model.classes_))
    if not np.isfinite(loss):
        raise ValueError("native prediction loss must be finite")
    return probabilities, loss


def _freeze_arrays(*arrays: np.ndarray) -> None:
    for array in arrays:
        array.flags.writeable = False


def _freeze_model(model: LogisticRegression) -> None:
    _freeze_arrays(model.coef_, model.intercept_, model.classes_)


@dataclass(frozen=True, slots=True, repr=False)
class NativeCase:
    """Faulted snapshot with a small legal probe alphabet and private evaluator state.

    ``reference`` is evaluator-only and must never enter selector context.
    Snapshot restoration proves a controlled effect, not deployment recovery.
    """

    _source: str
    _seed: int
    _condition: str
    _source_sha256: str
    _split_sha256: str
    _train_inputs: np.ndarray
    _clean_targets: np.ndarray
    _training_targets: np.ndarray
    _clean_inputs: np.ndarray
    _inputs: np.ndarray
    _evaluation_targets: np.ndarray
    _clean_model: LogisticRegression
    _model: LogisticRegression
    _clean_loss: float
    _fault_loss: float
    _probability_sha256: str

    def initial(self) -> dict[str, Any]:
        """Return the complete initial selector view, with no hidden identifiers."""
        return {
            "loss": self._fault_loss,
            "clean_loss": self._clean_loss,
            "harmful": self._fault_loss - self._clean_loss >= MINIMUM_HARM,
            "probability_sha256": self._probability_sha256,
        }

    def _state_hashes(
        self, model: LogisticRegression, inputs: np.ndarray, targets: np.ndarray
    ) -> dict[str, str]:
        return {
            "input": _array_hash(inputs),
            "model": _model_hash(model),
            "training": _array_hash(targets),
        }

    def _reference_hashes(self) -> dict[str, str]:
        return self._state_hashes(self._clean_model, self._clean_inputs, self._clean_targets)

    def probe(
        self,
        action: str,
        *,
        recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Execute a legal read or a fresh, non-mutating restoration branch.

        The value is intentionally coarse. Evidence digests bind the operation's
        actual observations, excluding timing and evaluator-only truth.
        A trusted evaluator may retain a copy through ``recorder``; selectors
        must not supply callbacks or receive the full recorded evidence. Buffer
        byte counts describe operands, not all solver/cache memory traffic.
        """
        if not isinstance(action, str) or action not in PROBES:
            raise ValueError("native probe action is outside the legal alphabet")
        if recorder is not None and not callable(recorder):
            raise ValueError("native recorder must be a trusted evaluator callback")
        started = time.perf_counter()
        if action.startswith("read_"):
            current, reference, actual_arrays, enrolled_arrays = self._read_evidence(action)
            value = "same" if current == reference else "changed"
            evidence: dict[str, Any] = {
                "action": action,
                "actual_sha256": current,
                "enrolled_sha256": reference,
                "value": value,
                "actual_arrays": actual_arrays,
                "enrolled_arrays": enrolled_arrays,
                "actual_read_bytes": _descriptor_bytes(actual_arrays),
                "enrolled_read_bytes": _descriptor_bytes(enrolled_arrays),
                "fit_count": 0,
                "prediction_count": 0,
            }
        else:
            model, inputs, targets = self._model, self._inputs, self._training_targets
            if action == "restore_input":
                inputs = self._clean_inputs.copy()
            elif action == "restore_model":
                model = copy.deepcopy(self._clean_model)
            else:
                targets = self._clean_targets.copy()
                model = _fit(self._train_inputs, targets, self._seed)
            probabilities, loss = _prediction(model, inputs, self._evaluation_targets)
            value = (
                "restored"
                if abs(loss - self._clean_loss) <= RESTORATION_TOLERANCE
                else "not_restored"
            )
            actual_hashes = self._state_hashes(model, inputs, targets)
            enrolled_hashes = self._reference_hashes()
            model_arrays = _model_descriptors(model)
            branch_arrays = {
                "input": _array_descriptor(inputs),
                "training_targets": _array_descriptor(targets),
                "evaluation_targets": _array_descriptor(self._evaluation_targets),
                "probabilities": _array_descriptor(probabilities),
                **model_arrays,
            }
            model_bytes = _descriptor_bytes(model_arrays)
            replacement_bytes = (
                int(inputs.nbytes)
                if action == "restore_input"
                else model_bytes
                if action == "restore_model"
                else int(targets.nbytes)
            )
            evidence = {
                "action": action,
                "value": value,
                "loss": loss,
                "clean_loss": self._clean_loss,
                "probability_sha256": _array_hash(probabilities),
                "actual_state_sha256": actual_hashes,
                "enrolled_state_sha256": enrolled_hashes,
                "functional_restored": value == "restored",
                "state_restored": actual_hashes == enrolled_hashes,
                "branch_arrays": branch_arrays,
                "prediction_input_bytes": int(inputs.nbytes),
                "prediction_model_bytes": model_bytes,
                "evaluation_target_bytes": int(self._evaluation_targets.nbytes),
                "prediction_output_bytes": int(probabilities.nbytes),
                "replacement_bytes": replacement_bytes,
                "training_bytes": (
                    int(self._train_inputs.nbytes + targets.nbytes) if action == "refit" else 0
                ),
                "training_input": (
                    _array_descriptor(self._train_inputs) if action == "refit" else None
                ),
                "fit_count": int(action == "refit"),
                "prediction_count": 1,
            }
        evidence_sha256 = canonical_sha256(evidence)
        if recorder is not None:
            recorder(copy.deepcopy(evidence))
        seconds = time.perf_counter() - started
        if not np.isfinite(seconds) or seconds < 0:
            raise ValueError("native probe duration must be nonnegative and finite")
        return {"value": value, "seconds": seconds, "evidence_sha256": evidence_sha256}

    def _read_evidence(
        self, action: str
    ) -> tuple[str, str, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        if action == "read_input":
            actual_arrays = {"input": _array_descriptor(self._inputs)}
            enrolled_arrays = {"input": _array_descriptor(self._clean_inputs)}
            return (
                _array_hash(self._inputs),
                _array_hash(self._clean_inputs),
                actual_arrays,
                enrolled_arrays,
            )
        if action == "read_model":
            return (
                _model_hash(self._model),
                _model_hash(self._clean_model),
                _model_descriptors(self._model),
                _model_descriptors(self._clean_model),
            )
        return (
            _array_hash(self._training_targets),
            _array_hash(self._clean_targets),
            {"training_targets": _array_descriptor(self._training_targets)},
            {"training_targets": _array_descriptor(self._clean_targets)},
        )

    def reference(self) -> dict[str, Any]:
        """Return evaluator truth; the study must keep this outside selector views."""
        actual = self._state_hashes(self._model, self._inputs, self._training_targets)
        enrolled = self._reference_hashes()
        changes = {site: actual[site] != enrolled[site] for site in actual}
        expected = {
            "healthy": set(),
            "input_sign": {"input"},
            "resident_sign": {"model"},
            "target_roll": {"model", "training"},
            "double_sign": {"input", "model"},
            "input_permutation": {"input"},
            "resident_permutation": {"model"},
            "intercept_offset": {"model"},
        }[self._condition]
        sites = {site for site, changed in changes.items() if changed}
        fidelity = sites == expected
        return {
            "condition": self._condition,
            "source": self._source,
            "seed": self._seed,
            "sites": sorted(sites),
            "expected_sites": sorted(expected),
            "actual_changes": changes,
            "actual_applied": bool(expected) and fidelity,
            "fidelity_verified": fidelity,
            "clean_loss": self._clean_loss,
            "fault_loss": self._fault_loss,
            "harmful_loss_delta": self._fault_loss - self._clean_loss,
            "harmful": self._fault_loss - self._clean_loss >= MINIMUM_HARM,
            "actual_state_sha256": actual,
            "enrolled_state_sha256": enrolled,
            "source_sha256": self._source_sha256,
            "split_sha256": self._split_sha256,
            "probability_sha256": self._probability_sha256,
            "train_rows": len(self._train_inputs),
            "evaluation_rows": len(self._inputs),
            "feature_count": self._inputs.shape[1],
            "scope": "controlled_snapshot_not_global_cause_uniqueness",
        }

    def compensation_control(self) -> dict[str, Any]:
        """Actually toggle each sign, showing recovery need not localize cause."""
        results: dict[str, Any] = {}
        for action in ("toggle_input_sign", "toggle_model_sign"):
            started = time.perf_counter()
            model, inputs = self._model, self._inputs
            if action == "toggle_input_sign":
                inputs = -self._inputs
            else:
                model = copy.deepcopy(self._model)
                model.coef_ = -model.coef_
            probabilities, loss = _prediction(model, inputs, self._evaluation_targets)
            actual = self._state_hashes(model, inputs, self._training_targets)
            record = {
                "action": action,
                "loss": loss,
                "functional_restored": abs(loss - self._clean_loss) <= RESTORATION_TOLERANCE,
                "state_restored": actual == self._reference_hashes(),
                "probability_sha256": _array_hash(probabilities),
                "actual_state_sha256": actual,
            }
            results[action] = {
                **record,
                "evidence_sha256": canonical_sha256(record),
                "seconds": time.perf_counter() - started,
            }
        return results


def create_case(source: str, seed: int, condition: str) -> NativeCase:
    """Fit a local enrolled reference and apply a declared new-method stressor."""
    if not isinstance(source, str) or source not in SOURCES:
        raise ValueError("native source is outside the declared local source set")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("native seed must be an integer in the uint32 range")
    if not isinstance(condition, str) or condition not in CONDITIONS:
        raise ValueError("native condition is outside the declared stressor set")
    dataset = load_iris() if source == "iris" else load_breast_cancer()
    x = _finite_array(np.asarray(dataset.data), dimensions=2)
    y = _finite_array(np.asarray(dataset.target), dimensions=1).astype(int)
    train, test = train_test_split(np.arange(len(y)), test_size=0.4, stratify=y, random_state=seed)
    with threadpool_limits(limits=1):
        scaler = StandardScaler().fit(x[train])
        train_inputs = _finite_array(scaler.transform(x[train]), dimensions=2)
        clean_inputs = _finite_array(scaler.transform(x[test]), dimensions=2)
    clean_targets, evaluation_targets = y[train].copy(), y[test].copy()
    clean_model = _fit(train_inputs, clean_targets, seed)
    _, clean_loss = _prediction(clean_model, clean_inputs, evaluation_targets)
    model, inputs, training_targets = (
        copy.deepcopy(clean_model),
        clean_inputs.copy(),
        clean_targets.copy(),
    )
    if condition in ("input_sign", "double_sign"):
        inputs *= -1
    if condition in ("resident_sign", "double_sign"):
        model.coef_ *= -1
    if condition == "target_roll":
        training_targets = (training_targets + 1) % len(clean_model.classes_)
        model = _fit(train_inputs, training_targets, seed)
    if condition == "input_permutation":
        inputs = np.roll(inputs, 1, axis=1)
    if condition == "resident_permutation":
        model.coef_ = np.roll(model.coef_, -1, axis=1)
    if condition == "intercept_offset":
        model.intercept_[0] += INTERCEPT_OFFSET
    probabilities, fault_loss = _prediction(model, inputs, evaluation_targets)
    _freeze_arrays(
        train_inputs, clean_targets, training_targets, clean_inputs, inputs, evaluation_targets
    )
    _freeze_model(clean_model)
    _freeze_model(model)
    return NativeCase(
        _source=source,
        _seed=seed,
        _condition=condition,
        _source_sha256=canonical_sha256({"inputs": _array_hash(x), "targets": _array_hash(y)}),
        _split_sha256=canonical_sha256({"train": train.tolist(), "test": test.tolist()}),
        _train_inputs=train_inputs,
        _clean_targets=clean_targets,
        _training_targets=training_targets,
        _clean_inputs=clean_inputs,
        _inputs=inputs,
        _evaluation_targets=evaluation_targets,
        _clean_model=clean_model,
        _model=model,
        _clean_loss=clean_loss,
        _fault_loss=fault_loss,
        _probability_sha256=_array_hash(probabilities),
    )
