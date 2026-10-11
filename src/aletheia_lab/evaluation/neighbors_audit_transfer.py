"""Unchanged maintainer source body with explicit harness interposition.

The final native predict handoff is recorded once. Host honesty and unchanged
uncaptured native implementation fields remain premises, not attestation claims.
"""

from __future__ import annotations

import copy
import importlib.util
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from aletheia_lab.evaluation.request_model_audit import digest, resolve
from aletheia_lab.project.identity import content_sha256

SOURCE_SHA = "0b0965c52e13f5c3f6bad63ec6eba2dc6860b63441900efbe7d6f24b82dd4375"
ARMS = (
    "original",
    "wrong_labels",
    "restore_labels",
    "missing_use",
    "missing_closure",
    "misjoin",
    "native_failure",
)


def source_path() -> Path:
    sklearn = import_module("sklearn")

    return Path(cast(str, sklearn.__file__)).parent / "neighbors/tests/test_neighbors_pipeline.py"


def array(value: Any) -> dict[str, Any]:
    np = import_module("numpy")

    if hasattr(value, "tocsr"):
        csr = value.tocsr()
        return {
            "shape": list(csr.shape),
            "data": csr.data.tolist(),
            "indices": csr.indices.tolist(),
            "indptr": csr.indptr.tolist(),
        }
    converted = np.asarray(value)
    return {"shape": list(converted.shape), "values": converted.tolist()}


def footprint(regressor: Any) -> dict[str, Any]:
    return {
        "class": type(regressor).__name__,
        "params": regressor.get_params(deep=False),
        "targets": array(regressor._y),
        "training_graph": array(regressor._fit_X),
    }


