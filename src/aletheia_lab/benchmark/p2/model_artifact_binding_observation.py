"""Read an already verified artifact cell without fitting or injecting again.

The caller pins the receipt verified by the independent forward-cell replay.
This adapter checks the retained chain and projects actual measurements; it is
not another source-refit verifier or a claim of trust against rewritten history.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.model_artifact_binding_development import ModelArtifactBindingError
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import (
    CASES,
    SCORER_ID,
    json_file,
    private_directory,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward_verify import (
    _metrics_match,
    _verify_artifact_bytes,
    _verify_rivals,
    _verify_trace,
    independent_losses,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_binding_reader import ArtifactBindingObservation

_JSON_NAMES = (
    "plan.json",
    "lease.json",
    "manifest.json",
    "load-trace.json",
    "scores.json",
    "rivals.json",
    "receipt.json",
)


def _retained_chain(
    root: Path, cell_dir: Path, expected_receipt_sha256: str
) -> dict[str, dict[str, Any]]:
    cell_dir = private_directory(root, cell_dir, existing=True)
    expected_files = {*_JSON_NAMES, "artifact_A.joblib", "artifact_B.joblib"}
    if {p.name for p in cell_dir.iterdir()} != expected_files or file_sha256(
        cell_dir / "receipt.json"
    ) != expected_receipt_sha256:
        raise ModelArtifactBindingError("verified receipt identity or retained file census differs")
    data = {name: json_file(cell_dir / name) for name in _JSON_NAMES}
    receipt, plan = data["receipt.json"], data["plan.json"]
    if (
        receipt.get("schema_version") != "model-artifact-binding-forward-receipt/v1"
        or receipt.get("status")
        not in {"development_positive_control_observed", "development_effect_insufficient"}
        or receipt.get("primary_endpoint") != SCORER_ID
        or receipt.get("provider_calls") != 0
        or any(
            receipt.get(key) is not False
            for key in (
                "scientific_admission",
                "U4_authorized",
                "registered_attempt",
                "protected_predictions_or_metrics_computed",
            )
        )
        or receipt.get("plan_sha256") != file_sha256(cell_dir / "plan.json")
    ):
        raise ModelArtifactBindingError("receipt has an incompatible development scope")
    retained = receipt.get("retained_sha256")
    if not isinstance(retained, dict) or retained.keys() != set(_JSON_NAMES) - {
        "plan.json",
        "receipt.json",
    }:
        raise ModelArtifactBindingError("retained receipt bindings are incomplete")
    for name, digest in retained.items():
        if file_sha256(cell_dir / name) != digest:
            raise ModelArtifactBindingError("a retained development artifact changed")
    for name, digest in plan["code_sha256"].items():
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or file_sha256(root / relative) != digest
        ):
            raise ModelArtifactBindingError("the verified upstream code binding changed")
    expected_lease = {
        "schema_version": "model-artifact-binding-forward-lease/v1",
        "plan_sha256": receipt["plan_sha256"],
    }
    if data["lease.json"] != expected_lease:
        raise ModelArtifactBindingError("lease binding changed")
    _verify_artifact_bytes(cell_dir, data["manifest.json"])
    _check_manifest_and_trace(data)
    return data


def _check_manifest_and_trace(data: dict[str, dict[str, Any]]) -> None:
    manifest, plan, receipt = data["manifest.json"], data["plan.json"], data["receipt.json"]
    for name in ("source", "recipes", "partitions"):
        if manifest[name] != plan[name]:
            raise ModelArtifactBindingError("manifest and plan bindings differ")
    if manifest["plan_sha256"] != receipt["plan_sha256"]:
        raise ModelArtifactBindingError("manifest belongs to a different cell")
    events = data["load-trace.json"]["events"]
    if not isinstance(events, list) or len(events) != len(CASES):
        raise ModelArtifactBindingError("load events are incomplete")
    _verify_trace(
        trace=data["load-trace.json"],
        manifest=manifest,
        plan=plan,
        feature_count=events[0]["feature_count"],
    )


def _check_scores(data: dict[str, dict[str, Any]]) -> tuple[list[str], list[int]]:
    scores, plan = data["scores.json"], data["plan.json"]
    rows, targets = scores["row_ids"], scores["targets"]
    if (
        not isinstance(rows, list)
        or not all(isinstance(row, str) and row for row in rows)
        or len(set(rows)) != len(rows)
        or not isinstance(targets, list)
        or len(rows) != len(targets)
        or any(type(y) is not int or y not in (0, 1) for y in targets)
        or set(targets) != {0, 1}
        or canonical_sha256({"rows": list(zip(rows, targets, strict=True))})
        != plan["partitions"]["measurement"]["row_target_sha256"]
    ):
        raise ModelArtifactBindingError("retained row-target measurement membership changed")
    if scores["paths"].keys() != {case for case, *_ in CASES}:
        raise ModelArtifactBindingError("retained score paths are incomplete")
    for path in scores["paths"].values():
        _check_raw(path["raw"], len(rows))
        if canonical_sha256({"rows": rows, "scores": path["raw"]}) != path["raw_scores_sha256"]:
            raise ModelArtifactBindingError("raw score identity changed")
        _metrics_match(
            path["raw_metrics"], independent_losses(targets, [p[1] for p in path["raw"]])
        )
    paths = scores["paths"]
    if (
        not (paths["healthy"] == paths["sham"] == paths["corrected"] == paths["manifest_text_only"])
        or paths["faulty"] != paths["legitimate_B"]
    ):
        raise ModelArtifactBindingError("retained controls differ")
    _verify_rivals(data["rivals.json"], scores, data["manifest.json"]["artifacts"]["A"])
    return rows, targets


def _check_raw(raw: Any, count: int) -> None:
    if not isinstance(raw, list) or len(raw) != count:
        raise ModelArtifactBindingError("probability rows are incomplete")
    for pair in raw:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(
                type(p) not in (int, float) or not math.isfinite(p) or not 0 < p < 1 for p in pair
            )
            or not math.isclose(sum(pair), 1, rel_tol=0, abs_tol=1e-12)
        ):
            raise ModelArtifactBindingError("a retained binary probability row is invalid")


def retained_artifact_observations(
    *, root: Path, cell_dir: Path, expected_receipt_sha256: str
) -> dict[str, ArtifactBindingObservation]:
    """Project all six paths and both rivals from a pinned, previously replayed cell.

    The same first class-0/class-1 measurement indices are used everywhere.
    They witness this retained two-row target swap, not arbitrary label faults.
    The shared A benchmark is never misrepresented as B/B's intended artifact.
    """

    try:
        data = _retained_chain(root, cell_dir, expected_receipt_sha256)
        _, targets = _check_scores(data)
        scores, receipt, trace, manifest = (
            data[name]
            for name in ("scores.json", "receipt.json", "load-trace.json", "manifest.json")
        )
        probes = (targets.index(0), targets.index(1))
        aliases = {
            manifest["artifacts"][name]: f"artifact-{i}" for i, name in enumerate(("A", "B"))
        }
        events = {event["case"]: event for event in trace["events"]}
        common = {
            "record_count": len(targets),
            "reference_log_loss": scores["paths"]["healthy"]["raw_metrics"][
                "reference_prior_log_loss"
            ],
            "source_targets": (0, 1),
            "feature_count": events["healthy"]["feature_count"],
            "training_count": receipt["train_count"],
            "calibration_count": receipt["calibration_count"],
            "private_source_sha256": expected_receipt_sha256,
        }

        def observation(
            event: dict[str, Any],
            raw: list[list[float]],
            positive: list[float],
            scored_targets: list[int],
            loss: float,
            consumed: tuple[int, int],
        ) -> ArtifactBindingObservation:
            return ArtifactBindingObservation(
                **common,
                observed_log_loss=loss,
                intended_artifact=aliases[event["declared_artifact_sha256"]],
                loaded_artifact=aliases[event["actual_loaded_sha256"]],
                reported_artifact=aliases[event["reported_manifest_sha256"]],
                model_classes=(0, 1),
                consumed_classes=consumed,
                raw_probe_scores=(
                    (raw[probes[0]][0], raw[probes[0]][1]),
                    (raw[probes[1]][0], raw[probes[1]][1]),
                ),
                scored_probe_positive=(positive[probes[0]], positive[probes[1]]),
                scoring_targets=(scored_targets[probes[0]], scored_targets[probes[1]]),
            )

        observations = {}
        for case, *_ in CASES:
            path = scores["paths"][case]
            observations[case] = observation(
                events[case],
                path["raw"],
                [p[1] for p in path["raw"]],
                targets,
                path["raw_metrics"]["reference_prior_log_loss"],
                (0, 1),
            )
        for name, consumed in (
            ("adapter_column_reversal", (1, 0)),
            ("two_row_target_swap", (0, 1)),
        ):
            rival = data["rivals.json"][name]
            observations[name] = observation(
                events["healthy"],
                scores["paths"]["healthy"]["raw"],
                rival["scored_positive"],
                rival["targets"],
                rival["metrics"]["reference_prior_log_loss"],
                consumed,
            )
        return observations
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        raise ModelArtifactBindingError("retained artifact projection is malformed") from exc
