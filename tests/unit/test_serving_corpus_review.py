from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.evaluation.serving_corpus_review import (
    assignment,
    blinded_packet,
    nominal_agreement,
    population_reliability,
    reliability,
    submission_template,
)


def packet():
    return blinded_packet(
        [
            {"family_id": str(i), "sources": ["public-source"], "draft_answer": "secret"}
            for i in range(8)
        ],
        assignment([str(i) for i in range(8)], "locked"),
    )


def filled(value, rater):
    result = submission_template(value)
    result.update(
        rater_id=rater,
        human=True,
        qualification_completed=True,
        independent_before_adjudication=True,
    )
    for row in result["labels"]:
        row.update(
            primary_boundary="artifact_state", source_tier="confirmed_mechanism", rationale="source"
        )
    return result


def test_assignment_order_does_not_depend_on_inputs_or_draft_labels():
    assert assignment(list("abcdefghi"), "salt") == assignment(list("ihgfedcba"), "salt")
    assert len(assignment(list("abcdefghi"), "salt")) == 3
    assert "draft_answer" not in str(packet())


def test_agreement_known_hand_computed_table_and_degenerate_labels():
    result = nominal_agreement(list("aabb"), list("abbb"))
    assert result["agreement"] == 0.75
    assert result["expected_agreement"] == 0.5
    assert result["cohen_kappa"] == 0.5
    assert nominal_agreement(["a"], ["a"])["cohen_kappa"] is None


def test_blank_and_machine_templates_cannot_produce_human_reliability():
    value = packet()
    with pytest.raises(ValueError, match="human"):
        reliability(value, submission_template(value), filled(value, "second"))
    machine = filled(value, "machine")
    machine["human"] = False
    with pytest.raises(ValueError, match="human"):
        reliability(value, machine, filled(value, "second"))


@pytest.mark.parametrize(
    "mutation", ["duplicate", "missing", "ontology", "rationale", "same_rater", "tampered_packet"]
)
def test_changed_or_nonindependent_submissions_fail_closed(mutation):
    value = packet()
    first, second = filled(value, "first"), filled(value, "second")
    if mutation == "duplicate":
        second["labels"][1] = copy.deepcopy(second["labels"][0])
    elif mutation == "missing":
        second["labels"].pop()
    elif mutation == "ontology":
        second["labels"][0]["primary_boundary"] = "novel-label"
    elif mutation == "rationale":
        second["labels"][0]["rationale"] = ""
    elif mutation == "same_rater":
        second["rater_id"] = "first"
    else:
        value["families"][0]["sources"] = ["changed"]
    with pytest.raises(ValueError):
        reliability(value, first, second)


def test_actual_locked_pairs_keep_disagreement_and_do_not_adjudicate():
    value = packet()
    first, second = filled(value, "first"), filled(value, "second")
    second["labels"][0]["primary_boundary"] = "unresolved"
    result = reliability(value, first, second)
    assert len(result["disagreements"]) == 1
    assert result["primary_boundary"]["agreement"] == 0.5
    assert result["adjudication"].startswith("not performed")


def population_pair():
    families = [{"family_id": str(i), "sources": ["public-source"]} for i in range(44)]
    population = blinded_packet(families, [row["family_id"] for row in families])
    paired = blinded_packet(families, assignment([row["family_id"] for row in families], "locked"))
    return population, paired


def test_full_population_and_locked_subset_have_only_eleven_agreement_pairs():
    population, paired = population_pair()
    first, second = filled(population, "first"), filled(paired, "second")
    second["labels"][0]["primary_boundary"] = "unresolved"
    before = copy.deepcopy((population, paired, first, second))
    result = population_reliability(population, paired, first, second)
    assert result["population_family_count"] == 44
    assert result["primary_boundary"]["denominator"] == 11
    assert result["primary_boundary"]["agreement"] == pytest.approx(10 / 11)
    assert result["unpaired_population_count"] == 33
    assert result["submission_sha256"] == [digest(first), digest(second)]
    assert (population, paired, first, second) == before


@pytest.mark.parametrize(
    "mutation", ["too_small", "foreign", "sources", "ontology", "same_rater", "blank"]
)
def test_population_subset_cannot_reassign_sources_or_fabricate_independence(mutation):
    population, paired = population_pair()
    first, second = filled(population, "first"), filled(paired, "second")
    if mutation in {"too_small", "foreign", "sources", "ontology"}:
        if mutation == "too_small":
            paired["families"].pop()
        elif mutation == "foreign":
            paired["families"][0]["family_id"] = "foreign"
        elif mutation == "sources":
            paired["families"][0]["sources"] = ["other-source"]
        else:
            paired["ontology"]["source_tier"].append("new-category")
        paired["packet_sha256"] = digest(
            {key: value for key, value in paired.items() if key != "packet_sha256"}
        )
        second = filled(paired, "second")
    elif mutation == "same_rater":
        second["rater_id"] = "first"
    else:
        first = submission_template(population)
    with pytest.raises(ValueError):
        population_reliability(population, paired, first, second)


def test_cli_accepts_two_unchanged_packets_with_different_assignment_sizes(tmp_path):
    population, paired = population_pair()
    first, second = filled(population, "synthetic-first"), filled(paired, "synthetic-second")
    paths = []
    for name, value in zip(
        ("population", "paired", "first", "second"),
        (population, paired, first, second),
        strict=True,
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "agreement.json"
    before = [path.read_bytes() for path in paths]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/serving_corpus_review.py"),
            "--population-packet",
            str(paths[0]),
            "--packet",
            str(paths[1]),
            "--first",
            str(paths[2]),
            "--second",
            str(paths[3]),
            "--output",
            str(destination),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(destination.read_bytes())
    assert result["primary_boundary"]["denominator"] == 11
    assert result["population_packet_sha256"] == population["packet_sha256"]
    assert result["submission_sha256"] == [digest(first), digest(second)]
    assert [path.read_bytes() for path in paths] == before
