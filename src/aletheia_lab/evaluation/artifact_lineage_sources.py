"""Read the retained development artifact cell; never fit or use final outcomes."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.model_artifact_binding_observation import (
    retained_artifact_observations,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_binding_reader import (
    CONDITIONS,
    ArtifactBindingObservation,
    build_artifact_binding_context,
)
from aletheia_lab.evaluation.artifact_lineage_policy import BINDING_STATUSES, visible_reference

RECEIPT_SHA256 = "6e800aa91dc110bcd1aa5da0d07c0117e46c7de0abaa6825bef80af212339180"
CASE_KINDS = (
    "healthy",
    "faulty",
    "sham",
    "corrected",
    "legitimate_B",
    "manifest_text_only",
    "adapter_column_reversal",
    "two_row_target_swap",
)
SOURCE_CLUSTER = "retained-online-shoppers-development"


def cases_from_observations(
    observations: dict[str, ArtifactBindingObservation],
) -> list[dict[str, Any]]:
    if observations.keys() != set(CASE_KINDS):
        raise ValueError("expected all six loader paths and both retained rivals")
    cases = []
    for alias_order in (0, 1):
        aliases = {
            "artifact-0": f"artifact-{alias_order}",
            "artifact-1": f"artifact-{1 - alias_order}",
        }
        for name in CASE_KINDS:
            original = observations[name]
            relabeled = replace(
                original,
                intended_artifact=aliases[original.intended_artifact],
                loaded_artifact=aliases[original.loaded_artifact],
                reported_artifact=aliases[original.reported_artifact],
            )
            truth = "binding_fault" if name == "faulty" else "no_binding_fault"
            if (original.intended_artifact != original.loaded_artifact) != (
                truth == "binding_fault"
            ):
                raise ValueError("retained loader truth differs from the declared control")
            for condition in CONDITIONS:
                context = json.loads(
                    json.dumps(
                        build_artifact_binding_context(
                            relabeled, condition=condition
                        ).model_payload()
                    )
                )
                cases.append(
                    {
                        "case_id": f"{name}:{alias_order}:{condition}",
                        "case_kind": name,
                        "alias_order": alias_order,
                        "pair_id": f"artifact-vs-legitimate:{alias_order}"
                        if name in {"faulty", "legitimate_B"}
                        else None,
                        "source_cluster": SOURCE_CLUSTER,
                        "truth": truth,
                        "condition": condition,
                        "context": context,
                        "reference": visible_reference(context),
                    }
                )
    validate_cases(cases)
    return cases


def validate_cases(cases: list[dict[str, Any]]) -> None:
    if (
        len(cases) != 64
        or len({case["case_id"] for case in cases}) != 64
        or {(case["case_kind"], case["alias_order"], case["condition"]) for case in cases}
        != {
            (name, alias, condition)
            for name in CASE_KINDS
            for alias in (0, 1)
            for condition in CONDITIONS
        }
        or {case["source_cluster"] for case in cases} != {SOURCE_CLUSTER}
    ):
        raise ValueError("development census or source scope differs")
    indexed = {(case["case_kind"], case["alias_order"], case["condition"]): case for case in cases}
    for case in cases:
        name, alias, condition = case["case_kind"], case["alias_order"], case["condition"]
        truth = "binding_fault" if name == "faulty" else "no_binding_fault"
        expected_pair = (
            f"artifact-vs-legitimate:{alias}" if name in {"faulty", "legitimate_B"} else None
        )
        reference = visible_reference(case["context"])
        if (
            case["truth"] != truth
            or case["pair_id"] != expected_pair
            or case["reference"] != reference
            or reference["compatible"]
            != (list(BINDING_STATUSES) if condition == "missing_key" else [truth])
        ):
            raise ValueError("case truth, pair or visible reference is inconsistent")
        if (
            name in {"sham", "corrected"}
            and case["context"] != indexed[("healthy", alias, condition)]["context"]
        ):
            raise ValueError("sham or correction changed the complete visible input")
        if (
            name == "legitimate_B"
            and condition == "missing_key"
            and case["context"] != indexed[("faulty", alias, condition)]["context"]
        ):
            raise ValueError("missing-lineage pair has a hidden-world shortcut")
    _check_view_controls(indexed)


def _check_view_controls(indexed: dict[tuple[str, int, str], dict[str, Any]]) -> None:
    """Cross-world distinctions and alias invariance at the visible boundary."""
    for alias in (0, 1):
        if (
            indexed[("faulty", alias, "full")]["context"]
            == indexed[("legitimate_B", alias, "full")]["context"]
        ):
            raise ValueError("full lineage lost its distinguishing witness")
        if (
            indexed[("healthy", alias, "full")]["context"]
            != indexed[("manifest_text_only", alias, "full")]["context"]
        ):
            raise ValueError("untrusted manifest text changed the trusted full input")
    for name in CASE_KINDS:
        if (
            indexed[(name, 0, "missing_key")]["context"]
            != indexed[(name, 1, "missing_key")]["context"]
        ):
            raise ValueError("an alias leaked through the missing-lineage channel")


def retained_development_cases(
    *, root: Path, memory_root: Path
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    source = memory_root / "m4-artifact-binding-development-v2"
    observations = retained_artifact_observations(
        root=root, cell_dir=source, expected_receipt_sha256=RECEIPT_SHA256
    )
    # Hash supplied artifacts without loading/deserializing them. The upstream
    # independent replay has already established the retained source's scope.
    hashes = {path.name: file_sha256(path) for path in sorted(source.iterdir())}
    return cases_from_observations(observations), hashes
