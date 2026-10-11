"""Query-relative evidence at a pretrained classifier/calibrator boundary.

The sample-disjointness requirement comes from scikit-learn's calibration API.
Fixed fitted-state authorization is an explicit application policy, not a promise
that FrozenEstimator makes its wrapped object immutable. All controls are owned.
"""

from __future__ import annotations

import copy
from importlib import import_module
from time import perf_counter_ns
from typing import Any, cast

from aletheia_lab.evaluation.request_model_audit import digest

ARMS = (
    "original",
    "overlap",
    "repair_membership",
    "changed_state_equal_output",
    "ineffective_frozen_fit",
    "restore_state",
    "missing_association",
    "missing_membership",
    "missing_closure",
    "misjoin",
    "native_failure",
    "wrong_probability_report",
)
QUERIES = ("authorized_state", "disjoint_membership", "sigmoid_arithmetic")
FORECASTS = {
    "original": ["compliant", "compliant", "compliant"],
    "overlap": ["compliant", "violation", "compliant"],
    "repair_membership": ["compliant", "compliant", "compliant"],
    "changed_state_equal_output": ["violation", "compliant", "compliant"],
    "ineffective_frozen_fit": ["violation", "compliant", "compliant"],
    "restore_state": ["compliant", "compliant", "compliant"],
    "missing_association": ["unknown", "compliant", "unknown"],
    "missing_membership": ["compliant", "unknown", "compliant"],
    "missing_closure": ["unknown", "unknown", "unknown"],
    "misjoin": ["conflict", "conflict", "conflict"],
    "native_failure": ["unknown", "unknown", "unknown"],
    "wrong_probability_report": ["compliant", "compliant", "violation"],
}


def state(base: Any) -> dict[str, Any]:
    """Declared binary linear footprint; no claim about arbitrary native fields."""
    return {
        "class": type(base).__name__,
        "params": base.get_params(deep=False),
        "coef": base.coef_.tolist(),
        "intercept": base.intercept_.tolist(),
        "classes": base.classes_.tolist(),
        "features": int(base.n_features_in_),
    }


def arithmetic(call: dict[str, Any]) -> list[list[float]]:
    """Recompute Platt probabilities without calling a sklearn predictor."""
    np, special = import_module("numpy"), import_module("scipy.special")
    actual = call["actual_state"]
    if actual["classes"] != [0, 1] or len(actual["coef"]) != 1 or len(actual["intercept"]) != 1:
        raise ValueError("declared binary linear footprint required")
    inputs = np.asarray(call["input"], dtype=float)
    coefficients = np.asarray(actual["coef"], dtype=float)
    if (
        inputs.ndim != 2
        or inputs.shape[1] != actual["features"]
        or coefficients.shape != (1, actual["features"])
    ):
        raise ValueError("declared input/footprint shape differs")
    if not np.isfinite(inputs).all() or not np.isfinite(coefficients).all():
        raise ValueError("nonfinite native evidence")
    margin = (inputs @ coefficients.T).ravel()
    margin += actual["intercept"][0]
    sigmoid = call["sigmoid"]
    if not all(np.isfinite(value) for value in (*actual["intercept"], sigmoid["a"], sigmoid["b"])):
        raise ValueError("nonfinite sigmoid evidence")
    positive = special.expit(-(sigmoid["a"] * margin + sigmoid["b"]))
    return cast(list[list[float]], np.column_stack((1 - positive, positive)).tolist())


