from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_research_acceptance as acceptance
from aletheia_lab.evaluation.model_load_provenance import document_digest


def comparison() -> dict[str, Any]:
    return {
        "correct_identified": 1,
        "false_compliance": 0,
        "false_violation": 0,
        "verdict_counts": {"compliant": 1, "unknown": 1},
        "planned_denominator": 2,
    }


def payload(kind: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": acceptance.SCHEMAS[kind],
        "provider_calls": 0,
        "raw_hex": "PRIVATE_CANARY",
        "code_bindings": {"PRIVATE_PATH": "PRIVATE_CANARY"},
    }
    if kind in {"observability", "provenance"}:
        observed = comparison()
        observed["verdict_counts"]["no_new_load"] = 1
        result["summary"] = {
            "episode_count": 3,
            "eligible_load_attempts": 2,
            "no_new_load_attempts": 1,
            "planned_load_attempts": 2,
            "cache_only_attempts": 1,
            "technical_failure_count": 0,
            "comparisons": {"after": {"S": observed, "T": observed}}
            if kind == "observability"
            else {"S": comparison(), "T": comparison(), "P": comparison()},
        }
        result["disposition"] = (
            "narrow_controlled_feasibility_no_new_method_evidence"
            if kind == "observability"
            else "bounded_capture_finding_no_new_checker_advantage"
        )
    elif kind == "validation":
        result["reserved_native_entries"] = 4
        result["analysis"] = {
            "planned_slots": 3,
            "planned_load_slots": 2,
            "planned_cache_slots": 1,
            "status_counts": {"completed": 2, "technical_failure": 1},
            "retained_native_entries": 2,
            "loader_format_strata": 2,
            "independent_deployments": 0,
            "comparisons": {"after": {"S": comparison(), "T": comparison(), "P": comparison()}},
            "disposition": "incomplete_validation",
            "prevention": {
                "planned_opportunities": 2,
                "unavailable": 1,
                "blocked_proposals": 1,
                "false_blocks": 0,
                "compliant_load_completions": 0,
                "residual_violations": 0,
                "cache_only": 0,
                "blocked_without_load": 1,
            },
        }
    elif kind == "closeout":
        result.update(
            {
                "validation_results_sha256": "a" * 64,
                "validation_seal_sha256": "b" * 64,
                "analysis_class": "exposed_post_result_descriptive_ablation",
                "cutoffs": {"after": {"unmodified": {"S": comparison(), "T": comparison()}}},
            }
        )
    elif kind == "runtime":
        result.update(
            {
                "status": "development_incomplete_failures_preserved",
                "native_cost": {
                    "planned_workers": 1,
                    "planned_native_loads": 3,
                    "known_native_entries": 2,
                    "known_native_completed": 1,
                    "status_counts": {"completed": 1, "failed": 1, "worker_failed_unknown": 1},
                    "all_native_entry_counts_known": False,
                },
                "concurrency": {
                    "executed_configuration_runs": 2,
                    "primary_requests": 1,
                    "primary_attempts": 2,
                    "comparisons": [
                        {
                            "service": "children_only",
                            "horizon": 2,
                            "comparison_available": False,
                            "matched_immediate_decisions": False,
                            "matched_audit_decisions_and_availability": False,
                        }
                    ],
                },
            }
        )
    else:
        result.update(
            {
                "summary": {
                    "planned_http_operations": 5,
                    "http_operation_count": 5,
                    "http_failure_count": 1,
                    "loader_calls": 1,
                    "reconstruction_entries": 1,
                    "source_cluster_count": 1,
                    "native_census_complete": True,
                    "failed_arms_with_unknown_partial_native_census": 0,
                    "disposition": "bounded_application_capture_transfer_no_new_checker_advantage",
                },
                "comparison": {
                    "reference_counts": {"violation": 1, "no_new_load": 2},
                    "operation_denominator": 3,
                    "planned_operation_denominator": 3,
                    "unscored_planned_operations": 0,
                    "decided_load_operations": 1,
                    "failed_explicit_load_operations": 1,
                    "unknown_load_operations": 0,
                    "S_reference_agreement": 3,
                    "P_reference_agreement": 3,
                },
            }
        )
    return result


