"""Owned calibrated Pipeline controls with query-relative preprocessing evidence.

The admitted source is sklearn 1.9.0, a dense finite binary StandardScaler ->
LogisticRegression Pipeline with memory=None, one FrozenEstimator and one sigmoid
calibrated pair. Serial direct delegation, an honest caller and no concurrent
mutation are assumptions. This certifies the declared effective footprint, not
arbitrary native state, host authenticity, calibration quality or general leakage.
The effective footprint includes Pipeline structure/memory, scaler parameters,
mean_/scale_/n_features_in_ and classifier parameters/coef_/intercept_/classes_/
n_features_in_. Scaler var_/n_samples_seen_ are outside this declared policy.
Approved fitted state is application policy; FrozenEstimator prevents refitting,
not mutation. Actual transformed operands can support downstream arithmetic when
preprocessing state is unavailable, but cannot establish that state's approval.
"""

from __future__ import annotations

import copy
import math
from importlib import import_module
from time import perf_counter_ns
from typing import Any, cast
from unittest.mock import patch

from aletheia_lab.evaluation.calibration_audit_source import QUERIES as QUERIES
from aletheia_lab.evaluation.calibration_audit_source import _validate_membership, membership
from aletheia_lab.evaluation.request_model_audit import digest

SCHEMA = "pipeline-native-evidence/v1"
ARMS = (
    "original",
    "overlap",
    "repair_membership",
    "preprocessing_mutation",
    "changed_preprocessing_equal_output",
    "ineffective_frozen_fit",
    "classifier_only_repair",
    "restore_state",
    "missing_preprocessing",
    "missing_preprocessing_with_transformed",
    "missing_transformed",
    "missing_association",
    "missing_membership",
    "missing_closure",
    "misjoin",
    "misassociated_transform",
    "native_failure",
    "wrong_probability_report",
)
FORECASTS = {
    "original": ["compliant", "compliant", "compliant"],
    "overlap": ["compliant", "violation", "compliant"],
    "repair_membership": ["compliant", "compliant", "compliant"],
    "preprocessing_mutation": ["violation", "compliant", "compliant"],
    "changed_preprocessing_equal_output": ["violation", "compliant", "compliant"],
    "ineffective_frozen_fit": ["violation", "compliant", "compliant"],
    "classifier_only_repair": ["violation", "compliant", "compliant"],
    "restore_state": ["compliant", "compliant", "compliant"],
    "missing_preprocessing": ["unknown", "compliant", "unknown"],
    "missing_preprocessing_with_transformed": ["unknown", "compliant", "compliant"],
    "missing_transformed": ["violation", "compliant", "compliant"],
    "missing_association": ["unknown", "compliant", "unknown"],
    "missing_membership": ["compliant", "unknown", "compliant"],
    "missing_closure": ["unknown", "unknown", "unknown"],
    "misjoin": ["conflict", "conflict", "conflict"],
    "misassociated_transform": ["compliant", "compliant", "conflict"],
    "native_failure": ["unknown", "unknown", "unknown"],
    "wrong_probability_report": ["compliant", "compliant", "violation"],
}


class _EvidenceConflict(ValueError):
    """Two purported observations do not describe one admitted computation."""


def _matrix(value: Any, features: int) -> Any:
    np = import_module("numpy")
    array = np.asarray(value, dtype=float)
    if array.ndim != 2 or array.shape[1] != features or not np.isfinite(array).all():
        raise ValueError("dense finite input/operand footprint required")
    return array


