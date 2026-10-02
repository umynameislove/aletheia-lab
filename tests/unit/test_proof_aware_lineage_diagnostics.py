"""Observable defect taxonomy, dependent-format pairs and read-only replay."""

from __future__ import annotations

import runpy
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import proof_aware_lineage_diagnostics as diagnostics
from aletheia_lab.evaluation.compositional_lineage import POLICIES, Decision, baseline_decision
from aletheia_lab.evaluation.proof_aware_lineage_transfer_cases import transfer_cases
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import _write

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cases():
    return transfer_cases()


def _context(cases, motif):
    return next(case["context"] for case in cases if case["motif"] == motif)


def _decision(**fields):
    return Decision.model_validate(
        {
            "decision": "abstain",
            "basis": "underdetermined",
            "next_check": "none",
            "cited_records": [],
            **fields,
        }
    )


@pytest.mark.parametrize(
    "motif,category",
    [
        ("direct_pair_fault", "valid_resolution"),
        ("only_requested_endpoint", "valid_query"),
        ("requested_endpoint_conflict", "valid_conflict"),
    ],
)
def test_valid_categories(cases, motif, category):
    context = _context(cases, motif)
    row = diagnostics.diagnose_view(context, baseline_decision(context))
    assert row["category"] == category
    assert row["raw_action_success"] and row["guard_action_success"]
    assert row["guard_origin"] == "model" and row["defect_flags"] == []


def test_safe_abstention_is_not_logical_failure_or_query_success(cases):
    row = diagnostics.diagnose_view(_context(cases, "only_requested_endpoint"), _decision())
    assert row["category"] == row["guard_category"] == "safe_simple_abstention"
    assert not row["raw_action_success"] and not row["guard_action_success"]
    assert row["defect_flags"] == []


@pytest.mark.parametrize(
    "motif,fields,category",
    [
        (
            "only_requested_endpoint",
            {"decision": "binding_fault", "basis": "entailed"},
            "commitment_on_ambiguous_evidence",
        ),
        (
            "only_requested_endpoint",
            {
                "decision": "flag_conflict",
                "basis": "inconsistent",
                "next_check": "reconcile_records",
            },
            "false_conflict",
        ),
        (
            "requested_endpoint_conflict",
            {
                "decision": "flag_conflict",
                "basis": "inconsistent",
                "next_check": "reconcile_records",
            },
            "conflict_flag_invalid_certificate_or_contract",
        ),
        ("requested_endpoint_conflict", {}, "missed_conflict"),
    ],
)
def test_remaining_categories_are_distinct_observed_failures(cases, motif, fields, category):
    assert (
        diagnostics.diagnose_view(_context(cases, motif), _decision(**fields))["category"]
        == category
    )


def test_scope_and_authority_defects_overlap_certificate_failure(cases):
    context = _context(cases, "direct_pair_fault")
    untrusted = next(
        record["id"] for record in context["records"] if record["kind"] == "performance_report"
    )
    answer = _decision(decision="binding_fault", basis="entailed", cited_records=[untrusted])
    assert diagnostics.diagnose_view(context, answer)["defect_flags"] == [
        "citation_not_valid_certificate",
        "untrusted_or_off_scope_citation",
    ]


def test_identified_refusal_differs_from_wrong_status(cases):
    context = _context(cases, "direct_pair_fault")
    wrong = _decision(decision="no_binding_fault", basis="entailed")
    row = diagnostics.diagnose_view(context, wrong)
    assert row["category"] == "wrong_endpoint_status"
    assert "endpoint_not_entailed_by_full_context" in row["defect_flags"]
    assert row["guard_category"] == "missed_identified_resolution"
    assert row["guard_origin"] == "rejected" and not row["guard_action_success"]
    assert (
        diagnostics.diagnose_view(context, _decision())["category"]
        == "missed_identified_resolution"
    )