def write_report(tmp_path: Path, kind: str, data: dict[str, Any]) -> tuple[Path, Path, str]:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    field = "results_sha256" if kind == "validation" else "report_sha256"
    data.pop(field, None)
    identity = document_digest(data)
    path = tmp_path / "private.json"
    path.write_text(json.dumps({**data, field: identity}), encoding="utf-8")
    return root, path, identity


@pytest.mark.parametrize("kind", tuple(acceptance.SCHEMAS))
def test_identity_only_is_allowlisted_immutable_and_not_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, path, identity = write_report(tmp_path, kind, payload(kind))
    before = path.read_bytes()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("identity-only invoked historical replay")

    monkeypatch.setattr(acceptance, "_replay", forbidden)
    result = acceptance.accept_receipt(
        root, path, kind, identity, replay=False, parent_results_sha256="a" * 64
    )
    encoded = json.dumps(result)
    assert "PRIVATE" not in encoded and "raw_hex" not in encoded and str(tmp_path) not in encoded
    assert path.read_bytes() == before
    assert result["status"] == "pinned_summary_only_not_replayed"
    assert result["historical_disposition"] in acceptance._DISPOSITIONS[kind]
    assert result["retained_signatures_independently_reverified"] is False
    assert result["scientific_admission_changed"] is False
    assert result["product_consumer_integration"] == "not_executed"
    assert result["native_loads_replayed"] == result["provider_calls"] == 0


def test_units_failures_and_cache_are_not_pooled(tmp_path: Path) -> None:
    root, path, identity = write_report(tmp_path, "observability", payload("observability"))
    result = acceptance.accept_receipt(root, path, "observability", identity, replay=False)[
        "summary"
    ]
    assert result["load_attempts"] == 2 and result["no_new_load_attempts"] == 1
    assert result["comparisons"][0]["denominator"] == 2
    assert result["comparisons"][0]["verdict_counts_unit"] == "authored_episode"
    assert result["technical_failure_count"] is None
    root, path, identity = write_report(tmp_path, "validation", payload("validation"))
    result = acceptance.accept_receipt(root, path, "validation", identity, replay=False)["summary"]
    assert result["terminal_status_counts"]["technical_failure"] == 1
    assert result["planned_slots"] == 3 and result["comparisons"][0]["denominator"] == 2


@pytest.mark.parametrize("mutation", ["self_hash", "schema", "provider", "disposition"])
def test_rehashed_artifact_cannot_replace_caller_accepted_identity(
    tmp_path: Path, mutation: str
) -> None:
    data = payload("observability")
    root, path, identity = write_report(tmp_path, "observability", deepcopy(data))
    if mutation == "self_hash":
        data["summary"]["eligible_load_attempts"] = 99
    elif mutation == "schema":
        data["schema_version"] = "different"
    elif mutation == "provider":
        data["provider_calls"] = 1
    else:
        data["disposition"] = "scientific_superiority"
    write_report(tmp_path, "observability", data)
    with pytest.raises(ValueError):
        acceptance.accept_receipt(root, path, "observability", identity, replay=False)


@pytest.mark.parametrize("value", [True, False, None, -1, 1.0, "1"])
def test_missing_or_boolean_count_cannot_become_measurement(value: Any) -> None:
    with pytest.raises(ValueError):
        acceptance.count(value)


@pytest.mark.parametrize(
    "raw",
    [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b"[]", b"PRIVATE_ERROR"],
)
def test_reader_rejects_duplicate_nonfinite_and_nonobjects(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        acceptance.read_document(path)


def test_reader_bounds_and_symlink_scope(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b'{"valid":1}')
    with pytest.raises(ValueError):
        acceptance.read_document(path, limit=3)
    for limit in (0, -1, True, acceptance.MAX_DOCUMENT_BYTES + 1):
        with pytest.raises(ValueError):
            acceptance.read_document(path, limit=limit)
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ValueError):
        acceptance.read_document(link)


@pytest.mark.parametrize(
    "key",
    [
        "all_native_entry_counts_known",
        "comparison_available",
        "matched_immediate_decisions",
        "matched_audit_decisions_and_availability",
    ],
)
@pytest.mark.parametrize("value", [None, 1, "PRIVATE_CANARY", {"private": "PRIVATE_CANARY"}])
def test_runtime_boolean_projection_is_not_a_leak(tmp_path: Path, key: str, value: Any) -> None:
    data = payload("runtime")
    target = (
        data["native_cost"]
        if key == "all_native_entry_counts_known"
        else data["concurrency"]["comparisons"][0]
    )
    target[key] = value
    root, path, identity = write_report(tmp_path, "runtime", data)
    with pytest.raises((ValueError, TypeError)):
        acceptance.accept_receipt(root, path, "runtime", identity, replay=False)