def _validate_state(actual: dict[str, Any], *, complete: bool = False) -> None:
    np = import_module("numpy")
    if (actual["class"], actual["steps"], actual["memory"]) != (
        "Pipeline",
        ["scale", "classifier"],
        None,
    ):
        raise ValueError("declared Pipeline with memory=None required")
    classifier = actual["classifier"]
    features = classifier["features"]
    if (
        classifier["class"] != "LogisticRegression"
        or classifier["classes"] != [0, 1]
        or type(features) is not int
        or features < 1
        or np.asarray(classifier["coef"]).shape != (1, features)
        or np.asarray(classifier["intercept"]).shape != (1,)
        or not np.isfinite(classifier["coef"]).all()
        or not np.isfinite(classifier["intercept"]).all()
    ):
        raise ValueError("finite binary linear classifier footprint required")
    scaler = actual.get("preprocessing")
    if scaler is None:
        if complete:
            raise ValueError("complete enrolled preprocessing footprint required")
        return
    if (
        scaler["class"] != "StandardScaler"
        or scaler["params"] != {"copy": True, "with_mean": True, "with_std": True}
        or scaler["features"] != features
        or np.asarray(scaler["mean"]).shape != (features,)
        or np.asarray(scaler["scale"]).shape != (features,)
        or not np.isfinite(scaler["mean"]).all()
        or not np.isfinite(scaler["scale"]).all()
        or not (np.asarray(scaler["scale"]) > 0).all()
    ):
        raise ValueError("finite centered/scaled preprocessing footprint required")


def state(base: Any) -> dict[str, Any]:
    """Effective dense binary footprint; var_/n_samples_seen_ are not queried."""
    pipeline = import_module("sklearn.pipeline")
    preprocessing, linear = (
        import_module("sklearn.preprocessing"),
        import_module("sklearn.linear_model"),
    )
    if (
        type(base) is not pipeline.Pipeline
        or base.memory is not None
        or [name for name, _ in base.steps] != ["scale", "classifier"]
    ):
        raise ValueError("declared native Pipeline with memory=None required")
    scaler, classifier = base.named_steps["scale"], base.named_steps["classifier"]
    if (
        type(scaler) is not preprocessing.StandardScaler
        or type(classifier) is not linear.LogisticRegression
    ):
        raise ValueError("declared native scaler and classifier required")
    result = {
        "class": "Pipeline",
        "steps": ["scale", "classifier"],
        "memory": None,
        "preprocessing": {
            "class": "StandardScaler",
            "params": scaler.get_params(deep=False),
            "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(),
            "features": int(scaler.n_features_in_),
        },
        "classifier": {
            "class": "LogisticRegression",
            "params": classifier.get_params(deep=False),
            "coef": classifier.coef_.tolist(),
            "intercept": classifier.intercept_.tolist(),
            "classes": classifier.classes_.tolist(),
            "features": int(classifier.n_features_in_),
        },
    }
    _validate_state(result, complete=True)
    return result


def _arithmetic_inputs(call: dict[str, Any]) -> Any:
    np = import_module("numpy")
    actual = call["actual_state"]
    _validate_state(actual)
    features = actual["classifier"]["features"]
    inputs = _matrix(call["input"], features)
    scaler, witness = actual.get("preprocessing"), call.get("transform_return")
    reconstructed = None
    if scaler is not None:
        reconstructed = (inputs - np.asarray(scaler["mean"])) / np.asarray(scaler["scale"])
        _matrix(reconstructed, features)
    if witness is None:
        if reconstructed is None:
            raise ValueError("preprocessing footprint or actual transformed operand required")
        return reconstructed
    if (
        witness["token"] != call["token"]
        or witness["step"] != "scale"
        or witness["status"] != "returned"
        or witness["input"] != call["input"]
        or witness["held_scaler_is_enrolled"] is not True
    ):
        raise _EvidenceConflict("transform witness association differs")
    transformed = _matrix(witness["output"], features)
    if transformed.shape != inputs.shape:
        raise ValueError("transformed operand census differs")
    if reconstructed is not None and not np.allclose(
        reconstructed, transformed, rtol=1e-12, atol=1e-14
    ):
        raise _EvidenceConflict("preprocessing state and actual transformed operand differ")
    return transformed


