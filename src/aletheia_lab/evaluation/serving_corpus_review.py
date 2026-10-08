"""Blinded sampling and human agreement for a source-grounded serving corpus.

Machine screening drafts are not human submissions. Agreement is computed on
locked, identical family assignments, before adjudication; it is not accuracy.
"""

from __future__ import annotations

from collections import Counter
from math import ceil
from typing import Any

from aletheia_lab.evaluation.request_model_audit import digest

BOUNDARIES = (
    "artifact_state",
    "implementation_realization",
    "request_association",
    "dependency_closure",
    "routing_selection",
    "protocol_representation",
    "lifecycle_availability",
    "unresolved",
)
TIERS = ("confirmed_mechanism", "unresolved_symptom", "authored_derived")


def assignment(family_ids: list[str], salt: str, fraction: float = 0.25) -> list[str]:
    """Stable selection without consulting first-coder or system labels."""
    if (
        not family_ids
        or len(family_ids) != len(set(family_ids))
        or not all(isinstance(value, str) and value for value in family_ids)
        or not salt
        or not 0.25 <= fraction <= 1
    ):
        raise ValueError("unique families and at least one-quarter assignment required")
    return sorted(family_ids, key=lambda value: (digest([salt, value]), value))[
        : ceil(fraction * len(family_ids))
    ]


def blinded_packet(families: list[dict[str, Any]], selected: list[str]) -> dict[str, Any]:
    """Only sources and IDs cross the blind boundary; no draft verdicts."""
    index = {row["family_id"]: row for row in families}
    if len(index) != len(families) or len(set(selected)) != len(selected):
        raise ValueError("duplicate family identity")
    if not selected or not set(selected) <= set(index):
        raise ValueError("assignment differs from eligible family census")
    packet = {
        "schema": "serving-corpus-coding/v1",
        "ontology": {"primary_boundary": list(BOUNDARIES), "source_tier": list(TIERS)},
        "families": [{"family_id": key, "sources": index[key]["sources"]} for key in selected],
    }
    return {**packet, "packet_sha256": digest(packet)}


def submission_template(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_sha256": packet["packet_sha256"],
        "rater_id": None,
        "human": None,
        "qualification_completed": None,
        "independent_before_adjudication": None,
        "labels": [
            {
                "family_id": row["family_id"],
                "primary_boundary": None,
                "source_tier": None,
                "rationale": None,
            }
            for row in packet["families"]
        ],
    }


def validate_submission(
    packet: dict[str, Any], submission: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    body = {key: value for key, value in packet.items() if key != "packet_sha256"}
    if digest(body) != packet["packet_sha256"]:
        raise ValueError("coding packet identity differs")
    if submission.get("packet_sha256") != packet["packet_sha256"]:
        raise ValueError("submission assignment identity differs")
    attestations = ("human", "qualification_completed", "independent_before_adjudication")
    if not all(submission.get(key) is True for key in attestations):
        raise ValueError("qualified independent human submission required")
    if not isinstance(submission.get("rater_id"), str) or not submission["rater_id"].strip():
        raise ValueError("human rater identity required")
    labels = submission.get("labels")
    if not isinstance(labels, list) or len(labels) != len(packet["families"]):
        raise ValueError("complete assignment required")
    rows = {row["family_id"]: row for row in labels}
    if len(rows) != len(labels) or set(rows) != {row["family_id"] for row in packet["families"]}:
        raise ValueError("duplicate or changed family assignment")
    for row in rows.values():
        if row["primary_boundary"] not in BOUNDARIES or row["source_tier"] not in TIERS:
            raise ValueError("unsupported coding ontology")
        if not isinstance(row["rationale"], str) or not row["rationale"].strip():
            raise ValueError("source-grounded coding rationale required")
    return rows


def nominal_agreement(first: list[str], second: list[str]) -> dict[str, Any]:
    if not first or len(first) != len(second):
        raise ValueError("paired nonempty labels required")
    size = len(first)
    left, right = Counter(first), Counter(second)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / size
    expected = sum(left[label] * right[label] for label in set(left) | set(right)) / size**2
    return {
        "denominator": size,
        "agreement": observed,
        "expected_agreement": expected,
        "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else None,
        "kappa_undefined": expected == 1,
        "confusion": [
            {"first": a, "second": b, "count": count}
            for (a, b), count in sorted(Counter(zip(first, second, strict=True)).items())
        ],
    }


def reliability(
    packet: dict[str, Any], first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    left, right = validate_submission(packet, first), validate_submission(packet, second)
    if first["rater_id"] == second["rater_id"]:
        raise ValueError("two distinct actual humans required")
    keys = [row["family_id"] for row in packet["families"]]
    return {
        "packet_sha256": packet["packet_sha256"],
        "submission_sha256": [digest(first), digest(second)],
        **_paired_results(keys, left, right),
    }


def _paired_results(
    keys: list[str], left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "primary_boundary": nominal_agreement(
            [left[key]["primary_boundary"] for key in keys],
            [right[key]["primary_boundary"] for key in keys],
        ),
        "source_tier": nominal_agreement(
            [left[key]["source_tier"] for key in keys],
            [right[key]["source_tier"] for key in keys],
        ),
        "disagreements": [
            {"family_id": key, "first": left[key], "second": right[key]}
            for key in keys
            if any(
                left[key][field] != right[key][field]
                for field in ("primary_boundary", "source_tier")
            )
        ],
        "adjudication": "not performed by agreement computation",
        "limitations": "Human attestations are declarations, not independently authenticated identities; kappa is not validity or representativeness.",
    }


def population_reliability(
    population_packet: dict[str, Any],
    paired_packet: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    """Compare a locked full submission with the locked independent subset.

    Original submissions and packet identities are never rewritten. Unpaired
    population labels do not inflate the paired agreement denominator.
    """
    left = validate_submission(population_packet, first)
    right = validate_submission(paired_packet, second)
    if first["rater_id"] == second["rater_id"]:
        raise ValueError("two distinct actual humans required")
    population = {row["family_id"]: row for row in population_packet["families"]}
    keys = [row["family_id"] for row in paired_packet["families"]]
    if len(keys) < ceil(len(population) * 0.25) or not set(keys) <= set(population):
        raise ValueError("locked paired subset must cover at least one quarter")
    if (
        population_packet["schema"] != paired_packet["schema"]
        or population_packet["ontology"] != paired_packet["ontology"]
        or any(row != population[row["family_id"]] for row in paired_packet["families"])
    ):
        raise ValueError("paired source or ontology differs from locked population")
    return {
        "population_packet_sha256": population_packet["packet_sha256"],
        "packet_sha256": paired_packet["packet_sha256"],
        "submission_sha256": [digest(first), digest(second)],
        "population_family_count": len(population),
        "paired_family_ids": keys,
        "unpaired_population_count": len(population) - len(keys),
        **_paired_results(keys, left, right),
    }