def assess(packet: dict[str, Any]) -> dict[str, str]:
    """Certify three predicates of a successfully completed, closed call.

    The conjunction gate is a service definition: failure does not make the
    historical fitting membership intrinsically unknowable. The complete-history
    comparison separately reports that still-answerable historical predicate.
    """
    if packet.get("schema") != "calibration-native-evidence/v1":
        raise ValueError("unsupported calibration evidence")
    call = packet["call"]
    if call["token"] != packet["token"]:
        return dict.fromkeys(QUERIES, "conflict")
    if not call["closed"] or call["failed"] or call["output"] is None:
        return dict.fromkeys(QUERIES, "unknown")
    actual = call.get("actual_state")
    membership = packet.get("membership")
    result = dict.fromkeys(QUERIES, "unknown")
    if actual is not None:
        result["authorized_state"] = (
            "compliant" if actual == packet["approved_state"] else "violation"
        )
        if call.get("sigmoid") is not None:
            np = import_module("numpy")
            expected, observed = arithmetic(call), np.asarray(call["output"], dtype=float)
            if observed.shape != (len(expected), 2) or not np.isfinite(observed).all():
                raise ValueError("probability evidence shape/values differ")
            result["sigmoid_arithmetic"] = (
                "compliant"
                if np.allclose(expected, observed, rtol=1e-10, atol=1e-12)
                else "violation"
            )
    if membership is not None:
        _validate_membership(membership)
        base_ids = {row["id"] for row in membership["base"]}
        cal_ids = {row["id"] for row in membership["calibration"]}
        base_rows = {row["sha256"] for row in membership["base"]}
        cal_rows = {row["sha256"] for row in membership["calibration"]}
        result["disjoint_membership"] = (
            "violation" if base_ids & cal_ids or base_rows & cal_rows else "compliant"
        )
    return result


def _validate_membership(membership: dict[str, Any]) -> None:
    bindings: dict[int, str] = {}
    for name in ("base", "calibration"):
        rows = membership[name]
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("membership identities must be unique within a fit")
        for row in rows:
            identifier, identity = row["id"], row["sha256"]
            if (
                type(identifier) is not int
                or identifier < 0
                or not isinstance(identity, str)
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise ValueError("invalid sample binding")
            if identifier in bindings and bindings[identifier] != identity:
                raise ValueError("sample identity was rebound to other data")
            bindings[identifier] = identity


def membership(session: dict[str, Any], ids: list[int]) -> list[dict[str, Any]]:
    return [
        {
            "id": identifier,
            "sha256": digest([session["x"][identifier].tolist(), int(session["y"][identifier])]),
        }
        for identifier in ids
    ]


def setup(seed: int, *, overlap: bool = False) -> dict[str, Any]:
    """Fit only owned generated data; keep all native top-level fit calls."""
    np = import_module("numpy")
    datasets = import_module("sklearn.datasets")
    linear = import_module("sklearn.linear_model")
    calibration, frozen = import_module("sklearn.calibration"), import_module("sklearn.frozen")
    x, y = datasets.make_classification(
        n_samples=240, n_features=6, n_informative=4, n_redundant=0, random_state=seed
    )
    base_ids = list(range(80))
    cal_ids = list(range(40, 120) if overlap else range(80, 160))
    base = linear.LogisticRegression(solver="liblinear", random_state=0, max_iter=1000)
    ledger: list[dict[str, Any]] = []
    base.fit(x[base_ids], y[base_ids])
    approved = state(base)
    ledger.append(
        {
            "api": "base.fit",
            "members": base_ids,
            "input": x[base_ids].tolist(),
            "labels": y[base_ids].tolist(),
            "returned_state": approved,
            "status": "returned",
        }
    )
    wrapped = frozen.FrozenEstimator(base)
    model = calibration.CalibratedClassifierCV(wrapped, method="sigmoid", cv=3)
    model.fit(x[cal_ids], y[cal_ids])
    ledger.append(
        {
            "api": "calibrator.fit",
            "members": cal_ids,
            "input": x[cal_ids].tolist(),
            "labels": y[cal_ids].tolist(),
            "returned_sigmoid": {
                "a": float(model.calibrated_classifiers_[0].calibrators[0].a_),
                "b": float(model.calibrated_classifiers_[0].calibrators[0].b_),
            },
            "status": "returned",
        }
    )
    query = np.asarray(x[160:176]).copy()
    query[:, -1] = 0.0
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
    }