def arithmetic(call: dict[str, Any]) -> list[list[float]]:
    """Downstream sigmoid arithmetic, using state or an actual delegated Z return."""
    np, special = import_module("numpy"), import_module("scipy.special")
    transformed = _arithmetic_inputs(call)
    classifier, sigmoid = call["actual_state"]["classifier"], call["sigmoid"]
    if not all(math.isfinite(sigmoid[key]) for key in ("a", "b")):
        raise ValueError("finite sigmoid parameters required")
    margin = (transformed @ np.asarray(classifier["coef"]).T).ravel()
    margin += classifier["intercept"][0]
    if not np.isfinite(margin).all():
        raise ValueError("finite downstream margins required")
    positive = special.expit(-(sigmoid["a"] * margin + sigmoid["b"]))
    return cast(list[list[float]], np.column_stack((1 - positive, positive)).tolist())


def _arithmetic_answer(call: dict[str, Any]) -> str:
    actual = call["actual_state"]
    if call.get("sigmoid") is None or (
        actual.get("preprocessing") is None and call.get("transform_return") is None
    ):
        return "unknown"
    try:
        expected = arithmetic(call)
    except _EvidenceConflict:
        return "conflict"
    np = import_module("numpy")
    observed = _matrix(call["output"], 2)
    if observed.shape != (len(expected), 2):
        raise ValueError("probability evidence census differs")
    return "compliant" if np.allclose(expected, observed, rtol=1e-10, atol=1e-12) else "violation"


def assess(packet: dict[str, Any]) -> dict[str, str]:
    """Three successful-call predicates; missing preprocessing is query-relative."""
    if packet.get("schema") != SCHEMA:
        raise ValueError("unsupported Pipeline evidence")
    call = packet["call"]
    if any(type(call[key]) is not bool for key in ("closed", "failed")):
        raise ValueError("explicit call closure/failure markers required")
    if call["token"] != packet["token"]:
        return dict.fromkeys(QUERIES, "conflict")
    if not call["closed"] or call["failed"] or call["output"] is None:
        return dict.fromkeys(QUERIES, "unknown")
    if call["held_base_is_enrolled"] is not True or call["state_stable"] is not True:
        return dict.fromkeys(QUERIES, "conflict")
    result = dict.fromkeys(QUERIES, "unknown")
    actual = call.get("actual_state")
    _validate_state(packet["approved_state"], complete=True)
    if actual is not None:
        _validate_state(actual)
        if actual["classifier"] != packet["approved_state"]["classifier"]:
            result["authorized_state"] = "violation"
        elif actual.get("preprocessing") is not None:
            result["authorized_state"] = (
                "compliant" if actual == packet["approved_state"] else "violation"
            )
        result["sigmoid_arithmetic"] = _arithmetic_answer(call)
    if packet.get("membership") is not None:
        members = packet["membership"]
        _validate_membership(members)
        result["disjoint_membership"] = _separation(members)
    return result


def _separation(members: dict[str, Any]) -> str:
    return (
        "violation"
        if any(
            {row[key] for row in members["base"]} & {row[key] for row in members["calibration"]}
            for key in ("id", "sha256")
        )
        else "compliant"
    )