def test_parent_and_replay_failure_do_not_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, identity = write_report(tmp_path, "closeout", payload("closeout"))
    with pytest.raises(ValueError):
        acceptance.accept_receipt(root, path, "closeout", identity, replay=False)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("SDK unavailable")

    monkeypatch.setattr(acceptance, "_replay", failed)
    with pytest.raises(RuntimeError):
        acceptance.accept_receipt(root, path, "closeout", identity, parent_results_sha256="a" * 64)


def test_replay_checks_before_projection_and_detects_source_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, identity = write_report(tmp_path, "provenance", payload("provenance"))
    calls = []

    def replay(*args: Any) -> None:
        calls.append(args[3])

    monkeypatch.setattr(acceptance, "_replay", replay)
    result = acceptance.accept_receipt(root, path, "provenance", identity)
    assert calls == ["provenance"]
    assert result["fresh_local_rule_checks"] is True
    assert result["status"] == "exact_historical_replay_pass"

    def mutate(*args: Any) -> None:
        path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(acceptance, "_replay", mutate)
    with pytest.raises(ValueError, match="mutated"):
        acceptance.accept_receipt(root, path, "provenance", identity)


@pytest.mark.parametrize("kind", ["observability", "provenance", "application", "runtime"])
def test_replay_dispatch_uses_existing_verifier_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from aletheia_lab.evaluation import (
        model_load_application_analysis,
        model_load_observability,
        model_load_provenance_study,
        model_load_runtime_cost,
    )

    modules = {
        "observability": (model_load_observability, "verify_report"),
        "provenance": (model_load_provenance_study, "verify_report"),
        "application": (model_load_application_analysis, "verify"),
        "runtime": (model_load_runtime_cost, "verify_report"),
    }
    calls = []

    def verify(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    module, name = modules[kind]
    monkeypatch.setattr(module, name, verify)
    acceptance._replay(tmp_path, tmp_path / "input.json", {}, kind, None, None)
    assert len(calls) == 1


def test_validation_and_closeout_require_reconstructed_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aletheia_lab.evaluation import model_load_evidence_analysis, model_load_validation_run

    data = payload("closeout")
    monkeypatch.setattr(
        model_load_validation_run,
        "verify",
        lambda *args: {"results_sha256": "a" * 64, "seal_sha256": "b" * 64},
    )
    monkeypatch.setattr(model_load_validation_run, "read_signed", lambda *args: {"rows": []})
    monkeypatch.setattr(
        model_load_evidence_analysis, "analyze_rows", lambda rows: {"cutoffs": data["cutoffs"]}
    )
    acceptance._replay(
        tmp_path, tmp_path / "input.json", data, "closeout", tmp_path / "plan", tmp_path
    )
    data["cutoffs"]["after"]["unmodified"]["S"]["correct_identified"] = 2
    monkeypatch.setattr(
        model_load_evidence_analysis,
        "analyze_rows",
        lambda rows: {"cutoffs": payload("closeout")["cutoffs"]},
    )
    with pytest.raises(ValueError):
        acceptance._replay(
            tmp_path, tmp_path / "input.json", data, "closeout", tmp_path / "plan", tmp_path
        )
    with pytest.raises(ValueError):
        acceptance._replay(
            tmp_path,
            tmp_path / "input.json",
            {"results_sha256": "c" * 64},
            "validation",
            tmp_path / "plan",
            tmp_path,
        )
    with pytest.raises(ValueError):
        acceptance._replay(tmp_path, tmp_path / "input.json", {}, "validation", None, None)


@pytest.mark.parametrize("parent", ["PRIVATE_CANARY", "a" * 63, "A" * 64, "g" * 64])
def test_parent_identity_cannot_be_used_as_private_text_channel(
    tmp_path: Path, parent: str
) -> None:
    data = payload("closeout")
    data["validation_results_sha256"] = parent
    root, path, identity = write_report(tmp_path, "closeout", data)
    with pytest.raises(ValueError):
        acceptance.accept_receipt(
            root, path, "closeout", identity, replay=False, parent_results_sha256=parent
        )


@pytest.mark.parametrize(
    "key",
    [
        "http_operation_count",
        "http_failure_count",
        "reconstruction_entries",
        "decided_load_operations",
        "unknown_load_operations",
        "S_reference_agreement",
        "planned_operation_denominator",
    ],
)
def test_application_projection_rejects_inconsistent_census(tmp_path: Path, key: str) -> None:
    data = payload("application")
    target = data["summary"] if key in data["summary"] else data["comparison"]
    target[key] = 99
    root, path, identity = write_report(tmp_path, "application", data)
    with pytest.raises(ValueError):
        acceptance.accept_receipt(root, path, "application", identity, replay=False)


def test_unknown_application_native_census_is_not_measured_zero(tmp_path: Path) -> None:
    data = payload("application")
    data["summary"].update(
        {
            "native_census_complete": False,
            "failed_arms_with_unknown_partial_native_census": 1,
            "loader_calls": 0,
            "reconstruction_entries": 0,
            "disposition": "incomplete_or_failed_application_development",
        }
    )
    root, path, identity = write_report(tmp_path, "application", data)
    result = acceptance.accept_receipt(root, path, "application", identity, replay=False)["summary"]
    assert result["native_census_complete"] is False
    assert result["known_native_loader_calls"] == 0
    assert result["failed_arms_with_unknown_partial_native_census"] == 1
    assert "native_loader_calls" not in result


@pytest.mark.parametrize(
    "mutation",
    [
        "denominator",
        "histogram",
        "false",
        "definite",
        "cache",
        "terminal",
        "cutoff",
        "comparator",
        "disposition",
        "provider",
    ],
)
def test_accepted_projection_still_requires_typed_internal_consistency(
    tmp_path: Path, mutation: str
) -> None:
    data = payload("validation")
    cmp = data["analysis"]["comparisons"]["after"]["S"]
    if mutation == "denominator":
        cmp["correct_identified"] = 3
    elif mutation == "histogram":
        cmp["verdict_counts"] = {"unknown": 0}
    elif mutation == "false":
        cmp["false_compliance"] = 3
    elif mutation == "definite":
        cmp["verdict_counts"] = {"unknown": 2}
    elif mutation == "cache":
        data["analysis"]["planned_cache_slots"] = 2
    elif mutation == "terminal":
        data["analysis"]["status_counts"] = {"completed": 1}
    elif mutation == "cutoff":
        data["analysis"]["comparisons"] = {"PRIVATE_CANARY": {"S": cmp}}
    elif mutation == "comparator":
        data["analysis"]["comparisons"]["after"]["PRIVATE_CANARY"] = cmp
    elif mutation == "disposition":
        data["analysis"]["disposition"] = "PRIVATE_CANARY"
    else:
        data["provider_calls"] = False
    root, path, identity = write_report(tmp_path, "validation", data)
    with pytest.raises(ValueError):
        acceptance.accept_receipt(root, path, "validation", identity, replay=False)


def test_zero_comparison_is_unestimated_rate_not_division_or_success(tmp_path: Path) -> None:
    data = payload("provenance")
    data["summary"].update(
        {"episode_count": 1, "planned_load_attempts": 0, "cache_only_attempts": 1}
    )
    for value in data["summary"]["comparisons"].values():
        value.update({"correct_identified": 0, "verdict_counts": {}, "planned_denominator": 0})
    root, path, identity = write_report(tmp_path, "provenance", data)
    result = acceptance.accept_receipt(root, path, "provenance", identity, replay=False)
    assert all(item["rate"] is None for item in result["summary"]["comparisons"])


def test_repository_destination_unknown_kind_and_nonboolean_replay_are_rejected(
    tmp_path: Path,
) -> None:
    root, path, identity = write_report(tmp_path, "provenance", payload("provenance"))
    inside = root / "private.json"
    inside.write_bytes(path.read_bytes())
    with pytest.raises(ValueError):
        acceptance.accept_receipt(root, inside, "provenance", identity, replay=False)
    with pytest.raises(ValueError):
        acceptance.accept_receipt(root, path, "not-supported", identity, replay=False)
    with pytest.raises(ValueError):
        acceptance.accept_receipt(root, path, "provenance", identity, replay=1)  # type: ignore[arg-type]
