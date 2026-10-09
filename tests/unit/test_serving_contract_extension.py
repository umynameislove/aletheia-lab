"""Synthetic delta declarations do not qualify native paths or authorize runs."""

import copy

import pytest
from test_serving_contract_design import design

from aletheia_lab.evaluation.serving_contract_design import inspect_design
from aletheia_lab.evaluation.serving_contract_extension import CAPTURE_QUALIFICATIONS, CORE, RISKS


def extension():
    plan = design()
    template = plan["paths"][0]
    prediction = plan["predictions"][0]
    plan["schema"] = "serving-contract-design/v2"
    plan["paths"] = []
    plan["predictions"] = []
    for identifier, (framework, phase) in CORE.items():
        row = copy.deepcopy(template)
        row.update(
            id=identifier,
            framework=framework,
            phase=phase,
            priority="P0",
            source_family_id=framework,
            implementation_group_id=framework,
            conditions=["external_helper", "external_model_store", "prospective_condition"],
        )
        row["route_change"] = identifier == "torch-default-selection"
        row["update_scopes"] = (
            []
            if row["route_change"]
            else ["U5"]
            if identifier == "ray-reconfigure"
            else ["U3", "U2"]
            if identifier == "ray-replacement"
            else ["U2"]
        )
        row["views"] = {
            name: {
                "fields": ["request_id"]
                + (["resident_components", "resident_capture_scope"] if name == "V2b" else []),
                "acquisition": "declared actual runtime records",
                "same_evidence_for_all_checkers": True,
            }
            for name in ("V0", "V1", "V2a", "V2b")
        }
        row["resident_capture"] = {
            "scope_id": "fixed-synthetic-scope",
            "acquisition_time": "at actual call under lock",
            "supported_types": "typed scalars and arrays",
            "encoding": "typed canonical SHA256",
            "synchronization": "lock across acquisition and use",
            "opaque_state_policy": "unknown only for requiring queries",
            "declared_roots": ["state"],
            "unknown_queries": ["actual_used_state", "authorized_state"],
            "same_selection_all_cases": True,
            "reads_expected_or_reference": False,
            "disk_digest_as_resident_state": False,
            "automatic_closure_discovery": False,
        }
        plan["paths"].append(row)
        forecast = copy.deepcopy(prediction)
        forecast.update(
            id=identifier,
            path_id=identifier,
            view="V2b",
            case="prospective_condition",
            requirements=["resident_components", "request_id"],
        )
        plan["predictions"].append(forecast)
    plan["risk_groups"] = [
        {
            "id": name,
            "priority": "P0",
            "outcome": "unobserved",
            "path_id": "ml-inline-registry",
            "condition": "prospective_condition",
            "prediction_ids": ["ml-inline-registry"],
            "forecast": "declared conditional prediction",
            "falsifier": "opposite independent reference",
            "assumptions": "trusted acquired footprint",
        }
        for name in sorted(RISKS)
    ]
    plan["amendment"] = {
        "predecessor_design_sha256": "a" * 64,
        "predecessor_journal_sha256": "b" * 64,
        "predecessor_preserved": True,
        "before_main_outcomes": True,
    }
    plan["incremental_cost"] = {
        "arms": ["V2a", "V2b"],
        "blocks": 5,
        "rerun_historical_layout_study": False,
        "eligible_capture_required": True,
        "unknown_is_fulfilled": False,
        "paired_order": "alternate within paired fresh blocks",
    }
    return plan


def test_delta_has_five_paths_four_frameworks_and_no_execution_authority():
    plan = extension()
    before = copy.deepcopy(plan)
    report = inspect_design(plan)
    assert plan == before
    assert report["path_count"] == 5
    assert len({row["framework"] for row in plan["paths"]}) == 4
    assert report["execution_authorized"] is report["execution_ready"] is False


def capture_amendment():
    plan = extension()
    plan["fault_independent_capture"] = {
        "rule": "python-entry-bounded-graph/v1",
        "component_name_allowlist": False,
        "reads_expected_or_reference": False,
        "projection_is_used_state_proof": False,
        "required_query_qualifications": sorted(CAPTURE_QUALIFICATIONS),
        "negative_control": {
            "expected": "unknown",
            "conclusive_wrong_is_failure": True,
            "primary_forecast": False,
        },
        "controls_and_repetitions_excluded_from_primary": True,
    }
    plan["forecast_atoms"] = [
        {
            "id": "synthetic-atom",
            "path_id": "ml-inline-registry",
            "case": "prospective_condition",
            "query": "actual_used_state",
            "origin": "model-composed",
            "role": "primary",
            "outcome": "unobserved",
            "expected": "A concrete bound state under a separately stated contract",
            "falsifier": "Independent qualified raw state disagrees",
            "source_basis": "Fixed synthetic source and model, not outcome-derived",
        }
    ]
    return plan