def predict(session: dict[str, Any], token: str, *, invalid: bool = False) -> dict[str, Any]:
    """Caller-side native snapshot and independent arithmetic reference.

    This admits serial, direct delegation and no mutation during the call. The
    caller snapshot is a computational reference, not a separate trusted host.
    """
    model = session["model"]
    pair = model.calibrated_classifiers_[0]
    actual = pair.estimator.estimator
    before = state(actual)
    query = session["query"][:, :-1] if invalid else session["query"]
    sigmoid = pair.calibrators[0]
    call: dict[str, Any] = {
        "token": token,
        "input": query.tolist(),
        "actual_state": before,
        "sigmoid": {"a": float(sigmoid.a_), "b": float(sigmoid.b_)},
        "held_base_is_enrolled": actual is session["base"],
        "closed": False,
        "failed": False,
        "output": None,
    }
    started = perf_counter_ns()
    try:
        call["output"] = model.predict_proba(query).tolist()
    except (ValueError, RuntimeError) as exc:
        call["failed"], call["error_type"] = True, type(exc).__name__
    session["last_prediction_ns"] = perf_counter_ns() - started
    call["closed"] = True
    call["state_stable"] = state(actual) == before
    if not call["state_stable"] or not call["held_base_is_enrolled"]:
        raise ValueError("native pairing/stability premise differs")
    return {
        "schema": "calibration-native-evidence/v1",
        "token": token,
        "approved_state": copy.deepcopy(session["approved"]),
        "membership": {
            "base": membership(session, session["base_ids"]),
            "calibration": membership(session, session["cal_ids"]),
        },
        "call": call,
    }


def execute(arm: str, seed: int) -> dict[str, Any]:
    """Owned forward controls; missing packets do not hide the ordinary history."""
    if arm not in ARMS:
        raise ValueError("unknown calibration arm")
    session = setup(seed, overlap=arm in {"overlap", "repair_membership"})
    before = predict(session, "before")
    intervene(session, arm)
    native = predict(session, "request", invalid=arm == "native_failure")
    if arm == "wrong_probability_report":
        # An authored consumer-reporting fault, not a defect in sklearn arithmetic.
        native["call"]["native_probability_output"] = copy.deepcopy(native["call"]["output"])
        native["call"]["output"] = [list(reversed(row)) for row in native["call"]["output"]]
    captured = copy.deepcopy(native)
    if arm == "missing_association":
        captured["call"]["actual_state"] = None
    elif arm == "missing_membership":
        captured["membership"] = None
    elif arm == "missing_closure":
        captured["call"]["closed"] = False
    elif arm == "misjoin":
        captured["call"]["token"] = "foreign-request"
    captured_answers, ordinary = assess(captured), assess(native)
    return {
        "arm": arm,
        "seed": seed,
        "fit_call_ledger": session["ledger"],
        "native_packets": [before, native],
        "captured_packet": captured,
        "captured_answers": captured_answers,
        "ordinary_complete_history": ordinary,
        "forecast": dict(zip(QUERIES, FORECASTS[arm], strict=True)),
        "forecast_supported": list(captured_answers.values()) == FORECASTS[arm],
        "same_entire_probability_output": before["call"]["output"] == native["call"]["output"],
        "native_top_level_predictions": 2,
        "native_failed_predictions": int(native["call"]["failed"]),
        "packet_sha256": digest(captured),
        "scope": "source-informed generated-data control; declared binary footprint and honest host",
    }


def intervene(session: dict[str, Any], arm: str) -> None:
    """Apply the prespecified change after the enrollment/calibration boundary."""
    if arm == "repair_membership":
        session["cal_ids"] = list(range(80, 160))
        session["model"].fit(session["x"][session["cal_ids"]], session["y"][session["cal_ids"]])
        session["ledger"].append(
            {
                "api": "calibrator.fit",
                "members": session["cal_ids"],
                "input": session["x"][session["cal_ids"]].tolist(),
                "labels": session["y"][session["cal_ids"]].tolist(),
                "status": "returned",
                "returned_sigmoid": {
                    "a": float(session["model"].calibrated_classifiers_[0].calibrators[0].a_),
                    "b": float(session["model"].calibrated_classifiers_[0].calibrators[0].b_),
                },
            }
        )
    if arm in {"changed_state_equal_output", "ineffective_frozen_fit", "restore_state"}:
        session["base"].coef_[0, -1] += 0.75
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
        elif arm == "restore_state":
            session["base"].coef_[0, -1] = session["approved"]["coef"][0][-1]