def test_missing_proof_differs_from_nonexistent_citation(cases):
    context = _context(cases, "direct_pair_fault")
    good = baseline_decision(context)
    requested = next(
        record["id"] for record in context["records"] if record["kind"] == "requested_endpoint"
    )
    incomplete = good.model_copy(update={"cited_records": [requested]})
    row = diagnostics.diagnose_view(context, incomplete)
    assert row["category"] == "correct_status_invalid_certificate_or_contract"
    assert row["defect_flags"] == ["citation_not_valid_certificate"]
    assert row["guard_origin"] == "proof_reconstructed" and row["guard_action_success"]
    fabricated = good.model_copy(update={"cited_records": ["not-a-record"]})
    assert "unknown_citation" in diagnostics.diagnose_view(context, fabricated)["defect_flags"]
    duplicate = good.model_copy(update={"cited_records": good.cited_records * 2})
    assert "duplicate_citation" in diagnostics.diagnose_view(context, duplicate)["defect_flags"]


def test_redundant_check_is_action_defect_not_wrong_endpoint(cases):
    row = diagnostics.diagnose_view(
        _context(cases, "only_requested_endpoint"),
        _decision(decision="check_evidence", next_check="requested_endpoint"),
    )
    assert row["category"] == "invalid_ambiguous_action_contract"
    assert row["defect_flags"] == ["check_not_minimum_guaranteed"]
    assert row["guard_category"] == "valid_query" and row["guard_origin"] == "resolver_query"


def test_global_conflict_overrides_locally_valid_path_certificate(cases):
    context = _context(cases, "off_path_manifest_conflict")
    path = [
        record["id"]
        for record in context["records"]
        if record["kind"] in {"requested_endpoint", "loaded_endpoint"}
    ]
    row = diagnostics.diagnose_view(
        context, _decision(decision="no_binding_fault", basis="entailed", cited_records=path)
    )
    assert row["category"] == "commitment_on_conflicting_evidence"
    assert row["guard_category"] == "valid_conflict" and row["guard_origin"] == "resolver_conflict"


def test_representation_differences_separate_action_from_citation_order():
    left = _decision(cited_records=["r00", "r07"])
    assert diagnostics._comparison(left, left) == "exact_same"
    assert (
        diagnostics._comparison(left, _decision(cited_records=["r07", "r00"]))
        == "citation_order_only"
    )
    assert (
        diagnostics._comparison(left, _decision(cited_records=["r00"]))
        == "citation_content_differs"
    )
    assert (
        diagnostics._comparison(left, _decision(next_check="loaded_endpoint"))
        == "status_basis_or_action_differs"
    )
    assert diagnostics._comparison(left, None) == "unavailable_pair"


def test_complete_denominator_pairing_and_baseline_counterfactual_do_not_mutate(cases):
    before = deepcopy(cases)
    proposals = {
        (case["case_id"], arm): baseline_decision(case["context"])
        for case in cases
        for arm in POLICIES
    }
    report = diagnostics.diagnose(cases, proposals)
    for arm in POLICIES:
        row = report["arms"][arm]
        assert row["raw_action_success"] == row["guard_action_success"] == 48
        assert row["raw_category_counts"] == {
            "valid_conflict": 16,
            "valid_query": 16,
            "valid_resolution": 16,
        }
        assert len(row["paired_motif_diagnostics"]) == 24
        assert all(value["guard_joint_success"] for value in row["paired_motif_diagnostics"])
        assert row["guard_success_origins"] == {"model": 48}
    counterfactual = report["secondary_baseline_case_sensitivity"]
    assert counterfactual["original_decision_counts"] == {"abstain": 48}
    assert counterfactual["lowercase_report_decision_counts"] == {"binding_fault": 48}
    assert counterfactual["unchanged_visible_semantics_count"] == 48
    assert cases == before and report["provider_calls"] == 0
    missing = {key: None for key in proposals}
    assert diagnostics.diagnose(cases, missing)["arms"][POLICIES[0]]["raw_category_counts"] == {
        "unavailable": 48
    }
    proposals.pop(next(iter(proposals)))
    with pytest.raises(ValueError, match="complete planned frame"):
        diagnostics.diagnose(cases, proposals)