def setup(seed: int, *, overlap: bool = False) -> dict[str, Any]:
    """Fit owned generated raw arrays; scaler and classifier share base membership."""
    if import_module("sklearn").__version__ != "1.9.0":
        raise ValueError("pinned sklearn 1.9.0 source required")
    datasets, pipelines = import_module("sklearn.datasets"), import_module("sklearn.pipeline")
    preprocessing, linear = (
        import_module("sklearn.preprocessing"),
        import_module("sklearn.linear_model"),
    )
    calibration, frozen = import_module("sklearn.calibration"), import_module("sklearn.frozen")
    x, y = datasets.make_classification(
        n_samples=240, n_features=6, n_informative=4, n_redundant=0, random_state=seed
    )
    base_ids, cal_ids = list(range(80)), list(range(40, 120) if overlap else range(80, 160))
    base = pipelines.Pipeline(
        [
            ("scale", preprocessing.StandardScaler()),
            (
                "classifier",
                linear.LogisticRegression(solver="liblinear", random_state=0, max_iter=1000),
            ),
        ],
        memory=None,
    )
    base.fit(x[base_ids], y[base_ids])
    approved = state(base)
    ledger = [
        {
            "api": "pipeline.fit",
            "members": base_ids,
            "input": x[base_ids].tolist(),
            "labels": y[base_ids].tolist(),
            "returned_state": approved,
            "status": "returned",
        }
    ]
    wrapped = frozen.FrozenEstimator(base)
    model = calibration.CalibratedClassifierCV(wrapped, method="sigmoid", cv=3, n_jobs=1)
    model.fit(x[cal_ids], y[cal_ids])
    sigmoid = model.calibrated_classifiers_[0].calibrators[0]
    ledger.append(
        {
            "api": "calibrator.fit",
            "members": cal_ids,
            "input": x[cal_ids].tolist(),
            "labels": y[cal_ids].tolist(),
            "returned_sigmoid": {"a": float(sigmoid.a_), "b": float(sigmoid.b_)},
            "status": "returned",
        }
    )
    query = x[160:176].copy()
    query[:, -1] = approved["preprocessing"]["mean"][-1]
    return {
        "base": base,
        "wrapper": wrapped,
        "model": model,
        "approved": approved,
        "base_ids": base_ids,
        "cal_ids": cal_ids,
        "ledger": ledger,
        "query": query,
        "x": x,
        "y": y,
        "top_level_predictions": 0,
        "last_prediction_ns": 0,
    }


def predict(session: dict[str, Any], token: str, *, invalid: bool = False) -> dict[str, Any]:
    """Capture the active scaler return inside one serial delegated prediction."""
    model = session["model"]
    if len(model.calibrated_classifiers_) != 1 or model.method != "sigmoid":
        raise ValueError("single sigmoid calibrated pair required")
    pair = model.calibrated_classifiers_[0]
    if pair.estimator is not session["wrapper"] or pair.estimator.estimator is not session["base"]:
        raise ValueError("enrolled FrozenEstimator/Pipeline delegation differs")
    base = pair.estimator.estimator
    before, scaler = state(base), base.named_steps["scale"]
    query = session["query"][:, :-1] if invalid else session["query"]
    sigmoid = pair.calibrators[0]
    call = {
        "token": token,
        "input": query.tolist(),
        "actual_state": before,
        "sigmoid": {"a": float(sigmoid.a_), "b": float(sigmoid.b_)},
        "held_base_is_enrolled": base is session["base"],
        "closed": False,
        "failed": False,
        "output": None,
        "transform_return": None,
    }
    native_transform, returned = scaler.transform, []

    def capture_transform(*args: Any, **kwargs: Any) -> Any:
        inputs = args[0].tolist()
        transformed = native_transform(*args, **kwargs)
        returned.append(
            {
                "token": token,
                "step": "scale",
                "status": "returned",
                "input": inputs,
                "output": transformed.tolist(),
                "held_scaler_is_enrolled": scaler is session["base"].named_steps["scale"],
            }
        )
        return transformed

    started = perf_counter_ns()
    session["top_level_predictions"] += 1
    with patch.object(scaler, "transform", capture_transform):
        try:
            call["output"] = model.predict_proba(query).tolist()
        except (ValueError, RuntimeError) as exc:
            call.update(failed=True, error_type=type(exc).__name__)
    session["last_prediction_ns"] = perf_counter_ns() - started
    call.update(closed=True, state_stable=state(base) == before)
    if not call["state_stable"] or len(returned) > 1 or (not call["failed"] and len(returned) != 1):
        raise ValueError("serial stable transform/delegation premise differs")
    call["transform_return"] = returned[0] if returned else None
    return {
        "schema": SCHEMA,
        "token": token,
        "approved_state": copy.deepcopy(session["approved"]),
        "membership": {
            "base": membership(session, session["base_ids"]),
            "calibration": membership(session, session["cal_ids"]),
        },
        "call": call,
    }


