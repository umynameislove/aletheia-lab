from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.model_load_application_analysis import receipt_decision, reference
from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Observation,
    Record,
    Scope,
    completion_monitor,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_handoff_acceptance import check_synthetic_product_view
from aletheia_lab.project.identity import content_sha256

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures/research_acceptance/synthetic_product_view.json"
)


def view() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_committed_synthetic_reference_retains_unknown_family_and_scope() -> None:
    assert (
        content_sha256(FIXTURE.read_bytes())
        == "1df9c117826d2bd5e95df4ae11ff8702eeb63073f177e3b9267187ea98e646d3"
    )
    result = check_synthetic_product_view(view(), view())
    assert result["status"] == "synthetic_canonical_projection_acceptance_pass"
    assert result["independent_families"] is None
    assert result["independent_families_status"] == "not_estimated"
    assert result["product_consumer_integration"] == "not_executed"


@pytest.mark.parametrize(
    "mutation",
    [
        "causal",
        "edge",
        "hidden",
        "dangling",
        "turn",
        "endpoint",
        "node",
        "count",
        "null",
        "boolean",
        "scope",
        "private",
        "disposition",
        "missing",
        "evidence_path",
        "windows_path",
        "duplicate",
        "relation_type",
        "counterevidence",
        "graph_citation",
    ],
)
def test_same_hash_count_fingerprint_cannot_authorize_semantic_mutation(mutation: str) -> None:
    original = view()
    candidate = deepcopy(original)
    if mutation == "causal":
        candidate["result"]["causal_status"] = "verified"
    elif mutation == "edge":
        candidate["graph"]["edges"][0]["kind"] = "CAUSES"
    elif mutation == "hidden":
        candidate["evidence"][0]["visibility"] = "evaluator"
    elif mutation == "dangling":
        candidate["claims"][0]["citation_ids"] = ["foreign-evidence"]
    elif mutation == "turn":
        candidate["conversation"]["turns"][0]["snapshot_id"] = "other-snapshot"
    elif mutation == "endpoint":
        candidate["graph"]["edges"][0]["source"] = "missing-node"
    elif mutation == "node":
        candidate["graph"]["nodes"][0]["source_id"] = "foreign-snapshot"
    elif mutation == "count":
        candidate["result"]["denominators"]["claims"] = 2
    elif mutation == "null":
        candidate["result"]["denominators"]["independent_families"] = 0
    elif mutation == "boolean":
        candidate["result"]["denominators"]["contexts"] = True
        assert candidate == original  # Python equality alone incorrectly accepts bool/int.
    elif mutation == "scope":
        candidate["runtime"]["external_call"] = True
    elif mutation == "private":
        candidate["result"]["raw_hex"] = "PRIVATE_CANARY"
    elif mutation == "disposition":
        candidate["result"]["disposition"] = "supported"
    elif mutation == "missing":
        candidate["conversation"]["turns"][0]["missing_evidence"] = []
    elif mutation == "evidence_path":
        candidate["evidence"][0]["relative_path"] = "../private.json"
    elif mutation == "windows_path":
        candidate["evidence"][0]["relative_path"] = "C:\\private.json"
    elif mutation == "duplicate":
        candidate["graph"]["nodes"][1]["id"] = candidate["graph"]["nodes"][0]["id"]
    elif mutation == "relation_type":
        candidate["graph"]["edges"][0]["kind"] = "CITES"
    elif mutation == "counterevidence":
        candidate["claims"][0]["counterevidence_ids"] = ["foreign-evidence"]
    else:
        candidate["claims"][0]["citation_ids"] = []
    assert original["evidence"][0]["source_sha256"] == candidate["evidence"][0]["source_sha256"]
    assert len(original["claims"]) == len(candidate["claims"])
    with pytest.raises(ValueError):
        check_synthetic_product_view(original, candidate)
    # A bad caller-selected reference is not rescued by equality with itself.
    if mutation not in {"disposition", "missing"}:
        with pytest.raises(ValueError):
            check_synthetic_product_view(candidate, candidate)


def test_redacted_does_not_mean_automatically_invisible() -> None:
    original = view()
    original["evidence"][0]["redacted"] = True
    assert check_synthetic_product_view(original, original)["provider_calls"] == 0


def test_equal_artifact_hashes_do_not_fix_selection_policy_verdict() -> None:
    scope = Scope("synthetic-request", 0)
    a, b = content_sha256(b"synthetic-A"), content_sha256(b"synthetic-B")
    contract = LoadContract("pin_at_acceptance", (a, b))
    selection = Record("selection", scope, "selection", a, "pin", 0, "pin_at_acceptance")
    loaded = Record("load", scope, "load", a, "pin")
    closure = Record("closure", scope, "closure", load_count=1)
    before = Observation(contract, scope, (selection, loaded, closure))
    after = replace(before, records=(replace(selection, phase="resolve_at_load"), loaded, closure))
    assert [r.digest for r in before.records] == [r.digest for r in after.records]
    for checker in (receipt_checker, completion_monitor):
        assert (checker(before).verdict, checker(after).verdict) == ("compliant", "violation")
        cache = Record("cache", scope, "cache_hit", a)
        open_cache = checker(replace(before, records=(cache,)))
        closed_cache = checker(replace(before, records=(cache, replace(closure, load_count=0))))
        assert (open_cache.verdict, open_cache.eligibility) == ("unknown", "undetermined")
        assert (closed_cache.verdict, closed_cache.eligibility) == (None, "no_new_load")


def test_http_outcome_is_separate_from_load_eligibility_and_verdict() -> None:
    row = {
        "scope": "synthetic-load",
        "status": 200,
        "counts": {"loader_calls": 1, "reconstructions": 1},
        "operator_pin_sha256": content_sha256(b"synthetic-B"),
    }
    raw = [{"scope": row["scope"], "raw_hex": b"synthetic-A".hex()}]
    receipts = [{"scope": row["scope"], "descriptor_sha256": content_sha256(b"synthetic-A")}]
    assert reference(row, raw) == receipt_decision(row, receipts) == "violation"
    failed = {**row, "status": 500}
    assert reference(failed, raw) == receipt_decision(failed, receipts) == "unknown"
    for status in (200, 404, 422):
        cache = {
            **row,
            "status": status,
            "counts": {"loader_calls": 0, "reconstructions": 0},
            "operator_pin_sha256": None,
        }
        assert reference(cache, []) == receipt_decision(cache, []) == "no_new_load"