def test_replay_verifies_twice_detects_mutation_and_never_invokes_provider(
    tmp_path, monkeypatch, cases
):
    directory = tmp_path / "pilot"
    directory.mkdir()
    _write(directory / "cases.json", cases)
    receipt = {"verification": "pass", "analysis_sha256": "a" * 64}
    verifications = []

    def verify(**kwargs):
        verifications.append(kwargs)
        return dict(receipt)

    monkeypatch.setattr(diagnostics.pilot, "verify", verify)
    monkeypatch.setattr(diagnostics.pilot, "_records", lambda directory, cases: {})

    def forbidden(*args, **kwargs):
        pytest.fail("diagnostic replay must never invoke a provider")

    monkeypatch.setattr(diagnostics.pilot, "execute", forbidden)
    report = diagnostics.replay_diagnostics(root=ROOT, memory_root=tmp_path, directory=directory)
    assert len(verifications) == 2 and report["verification"] == "pass"
    assert report["source_analysis_sha256"] == receipt["analysis_sha256"]
    assert len(report["source_receipt_sha256"]) == 64

    def mutated(**kwargs):
        if len(verifications) % 2:
            _write(directory / "unexpected.json", {})
        return verify(**kwargs)

    monkeypatch.setattr(diagnostics.pilot, "verify", mutated)
    with pytest.raises(ValueError, match="changed during"):
        diagnostics.replay_diagnostics(root=ROOT, memory_root=tmp_path, directory=directory)


def test_completed_call_is_parsed_and_no_raw_text_is_exported(tmp_path, monkeypatch, cases):
    directory = tmp_path / "pilot"
    directory.mkdir()
    _write(directory / "cases.json", cases)
    monkeypatch.setattr(diagnostics.pilot, "verify", lambda **kwargs: {"analysis_sha256": "a" * 64})
    call = DevelopmentCall(
        status="completed",
        payload_json=_decision().model_dump_json(),
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0,
        latency_seconds=0,
    )
    record = {"call": call.model_dump(mode="json")}
    monkeypatch.setattr(
        diagnostics.pilot,
        "_records",
        lambda directory, cases: {
            request["request_id"]: record for request in diagnostics.pilot.request_frame(cases)
        },
    )
    report = diagnostics.replay_diagnostics(root=ROOT, memory_root=tmp_path, directory=directory)
    assert "payload_json" not in repr(report) and "visible_context" not in repr(report)
    assert report["arms"][POLICIES[0]]["raw_category_counts"] == {
        "missed_conflict": 16,
        "missed_identified_resolution": 16,
        "safe_simple_abstention": 16,
    }


def test_cli_rejects_source_output_and_sanitizes_failure(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "pilot"
    directory.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose",
            "--memory-root",
            str(tmp_path),
            "--pilot-dir",
            str(directory),
            "--output",
            str(directory / "report.json"),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(
            str(ROOT / "scripts/analyze_proof_aware_lineage_transfer.py"), run_name="__main__"
        )
    assert exc.value.code == 1 and list(directory.iterdir()) == []
    assert "lineage_diagnostics_failed_closed" in capsys.readouterr().out


def test_cli_private_output_is_create_only(tmp_path, monkeypatch, capsys):
    output = tmp_path / "diagnostics.json"
    report = {"verification": "pass", "provider_calls": 0}
    monkeypatch.setattr(diagnostics, "replay_diagnostics", lambda **kwargs: report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose",
            "--memory-root",
            str(tmp_path),
            "--pilot-dir",
            str(tmp_path / "source"),
            "--output",
            str(output),
        ],
    )
    cli = ROOT / "scripts/analyze_proof_aware_lineage_transfer.py"
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(cli), run_name="__main__")
    assert exc.value.code == 0 and output.is_file()
    before = output.read_bytes()
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(cli), run_name="__main__")
    assert exc.value.code == 1 and output.read_bytes() == before
    assert "lineage_diagnostics_failed_closed" in capsys.readouterr().out