def intervene(session: dict[str, Any], arm: str) -> None:
    """Owned changes after fitting; frozen fit is an attempted no-op repair."""
    if arm == "repair_membership":
        session["cal_ids"] = list(range(80, 160))
        session["model"].fit(session["x"][session["cal_ids"]], session["y"][session["cal_ids"]])
        sigmoid = session["model"].calibrated_classifiers_[0].calibrators[0]
        session["ledger"].append(
            {
                "api": "calibrator.fit",
                "members": session["cal_ids"],
                "input": session["x"][session["cal_ids"]].tolist(),
                "labels": session["y"][session["cal_ids"]].tolist(),
                "returned_sigmoid": {"a": float(sigmoid.a_), "b": float(sigmoid.b_)},
                "status": "returned",
            }
        )
    scaler = session["base"].named_steps["scale"]
    classifier = session["base"].named_steps["classifier"]
    if arm in {"changed_preprocessing_equal_output", "ineffective_frozen_fit"}:
        scaler.scale_[-1] *= 1.5
    if arm in {
        "preprocessing_mutation",
        "classifier_only_repair",
        "restore_state",
        "missing_preprocessing",
        "missing_preprocessing_with_transformed",
        "missing_transformed",
    }:
        scaler.mean_[0] += 0.75 * scaler.scale_[0]
        scaler.scale_[1] *= 1.5
    if arm in {"classifier_only_repair", "restore_state"}:
        classifier.coef_[0, 2] += 0.5
        classifier.coef_[:] = session["approved"]["classifier"]["coef"]
        classifier.intercept_[:] = session["approved"]["classifier"]["intercept"]
    if arm == "restore_state":
        scaler.mean_[:] = session["approved"]["preprocessing"]["mean"]
        scaler.scale_[:] = session["approved"]["preprocessing"]["scale"]
    if arm == "ineffective_frozen_fit":
        session["wrapper"].fit(session["x"][80:160], session["y"][80:160])
        session["ledger"].append(
            {
                "api": "frozen.fit",
                "input": session["x"][80:160].tolist(),
                "labels": session["y"][80:160].tolist(),
                "status": "returned_noop",
                "returned_state": state(session["base"]),
            }
        )


def execute(arm: str, seed: int) -> dict[str, Any]:
    """Native lifecycle counterpairs and authored partial/incorrect reporting."""
    if arm not in ARMS:
        raise ValueError("unknown Pipeline arm")
    session = setup(seed, overlap=arm in {"overlap", "repair_membership"})
    before = predict(session, "before")
    intervene(session, arm)
    native = predict(session, "request", invalid=arm == "native_failure")
    if arm == "wrong_probability_report":
        native["call"]["native_probability_output"] = copy.deepcopy(native["call"]["output"])
        native["call"]["output"] = [list(reversed(row)) for row in native["call"]["output"]]
    captured = copy.deepcopy(native)
    if arm in {"missing_preprocessing", "missing_preprocessing_with_transformed"}:
        captured["call"]["actual_state"]["preprocessing"] = None
    if arm in {"missing_preprocessing", "missing_transformed"}:
        captured["call"]["transform_return"] = None
    if arm == "missing_association":
        captured["call"]["actual_state"] = None
    elif arm == "missing_membership":
        captured["membership"] = None
    elif arm == "missing_closure":
        captured["call"]["closed"] = False
    elif arm == "misjoin":
        captured["call"]["token"] = "foreign-request"
    elif arm == "misassociated_transform":
        captured["call"]["transform_return"]["token"] = "foreign-request"
    answers = assess(captured)
    row = {
        "arm": arm,
        "seed": seed,
        "fit_call_ledger": session["ledger"],
        "native_packets": [before, native],
        "captured_packet": captured,
        "captured_answers": answers,
        "forecast": dict(zip(QUERIES, FORECASTS[arm], strict=True)),
        "forecast_supported": list(answers.values()) == FORECASTS[arm],
        "same_entire_probability_output": before["call"]["output"] == native["call"]["output"],
        "native_top_level_predictions": session["top_level_predictions"],
        "native_failed_predictions": int(native["call"]["failed"]),
        "packet_sha256": digest(captured),
        "scope": "owned generated-data control; declared dense binary Pipeline and honest host",
    }
    row["ordinary_complete_history"] = history_reference(row)
    return row


