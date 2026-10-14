"""Reconcile and rebuild the new ground-truth method tables from saved evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.ground_truth_study import BASE_CASES, SEEDS, TRANSFER_CASES
from aletheia_lab.benchmark.p2.ground_truth_validity import assess_ground_truth
from aletheia_lab.diagnosis.study_artifacts import read_artifact, write_artifact


def wilson(count: int, total: int) -> tuple[float, float]:
    """95% binomial score interval for independently repeated simulator trials."""
    z = 1.959963984540054
    proportion, denominator = count / total, 1 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    radius = (
        z * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2)) / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def _check_record(record: dict[str, Any], siblings: list[dict[str, Any]]) -> None:
    input_sign = next((row for row in siblings if row["case"] == "input_sign"), None)
    if input_sign is None:
        raise ValueError("input-sign comparison is unavailable")
    same_probabilities = (
        record["prediction_payload"]["probabilities"]
        == input_sign["prediction_payload"]["probabilities"]
    )
    if (
        type(record["exact_probability_buffer_equal_to_input_sign"]) is not bool
        or record["exact_probability_buffer_equal_to_input_sign"] != same_probabilities
    ):
        raise ValueError("probability-buffer equality differs from saved payload")
    if not math.isclose(
        record["harmful_loss_delta"],
        record["prediction_payload"]["observed_loss"] - record["prediction_payload"]["clean_loss"],
        rel_tol=0,
        abs_tol=1e-12,
    ):
        raise ValueError("paired harmful effect differs from saved losses")
    for view, payload, digest in (
        ("prediction_only", "prediction_payload", "prediction_sha256"),
        ("configured_pipeline_lineage", "lineage_payload", "lineage_sha256"),
    ):
        if canonical_sha256(record[payload]) != record[digest]:
            raise ValueError("visible payload differs from its digest")
        compatible = sorted(
            {
                "input_sign" if rival["case"] == "wrong_repair" else rival["case"]
                for rival in siblings
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
        stored = record["views"][view]
        if stored["compatible"] != compatible or stored["status"] != decision.status:
            raise ValueError("stored decision or rival catalogue differs from evidence")
        if (
            stored["verified_consequence"] != decision.verified_consequence
            or stored["identifiable_in_catalogue"] != decision.identifiable_in_catalogue
            or stored["reason"] != decision.reason
        ):
            raise ValueError("stored gate fields differ from the reconstructed decision")


def _native_table(report: dict[str, Any]) -> dict[str, Any]:
    phase = report["phase"]
    if phase not in ("development", "transfer"):
        raise ValueError("native phase is invalid")
    expected_sources = ("iris",) if phase == "development" else ("wine", "digits")
    cases = BASE_CASES if phase == "development" else TRANSFER_CASES
    if tuple(source["source"] for source in report["sources"]) != expected_sources:
        raise ValueError("source census differs")
    tables: dict[str, Any] = {}
    for source in report["sources"]:
        records, failures = source["records"], source["failures"]
        identities = [(row["seed"], row["case"]) for row in records]
        failed = [(failure["seed"], case) for failure in failures for case in failure["cases"]]
        expected = {(seed, case) for seed in SEEDS for case in cases}
        if (
            len(identities + failed) != len(set(identities + failed))
            or set(identities + failed) != expected
            or source["planned"] != len(expected)
        ):
            raise ValueError("native census has duplicate, missing or unexpected slots")
        for record in records:
            if record["source"] != source["source"]:
                raise ValueError("record source differs from its parent")
            siblings = [row for row in records if row["seed"] == record["seed"]]
            _check_record(record, siblings)
        by_case = {}
        for case in cases:
            rows = [row for row in records if row["case"] == case]
            by_case[case] = {
                "count": len(rows),
                "harm_range": [
                    min(row["harmful_loss_delta"] for row in rows),
                    max(row["harmful_loss_delta"] for row in rows),
                ]
                if rows
                else None,
                "repair_range": [
                    min(row["repair_residual"] for row in rows),
                    max(row["repair_residual"] for row in rows),
                ]
                if rows
                else None,
                "prediction_only": dict(
                    Counter(row["views"]["prediction_only"]["status"] for row in rows)
                ),
                "lineage": dict(
                    Counter(row["views"]["configured_pipeline_lineage"]["status"] for row in rows)
                ),
            }
        tables[source["source"]] = {
            "planned": len(expected),
            "completed": len(records),
            "failed": len(failed),
            "source_sha256": source["source_sha256"],
            "cases": by_case,
        }
    return tables


def build_analysis(directory: Path) -> dict[str, Any]:
    names = ("seal.json", "development-reviewed.json", "sampling.json", "transfer.json")
    snapshots = {name: read_artifact(directory / name) for name in names}
    inputs = {name: item[0] for name, item in snapshots.items()}
    seal = inputs["seal.json"]
    for name, report in inputs.items():
        if (
            report["design_sha256"] != seal["design_sha256"]
            or report["code_sha256"] != seal["code_sha256"]
        ):
            raise ValueError(f"design/code differs for {name}")
        if report["historical_registered_runs_executed"] is not False:
            raise ValueError("historical execution flag differs")
    if canonical_sha256(seal["design"]) != seal["design_sha256"]:
        raise ValueError("design payload digest differs")
    native = {
        phase: _native_table(inputs[name])
        for phase, name in (
            ("development", "development-reviewed.json"),
            ("transfer", "transfer.json"),
        )
    }
    sampling = inputs["sampling.json"]["sampling"]
    rows = sampling["records"]
    expected_conditions = (
        "iid_null",
        "iid_shift",
        "fixed_pool_null",
        "shared_ancestry_iid_guard",
        "fixed_pool_biased_alternative",
    )
    identities = [(row["seed"], row["condition"]) for row in rows]
    expected = {
        (seed, condition) for seed in range(41000, 41100) for condition in expected_conditions
    }
    if len(identities) != len(set(identities)) or set(identities) != expected:
        raise ValueError("sampling census differs")
    rates = {}
    for condition in expected_conditions:
        group = [row for row in rows if row["condition"] == condition]
        rejections = sum(row["p_value"] is not None and row["p_value"] <= 0.05 for row in group)
        rates[condition] = {
            "n": len(group),
            "rejected": rejections,
            "unidentified": sum(row["status"] == "unidentified" for row in group),
            "wilson_95_simulator_rate": wilson(rejections, len(group))
            if condition != "shared_ancestry_iid_guard"
            else None,
        }
    transfer_records = [
        row for source in inputs["transfer.json"]["sources"] for row in source["records"]
    ]
    twins = [row for row in transfer_records if row["case"] == "resident_sign"]
    harms = [row for row in transfer_records if row["case"] in ("input_sign", "resident_sign")]
    recoding = [row for row in transfer_records if row["case"] == "training_target_roll"]
    forecasts = {
        "measure": "supported"
        if sampling["structural"]["uniform_duplication"]["tv"] < 1e-12
        and sampling["structural"]["unequal_duplication"]["tv"] > 0
        else "contradicted",
        "sampling": "supported"
        if any(row["condition"] == "fixed_pool_null" and row["tv"] > 0 for row in rows)
        else "contradicted",
        "twins": "supported"
        if len(twins) == 10
        and all(row["exact_probability_buffer_equal_to_input_sign"] for row in twins)
        else "unidentified_or_contradicted",
        "effect": "supported"
        if len(harms) == 20 and all(row["harmful_loss_delta"] >= 0.01 for row in harms)
        else "unidentified_or_contradicted",
        "transfer": "supported"
        if len(recoding) == 10
        and all(
            row["harmful_loss_delta"] >= 0.01 and abs(row["repair_residual"]) <= 1e-10
            for row in recoding
        )
        else "unidentified_or_contradicted",
    }
    if any(read_artifact(directory / name)[1] != raw for name, (_, raw) in snapshots.items()):
        raise ValueError("an immutable input changed during analysis")
    return {
        "schema": "ground-truth-validity-analysis/1",
        "native": native,
        "sampling": rates,
        "forecasts": forecasts,
        "inputs_sha256": {
            name: hashlib.sha256(raw).hexdigest() for name, (_, raw) in snapshots.items()
        },
        "design_sha256": seal["design_sha256"],
        "historical_admitted_denominator_changed": False,
        "interpretation": "finite_method_stress_test_not_diagnostic_accuracy_or_new_causal_admission",
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_analysis(args.directory)
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    if args.output is not None:
        write_artifact(args.output, Path(__file__).resolve().parents[1], report)
        print(
            json.dumps({"output": str(args.output), "sha256": hashlib.sha256(payload).hexdigest()})
        )
    else:
        print(payload.decode("utf-8"), end="")
    return 0


def main() -> int:
    try:
        return _main()
    except (OSError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        print(json.dumps({"status": "failed_closed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
