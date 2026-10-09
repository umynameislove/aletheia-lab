"""Prospective breadth and instrumentation declarations for design amendments.

These are declaration checks, not native qualification, human approval, or an
execution seal. Pending core paths remain visible and cannot become optional.
"""

from __future__ import annotations

from typing import Any

from aletheia_lab.evaluation.serving_contract_design import QUERIES

CORE = {
    "ml-inline-registry": ("MLServer", "development"),
    "ray-reconfigure": ("Ray Serve", "validation"),
    "ray-replacement": ("Ray Serve", "validation"),
    "bento-official-watcher": ("BentoML", "validation"),
    "torch-default-selection": ("TorchServe", "validation"),
}
RISKS = {
    "new_object_old_dependency",
    "same_instance_new_state",
    "replacement_refresh_boundary",
    "acquisition_timing",
    "version_selection",
    "failed_activation",
    "repair_target",
    "ack_not_fulfilment",
}
FORECAST_ORIGINS = {"doc-stated", "pinned-source-derived", "model-composed"}
CAPTURE_QUALIFICATIONS = {
    "owned_source",
    "supported_operand_types",
    "capture_through_use",
    "readonly_or_consumed_snapshot",
    "query_dependency_coverage",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("amendment object required")
    return value


def _instrumentation(row: dict[str, Any]) -> None:
    capture = _object(row.get("resident_capture"))
    for key in (
        "scope_id",
        "acquisition_time",
        "supported_types",
        "encoding",
        "synchronization",
        "opaque_state_policy",
    ):
        _require(_text(capture.get(key)), f"resident capture {key} required")
    roots = capture.get("declared_roots")
    if not isinstance(roots, list) or not roots or not all(_text(root) for root in roots):
        raise ValueError("declared resident roots required")
    _require(len(roots) == len(set(roots)), "duplicate resident root")
    _require(
        capture.get("same_selection_all_cases") is True
        and capture.get("reads_expected_or_reference") is False
        and capture.get("disk_digest_as_resident_state") is False
        and capture.get("automatic_closure_discovery") is False,
        "resident measurement must not infer reference or universal closure",
    )
    affected = capture.get("unknown_queries")
    _require(
        isinstance(affected, list)
        and bool(affected)
        and all(_text(query) for query in affected)
        and len(affected) == len(set(affected))
        and set(affected) <= set(QUERIES),
        "query-relative opaque policy required",
    )
    views = row["views"]
    a, b = set(views["V2a"]["fields"]), set(views["V2b"]["fields"])
    _require(
        a < b
        and not any(
            field.startswith("resident_") or field in {"use_tuple", "ordinary_use_tuple"}
            for field in a
        ),
        "separate lifecycle and resident views required",
    )
    _require(
        "resident_components" in b and "resident_capture_scope" in b,
        "resident capture fields absent",
    )


def _coverage(plan: dict[str, Any]) -> None:
    paths = {row["id"]: row for row in plan["paths"]}
    _require(set(CORE) <= set(paths), "P0 mechanism coverage must not coalesce")
    for row in paths.values():
        _require(row.get("priority") in {"P0", "P1", "P2"}, "path priority required")
        _instrumentation(row)
    for identifier, (framework, phase) in CORE.items():
        row = paths[identifier]
        _require(
            row.get("priority") == "P0" and row["framework"] == framework and row["phase"] == phase,
            "core priority or partition changed",
        )
        conditions = row.get("conditions")
        _require(
            isinstance(conditions, list)
            and bool(conditions)
            and all(_text(condition) for condition in conditions)
            and len(conditions) == len(set(conditions)),
            "registered unique conditions required",
        )
    conditions = set(paths["bento-official-watcher"].get("conditions", []))
    _require(
        {"external_helper", "external_model_store"} <= conditions,
        "outside-watcher acquisition contrasts required",
    )
    _require(
        paths["torch-default-selection"].get("route_change") is True
        and paths["torch-default-selection"]["update_scopes"] == [],
        "default selection is routing, not refresh",
    )
    _require(
        paths["ray-reconfigure"]["update_scopes"] == ["U5"]
        and set(paths["ray-replacement"]["update_scopes"]) == {"U3", "U2"},
        "distinct Ray refresh mechanisms required",
    )


def _risk_groups(plan: dict[str, Any]) -> None:
    risks = plan.get("risk_groups")
    if not isinstance(risks, list) or not all(isinstance(row, dict) for row in risks):
        raise ValueError("forecast risk groups required")
    _require(
        len(risks) == len(RISKS) and {row.get("id") for row in risks} == RISKS,
        "eight prospective risk groups required",
    )
    paths = {row["id"]: row for row in plan["paths"]}
    predictions = {row["id"]: row for row in plan["predictions"]}
    for row in risks:
        _require(
            row.get("priority") == "P0" and row.get("outcome") == "unobserved",
            "risk groups are prospective P0 requirements",
        )
        _require(row.get("path_id") in paths, "forecast path absent")
        _require(
            row.get("condition") in paths[row["path_id"]]["conditions"],
            "risk condition not registered on path",
        )
        linked = row.get("prediction_ids")
        if not isinstance(linked, list):
            raise ValueError("risk must link same-path same-condition forecasts")
        _require(
            bool(linked)
            and all(
                _text(value)
                and value in predictions
                and predictions[value]["path_id"] == row["path_id"]
                and predictions[value]["case"] == row["condition"]
                for value in linked
            )
            and len(linked) == len(set(linked)),
            "risk must link same-path same-condition forecasts",
        )
        for key in ("condition", "forecast", "falsifier", "assumptions"):
            _require(_text(row.get(key)), f"risk group {key} required")


def inspect_extension(plan: dict[str, Any]) -> None:
    amendment = _object(plan.get("amendment"))
    for key in ("predecessor_design_sha256", "predecessor_journal_sha256"):
        value = amendment.get(key)
        _require(
            isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef"),
            "predecessor hash required",
        )
    _require(
        amendment.get("predecessor_preserved") is True
        and amendment.get("before_main_outcomes") is True,
        "prospective history must be preserved",
    )
    _coverage(plan)
    _risk_groups(plan)
    if "fault_independent_capture" in plan:
        _capture_amendment(plan)
    cost = _object(plan.get("incremental_cost"))
    _require(
        cost.get("arms") == ["V2a", "V2b"]
        and cost.get("blocks") == 5
        and type(cost["blocks"]) is int,
        "bounded incremental paired cost required",
    )
    _require(
        cost.get("rerun_historical_layout_study") is False
        and cost.get("eligible_capture_required") is True,
        "cost must not replay layout study or fake resident capture",
    )
    _require(
        cost.get("unknown_is_fulfilled") is False and _text(cost.get("paired_order")),
        "cost service and order must be explicit",
    )


def _capture_amendment(plan: dict[str, Any]) -> None:
    policy = _object(plan["fault_independent_capture"])
    _require(
        policy.get("rule") == "python-entry-bounded-graph/v1"
        and policy.get("component_name_allowlist") is False
        and policy.get("reads_expected_or_reference") is False
        and policy.get("projection_is_used_state_proof") is False,
        "fixed-rule projection must not be an oracle or use proof",
    )
    qualifications = policy.get("required_query_qualifications")
    _require(
        isinstance(qualifications, list)
        and len(qualifications) == len(CAPTURE_QUALIFICATIONS)
        and all(isinstance(value, str) for value in qualifications)
        and set(qualifications) == CAPTURE_QUALIFICATIONS,
        "source, operands, acquisition/use and query coverage must qualify separately",
    )
    negative = _object(policy.get("negative_control"))
    _require(
        negative.get("expected") == "unknown"
        and negative.get("conclusive_wrong_is_failure") is True
        and negative.get("primary_forecast") is False,
        "unsupported control must distinguish abstention from false confidence",
    )
    _require(
        policy.get("controls_and_repetitions_excluded_from_primary") is True,
        "sanity controls cannot inflate primary prediction success",
    )
    _forecast_atoms(plan)


def _forecast_atoms(plan: dict[str, Any]) -> None:
    atoms = plan.get("forecast_atoms")
    if not isinstance(atoms, list) or not 1 <= len(atoms) <= 180:
        raise ValueError("bounded atomic forecast provenance required")
    paths = {row["id"]: row for row in plan["paths"]}
    identifiers = set()
    for value in atoms:
        atom = _object(value)
        identifier = atom.get("id")
        _require(
            _text(identifier) and identifier not in identifiers, "unique forecast atom required"
        )
        identifiers.add(identifier)
        _require(
            atom.get("path_id") in paths
            and atom.get("case") in paths[atom["path_id"]]["conditions"]
            and atom.get("query") in QUERIES,
            "forecast atom must reference registered query and condition",
        )
        _require(
            atom.get("origin") in FORECAST_ORIGINS
            and atom.get("role") in {"primary", "authored_control", "diagnostic"}
            and atom.get("outcome") == "unobserved",
            "prospective source and control partition required",
        )
        for key in ("expected", "falsifier", "source_basis"):
            _require(_text(atom.get(key)), f"atomic forecast {key} required")