def _history_membership(row: dict[str, Any]) -> dict[str, Any]:
    ledger = row["fit_call_ledger"]
    base = next(item for item in ledger if item["api"] == "pipeline.fit")
    cal = [item for item in ledger if item["api"] == "calibrator.fit"][-1]
    result = {}
    for name, record in (("base", base), ("calibration", cal)):
        result[name] = [
            {"id": i, "sha256": digest([x, y])}
            for i, x, y in zip(record["members"], record["input"], record["labels"], strict=True)
        ]
    return result


def historical_membership(row: dict[str, Any]) -> str:
    """Actual fit-array separation remains answerable after prediction failure."""
    members = _history_membership(row)
    shared_ids = {item["id"] for item in members["base"]} & {
        item["id"] for item in members["calibration"]
    }
    shared_rows = {item["sha256"] for item in members["base"]} & {
        item["sha256"] for item in members["calibration"]
    }
    return "violation" if shared_ids or shared_rows else "compliant"


def history_reference(row: dict[str, Any]) -> dict[str, str]:
    """Independent scalar replay of full caller/history, not assess/arithmetic."""
    ledger, packet = row["fit_call_ledger"], row["native_packets"][-1]
    base = next(item for item in ledger if item["api"] == "pipeline.fit")
    cal = [item for item in ledger if item["api"] == "calibrator.fit"][-1]
    if (
        packet["membership"] != _history_membership(row)
        or packet["approved_state"] != base["returned_state"]
    ):
        raise ValueError("native capsule differs from actual fit history")
    call = packet["call"]
    if call["token"] != packet["token"]:
        raise ValueError("native call history association differs")
    if not call["closed"] or call["failed"] or call["output"] is None:
        return dict.fromkeys(QUERIES, "unknown")
    if (
        call["sigmoid"] != cal["returned_sigmoid"]
        or not call["state_stable"]
        or not call["held_base_is_enrolled"]
    ):
        raise ValueError("calibrator/delegation history differs")
    actual, witness = call["actual_state"], call["transform_return"]
    scaler, classifier = actual["preprocessing"], actual["classifier"]
    if (
        witness["token"] != call["token"]
        or witness["step"] != "scale"
        or witness["status"] != "returned"
        or witness["input"] != call["input"]
        or not witness["held_scaler_is_enrolled"]
    ):
        raise ValueError("native transform history association differs")
    probabilities = []
    for features, observed_z in zip(call["input"], witness["output"], strict=True):
        transformed = [
            (x - mean) / scale
            for x, mean, scale in zip(features, scaler["mean"], scaler["scale"], strict=True)
        ]
        if any(
            not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-14)
            for a, b in zip(transformed, observed_z, strict=True)
        ):
            raise ValueError("native transformed operand differs from scalar preprocessing")
        margin = sum(w * x for w, x in zip(classifier["coef"][0], transformed, strict=True))
        margin += classifier["intercept"][0]
        z = -(call["sigmoid"]["a"] * margin + call["sigmoid"]["b"])
        positive = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
        probabilities.append([1 - positive, positive])

    def matches(values: list[list[float]]) -> bool:
        return len(values) == len(probabilities) and all(
            len(observed) == 2
            and all(
                math.isfinite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
                for a, b in zip(expected, observed, strict=True)
            )
            for expected, observed in zip(probabilities, values, strict=True)
        )

    if "native_probability_output" in call and not matches(call["native_probability_output"]):
        raise ValueError("recorded native arithmetic differs")
    return {
        "authorized_state": "compliant" if actual == base["returned_state"] else "violation",
        "disjoint_membership": historical_membership(row),
        "sigmoid_arithmetic": "compliant" if matches(call["output"]) else "violation",
    }