class Recorder:
    """Enrollment from fit and actual final handoff, separate from reference."""

    def __init__(self, arm: str, sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.arm = arm
        self.sink = sink
        self.expected: dict[int, dict[str, Any]] = {}
        self.rows: list[dict[str, Any]] = []
        self.native: list[dict[str, Any]] = []
        self.allowed_targets: dict[int, Any] = {}
        self.owners: dict[int, Any] = {}

    def fitted(self, pipeline: Any, original: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(pipeline, *args, **kwargs)
        regressor = pipeline.steps[-1][1]
        self.expected[id(regressor)] = copy.deepcopy(footprint(regressor))
        self.owners[id(regressor)] = regressor
        self.allowed_targets[id(regressor)] = regressor._y.copy()
        return result

    def pipeline_predict(self, pipeline: Any, original: Any, x: Any, **kwargs: Any) -> Any:
        regressor = pipeline.steps[-1][1]
        fourth = len(self.rows) == 3
        before = regressor._y.copy()
        changed = fourth and self.arm in {"wrong_labels", "restore_labels"}
        if changed:
            regressor._y = before.copy() + 1
            if self.arm == "restore_labels":
                regressor._y = before.copy()
        try:
            if fourth and self.arm == "native_failure":
                x = x[:, :-1]  # Authored invalid input, not a natural incident.
            return original(pipeline, x, **kwargs)
        except (ValueError, RuntimeError) as exc:
            if len(self.rows) == 3:
                row = {
                    "ordinal": 3,
                    "native_error": type(exc).__name__,
                    "reference": "unknown",
                    "frame": failed_frame("p3", x),
                }
                self.rows.append(row)
                if self.sink:
                    self.sink(row)
            raise
        finally:
            regressor._y = before

    def predict(self, regressor: Any, original: Any, x: Any, **kwargs: Any) -> Any:
        raw = {
            "class": type(regressor).__name__,
            "input": array(x),
            "targets": array(regressor._y),
            "precomputed": regressor.metric == "precomputed",
        }
        self.native.append(raw)
        if (
            regressor.metric != "precomputed"
            or id(regressor) not in self.owners
            or self.owners[id(regressor)] is not regressor
        ):
            result = original(regressor, x, **kwargs)
            raw["output"] = array(result)
            return result
        ordinal = len(self.rows)
        expected, observed = self.expected[id(regressor)], footprint(regressor)
        before_hash = digest(observed)
        result = original(regressor, x, **kwargs)
        raw["output"] = array(result)
        stable = before_hash == digest(footprint(regressor))
        truth = "compliant" if observed == expected else "violation"
        token = f"p{ordinal}"
        frame = make_frame(token, x, result, before_hash, truth, stable)
        if ordinal == 3:
            if self.arm == "missing_use":
                frame["uses"] = []
            elif self.arm == "missing_closure":
                frame["closed"] = False
            elif self.arm == "misjoin":
                frame["uses"][0]["token"] = "wrong-request"
        self.rows.append(
            {
                "ordinal": ordinal,
                "reference": truth,
                "expected_footprint": expected,
                "actual_footprint": observed,
                "state_stable_during_call": stable,
                "frame": frame,
                "ordinary_state_history": truth,
                "enrollment_targets_stable": array(self.allowed_targets[id(regressor)])
                == expected["targets"],
            }
        )
        if self.sink:
            self.sink(self.rows[-1])
        return result


def make_frame(
    token: str, x: Any, output: Any, fingerprint: str, truth: str, closed: bool = True
) -> dict[str, Any]:
    return {
        "token": token,
        "requested": "aaa",
        "kind": "batched",
        "input": digest(array(x)),
        "output": digest(array(output)),
        "closed": closed,
        "failed": False,
        "loads": {
            token: {
                "model": "aaa" if truth == "compliant" else "bbb",
                "artifact": fingerprint,
                "fingerprint": fingerprint,
            }
        },
        "uses": [
            {
                "token": token,
                "batch": token,
                "index": 0,
                "generation": token,
                "input": digest(array(x)),
                "output": digest(array(output)),
                "fingerprint": fingerprint,
            }
        ],
    }


def failed_frame(token: str, x: Any) -> dict[str, Any]:
    return {
        "token": token,
        "requested": "aaa",
        "kind": "batched",
        "input": digest(array(x)),
        "output": None,
        "closed": True,
        "failed": True,
        "uses": [],
        "loads": {},
    }


def execute(
    arm: str, sink: Callable[[dict[str, Any]], None] | None = None, seed: int = 0
) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError("unknown transfer arm")
    path = source_path()
    if content_sha256(path.read_bytes()) != SOURCE_SHA:
        raise ValueError("maintainer source differs")
    neighbors = import_module("sklearn.neighbors")
    KNeighborsRegressor, RadiusNeighborsRegressor = (
        neighbors.KNeighborsRegressor,
        neighbors.RadiusNeighborsRegressor,
    )
    Pipeline = import_module("sklearn.pipeline").Pipeline
    np = import_module("numpy")
    if seed not in (0, 59, 61):
        raise ValueError("unplanned source-informed seed variant")
    random_state = np.random.RandomState

    spec = importlib.util.spec_from_file_location("owned_neighbors_source", path)
    if spec is None or spec.loader is None:
        raise ValueError("source module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = Recorder(arm, sink)
    fit, predict = Pipeline.fit, Pipeline.predict
    k_predict, r_predict = KNeighborsRegressor.predict, RadiusNeighborsRegressor.predict
    status, error = "pass", None
    with (
        patch.object(
            np.random, "RandomState", lambda value=None: random_state(seed if value == 0 else value)
        ),
        patch.object(Pipeline, "fit", lambda self, *a, **kw: recorder.fitted(self, fit, *a, **kw)),
        patch.object(
            Pipeline,
            "predict",
            lambda self, x, **kw: recorder.pipeline_predict(self, predict, x, **kw),
        ),
        patch.object(
            KNeighborsRegressor,
            "predict",
            lambda self, x, **kw: recorder.predict(self, k_predict, x, **kw),
        ),
        patch.object(
            RadiusNeighborsRegressor,
            "predict",
            lambda self, x, **kw: recorder.predict(self, r_predict, x, **kw),
        ),
    ):
        try:
            module.test_kneighbors_regressor()
        except (AssertionError, ValueError, RuntimeError) as exc:
            status, error = "source_failure", type(exc).__name__
    for row in recorder.rows:
        row["resolution"] = resolve(row["frame"])
    return {
        "arm": arm,
        "seed": seed,
        "frame_kind": "original-seed source body with interposed execution"
        if seed == 0
        else "source-informed new-data variant",
        "source_sha256": SOURCE_SHA,
        "source_status": status,
        "source_error": error,
        "planned_native_predictions": 8,
        "attempted_native_predictions": len(recorder.native),
        "returned_native_predictions": sum("output" in row for row in recorder.native),
        "chain_rows": recorder.rows,
        "native_predictions": recorder.native,
        "planned_chain_rows": 4,
        "source_file_modified": False,
        "execution_interposed": True,
    }


def native_counterpair() -> dict[str, Any]:
    """Actually realized, separate two-fit witness, not the maintainer workflow."""
    np = import_module("numpy")
    KNeighborsRegressor = import_module("sklearn.neighbors").KNeighborsRegressor

    x, query = np.array([[-1.0], [1.0]]), np.array([[0.0]])
    labels = (np.array([0.0, 0.0]), np.array([-1.0, 1.0]))
    outputs = [KNeighborsRegressor(n_neighbors=2).fit(x, y).predict(query).tolist() for y in labels]
    projection = {
        "requested_targets": digest(array(labels[0])),
        "training_input": array(x),
        "query": array(query),
        "params": {"n_neighbors": 2},
        "output": outputs[0],
        "closed": True,
    }
    assert outputs[0] == outputs[1]
    return {
        "projection_a": projection,
        "projection_b": {**projection, "output": outputs[1]},
        "actual_targets": [array(y) for y in labels],
        "answers": ["compliant", "violation"],
        "native_fits": 2,
        "native_predictions": 2,
        "scope": "authored native counterexample; targets absent from entire declared projection",
    }