def test_fixed_rule_declarations_do_not_attest_or_authorize_capture():
    report = inspect_design(capture_amendment())
    assert report["scientific_claims_verified"] is False
    assert report["execution_authorized"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "oracle",
        "hand_names",
        "use_proof",
        "missing_qualification",
        "wrong_is_success",
        "count_control",
        "source_missing",
        "already_observed",
        "atom_duplicate",
    ],
)
def test_fault_independent_amendment_rejects_false_capture_or_forecast_claims(mutation):
    plan = capture_amendment()
    policy = plan["fault_independent_capture"]
    atom = plan["forecast_atoms"][0]
    if mutation == "oracle":
        policy["reads_expected_or_reference"] = True
    elif mutation == "hand_names":
        policy["component_name_allowlist"] = True
    elif mutation == "use_proof":
        policy["projection_is_used_state_proof"] = True
    elif mutation == "missing_qualification":
        policy["required_query_qualifications"].pop()
    elif mutation == "wrong_is_success":
        policy["negative_control"]["conclusive_wrong_is_failure"] = False
    elif mutation == "count_control":
        policy["controls_and_repetitions_excluded_from_primary"] = False
    elif mutation == "source_missing":
        atom["origin"] = "independent_blind"
    elif mutation == "already_observed":
        atom["outcome"] = "supported"
    elif mutation == "atom_duplicate":
        plan["forecast_atoms"].append(copy.deepcopy(atom))
    with pytest.raises(ValueError):
        inspect_design(plan)


@pytest.mark.parametrize(
    "mutation",
    [
        "drop_core",
        "downgrade_core",
        "route_as_refresh",
        "fake_U",
        "merge_ray",
        "missing_store",
        "leaky_V2a",
        "missing_resident_field",
        "missing_roots",
        "duplicate_roots",
        "missing_timing",
        "reference_read",
        "disk_read",
        "case_specific",
        "automatic_closure",
        "missing_opaque_policy",
        "drop_risk",
        "duplicate_risk",
        "observed_risk",
        "optional_failure_risk",
        "predecessor_hash",
        "discard_history",
        "cost_layout_rerun",
        "cost_without_eligibility",
        "unknown_as_fulfilled",
        "cost_boolean_blocks",
    ],
)
def test_delta_rejects_changed_breadth_or_instrumentation_contract(mutation):
    plan = extension()
    path = plan["paths"][0]
    capture = path["resident_capture"]
    if mutation == "drop_core":
        plan["paths"].pop()
        plan["predictions"].pop()
    elif mutation == "downgrade_core":
        path["priority"] = "P2"
    elif mutation == "route_as_refresh":
        plan["paths"][-1]["update_scopes"] = ["U2"]
    elif mutation == "fake_U":
        plan["paths"][-1]["update_scopes"] = ["U6"]
    elif mutation == "merge_ray":
        plan["paths"][1]["update_scopes"] = ["U3"]
    elif mutation == "missing_store":
        plan["paths"][3]["conditions"] = ["external_helper"]
    elif mutation == "leaky_V2a":
        path["views"]["V2a"]["fields"].append("ordinary_use_tuple")
        path["views"]["V2b"]["fields"].append("ordinary_use_tuple")
    elif mutation == "missing_resident_field":
        path["views"]["V2b"]["fields"].remove("resident_capture_scope")
    elif mutation == "missing_roots":
        capture["declared_roots"] = []
    elif mutation == "duplicate_roots":
        capture["declared_roots"] *= 2
    elif mutation == "missing_timing":
        capture["acquisition_time"] = ""
    elif mutation == "reference_read":
        capture["reads_expected_or_reference"] = True
    elif mutation == "disk_read":
        capture["disk_digest_as_resident_state"] = True
    elif mutation == "case_specific":
        capture["same_selection_all_cases"] = False
    elif mutation == "automatic_closure":
        capture["automatic_closure_discovery"] = True
    elif mutation == "missing_opaque_policy":
        capture["unknown_queries"] = []
    elif mutation == "drop_risk":
        plan["risk_groups"].pop()
    elif mutation == "duplicate_risk":
        plan["risk_groups"][1] = plan["risk_groups"][0]
    elif mutation == "observed_risk":
        plan["risk_groups"][0]["outcome"] = "supported"
    elif mutation == "optional_failure_risk":
        next(r for r in plan["risk_groups"] if r["id"] == "failed_activation")["priority"] = "P1"
    elif mutation == "predecessor_hash":
        plan["amendment"]["predecessor_design_sha256"] = "invalid"
    elif mutation == "discard_history":
        plan["amendment"]["predecessor_preserved"] = False
    elif mutation == "cost_layout_rerun":
        plan["incremental_cost"]["rerun_historical_layout_study"] = True
    elif mutation == "cost_without_eligibility":
        plan["incremental_cost"]["eligible_capture_required"] = False
    elif mutation == "unknown_as_fulfilled":
        plan["incremental_cost"]["unknown_is_fulfilled"] = True
    else:
        plan["incremental_cost"]["blocks"] = True
    with pytest.raises(ValueError):
        inspect_design(plan)


@pytest.mark.parametrize(
    "change",
    [
        "unknown_query",
        "duplicate_query",
        "unknown_condition",
        "unknown_prediction",
        "other_path_prediction",
        "nonobject_risk",
        "float_counter",
    ],
)
def test_risk_coverage_cannot_be_names_without_registered_forecasts(change):
    plan = extension()
    risk = plan["risk_groups"][0]
    if change == "unknown_query":
        plan["paths"][0]["resident_capture"]["unknown_queries"] = ["arbitrary"]
    elif change == "duplicate_query":
        plan["paths"][0]["resident_capture"]["unknown_queries"] *= 2
    elif change == "unknown_condition":
        risk["condition"] = "unregistered"
    elif change == "unknown_prediction":
        risk["prediction_ids"] = ["not-a-prediction"]
    elif change == "other_path_prediction":
        risk["prediction_ids"] = ["ray-replacement"]
    elif change == "nonobject_risk":
        plan["risk_groups"][0] = "not-an-object"
    else:
        plan["paths"][0]["qualification"]["main_update_fault_calls"] = 0.0
    with pytest.raises(ValueError):
        inspect_design(plan)
