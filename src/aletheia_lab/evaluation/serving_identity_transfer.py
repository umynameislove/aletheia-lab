"""Outcome-held-out store ownership: ordinary request/object correspondence.

This analysis keeps actual-use identity, artifact authentication and selection
policy distinct. No new checker, hostile-host guarantee or signing superiority
is inferred. Inputs are retained observations of freshly owned native models.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from aletheia_lab.evaluation.request_model_audit import correspondence, digest, resolve

EXPECTED = ("B", "A", "A", "A", "A", "A", "A", "B")
FORECAST = {
    "affected": ("B", "B", "B", "B", "A", "B", "B", "B"),
    "fixed": ("B", "A", "A", "B", "A", "B", "A", "B"),
}
VIEWS = ("complete", "missing_use", "missing_load_closure", "conflicting_use")
MODEL_NAMES = {"A": "aaa", "B": "bbb"}


def reference(row: dict[str, Any], fixtures: dict[str, Any]) -> dict[str, Any]:
    """Identify caller-observed native state independently of capture/expected.

    Output and current model path never select the reference identity. A unique
    owned serialized state and its retained bytes must agree with the caller's
    backend at entry. The independent verifier also rereads the owned pickle.
    """
    matches = [
        label
        for label, model in fixtures["models"].items()
        if model["owned_state"] == row["caller_backend_state"]
        and model["owned_state"] == row["caller_serialized_state"]
        and model["pickle_sha256"] == row["selected_model_sha256"]
    ]
    if len(matches) != 1 or row["caller_object_id"] != row["load_object_id"]:
        return {"identity": None, "verdict": "unknown"}
    identity = matches[0]
    expected = EXPECTED[int(row["slot"][1:])]
    return {"identity": identity, "verdict": "compliant" if identity == expected else "violation"}


def captured_frame(row: dict[str, Any], fixtures: dict[str, Any]) -> dict[str, Any]:
    """Capture-only construction; do not fill loads from the reference answer."""
    load_matches = [
        (label, model)
        for label, model in fixtures["models"].items()
        if model["owned_state"] == row["captured_load_state"]
        and model["pickle_sha256"] == row["selected_model_sha256"]
    ]
    generation = str(row["load_generation"])
    loads = {}
    if len(load_matches) == 1:
        label, model = load_matches[0]
        loads[generation] = {
            "model": MODEL_NAMES[label],
            "artifact": model["closure"]["closure_sha256"],
            "fingerprint": digest([row["load_object_id"], row["captured_load_state"]]),
        }
    return {
        "token": row["token"],
        "requested": MODEL_NAMES[EXPECTED[int(row["slot"][1:])]],
        "kind": "non_batched",
        "input": digest(row["input"]),
        "output": digest(row["output"]),
        "closed": row["closed"],
        "failed": row["exception"] is not None,
        "loads": loads,
        "uses": [
            {
                "token": row["token"],
                "batch": row["slot"],
                "index": 0,
                "generation": generation,
                "input": digest(row["input"]),
                "output": digest(row["output"]),
                "fingerprint": digest([row["actual_use_object_id"], row["captured_use_state"]]),
            }
        ],
    }


def projected(frame: dict[str, Any], view: str) -> dict[str, Any]:
    result = deepcopy(frame)
    if view == "missing_use":
        result["uses"] = []
    elif view == "missing_load_closure":
        result["loads"] = {}
    elif view == "conflicting_use":
        other = dict(result["uses"][0])
        other["fingerprint"] = digest("incompatible same-request object")
        result["uses"].append(other)
    elif view != "complete":
        raise ValueError("unsupported evidence projection")
    return result


def signed_history(frame: dict[str, Any], signatures: dict[str, Any]) -> str:
    """Official closure authentication plus the ordinary actual-use join.

    Authentication is actually performed by the orchestration's qualified
    official verifier. It is not replaced by this join or by a boolean mock.
    """
    verdict = correspondence(frame)
    for load in frame["loads"].values():
        label = next(key for key, value in MODEL_NAMES.items() if value == load["model"])
        if signatures[label]["closure_sha256"] != load["artifact"]:
            return "unknown"
    return verdict


def _request_result(
    row: dict[str, Any], fixtures: dict[str, Any], signatures: dict[str, Any], version: str
) -> dict[str, Any]:
    truth = reference(row, fixtures)
    frame = captured_frame(row, fixtures)
    views = {}
    for view in VIEWS:
        offered = projected(frame, view)
        views[view] = {
            "candidate": resolve(offered),
            "native_history": correspondence(offered),
            "signing_integrated": signed_history(offered, signatures),
        }
    predicted = FORECAST[version][int(row["slot"][1:])]
    return {
        "slot": row["slot"],
        "expected": EXPECTED[int(row["slot"][1:])],
        "reference": truth,
        "predicted_identity": predicted,
        "identity_prediction": "unidentified"
        if truth["identity"] is None
        else ("supported" if predicted == truth["identity"] else "contradicted"),
        "artifact_authentication": True,
        "views": views,
        "numerical_probe": row["output"],
        "frame": frame,
    }


def _counterpair(workers: dict[str, Any], fixtures: dict[str, Any]) -> dict[str, Any]:
    pairs = {}
    truths = {}
    for version, worker in workers.items():
        rows = [row for row in worker["requests"] if row["slot"] == "t1"]
        if not rows:
            return {"status": "unidentified", "reason": "missing t1 native outcome"}
        row = rows[0]
        pairs[version] = {
            "expected_closure": fixtures["models"]["A"]["closure"]["closure_sha256"],
            "tag": fixtures["tag"],
            "input": row["input"],
            "output": row["output"],
        }
        truths[version] = reference(row, fixtures)["verdict"]
    equal = pairs["affected"] == pairs["fixed"]
    different = set(truths.values()) == {"compliant", "violation"}
    return {
        "status": "unidentified"
        if "unknown" in truths.values()
        else ("supported" if equal and different else "contradicted"),
        "projection_equal": equal,
        "projection_sha256": {k: digest(v) for k, v in pairs.items()},
        "actual_use_verdicts": truths,
        "scope": "expected signed package/tag/input/output only; not all possible native history",
    }


def _sufficiency(rows: list[dict[str, Any]], names: tuple[str, ...]) -> str:
    for row in rows:
        truth = row["reference"]["verdict"]
        if truth in {"compliant", "violation"} and any(
            row["views"]["complete"][name] != truth for name in names
        ):
            return "contradicted"
    if len(rows) != 16 or any(row["reference"]["identity"] is None for row in rows):
        return "unidentified"
    return "supported"


def _repair_predictions(versions: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for version, values in versions.items():
        slots = {row["slot"]: row for row in values["requests"]}
        for slot in ("t4", "t5", "t6"):
            row = slots.get(slot)
            results.append(
                {
                    "version": version,
                    "slot": slot,
                    "predicted_identity": FORECAST[version][int(slot[1:])],
                    "actual_identity": None if row is None else row["reference"]["identity"],
                    "status": "unidentified" if row is None else row["identity_prediction"],
                }
            )
    return results


def analyze(
    workers: dict[str, Any], fixtures: dict[str, Any], signatures: dict[str, Any]
) -> dict[str, Any]:
    if set(workers) != set(FORECAST) or set(signatures) != {"A", "B"}:
        raise ValueError("two source versions and two signed closures required")
    if any(signatures[label] != model["closure"] for label, model in fixtures["models"].items()):
        raise ValueError("official signed closure differs from immutable fixture")
    versions = {}
    for version, worker in workers.items():
        slots = [row["slot"] for row in worker["requests"]]
        if slots != list(tuple(f"t{i}" for i in range(len(slots)))) or len(slots) > 8:
            raise ValueError("worker must preserve the ordered native prefix")
        rows = [_request_result(row, fixtures, signatures, version) for row in worker["requests"]]
        entered = worker.get("entered_slots", slots)
        unattempted = worker.get("unattempted_slots", [f"t{i}" for i in range(len(entered), 8)])
        versions[version] = {
            "planned": 8,
            "completed": len(rows),
            "unfinished": 8 - len(rows),
            "entered_slots": entered,
            "unattempted_slots": unattempted,
            "unattempted": len(unattempted),
            "attempted_failed": len(entered) - len(rows),
            "active_slot": worker.get("active_slot"),
            "terminal": worker.get("terminal"),
            "terminal_error": worker.get("terminal_error"),
            "identity_prediction_counts": dict(Counter(row["identity_prediction"] for row in rows)),
            "reference_counts": dict(Counter(row["reference"]["verdict"] for row in rows)),
            "operations": worker["operations"],
            "requests": rows,
        }
    rows = [row for version in versions.values() for row in version["requests"]]

    def correct(row: dict[str, Any], comparator: str) -> bool:
        return row["views"]["complete"][comparator] == row["reference"]["verdict"] and row[
            "reference"
        ]["verdict"] in {"compliant", "violation"}

    comparisons = {
        name: sum(correct(row, name) for row in rows)
        for name in ("candidate", "native_history", "signing_integrated")
    }
    predictions = dict(Counter(row["identity_prediction"] for row in rows))
    repairs = _repair_predictions(versions)
    repair_states = {row["status"] for row in repairs}
    return {
        "schema": "serving-identity-transfer-analysis/v1",
        "planned_requests": 16,
        "completed_requests": len(rows),
        "unfinished_requests": 16 - len(rows),
        "unattempted_requests": sum(version["unattempted"] for version in versions.values()),
        "attempted_failed_requests": sum(
            version["attempted_failed"] for version in versions.values()
        ),
        "source_family_count": 1,
        "version_trajectory_count": 2,
        "exposure": "source-informed, outcome-held-out new store-ownership mechanism/implementation",
        "identity_prediction_counts": predictions,
        "complete_correct_counts": comparisons,
        "view_counts": {
            view: {
                name: dict(Counter(row["views"][view][name] for row in rows))
                for name in comparisons
            }
            for view in VIEWS
        },
        "forecasts": {
            "F1": _sufficiency(rows, ("candidate",)),
            "F2": _sufficiency(rows, ("native_history", "signing_integrated")),
            "F3": "contradicted"
            if "contradicted" in repair_states
            else ("unidentified" if "unidentified" in repair_states else "supported"),
        },
        "repair_predictions": repairs,
        "counterpair": _counterpair(workers, fixtures),
        "versions": versions,
        "limits": "one local family, owned equal-output models, serial SDK construction/use, trusted host/capture; no natural deployment, source-blind discovery or new algorithm advantage",
    }
