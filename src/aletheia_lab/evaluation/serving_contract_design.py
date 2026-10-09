"""Read-only checks for a prospective contract-boundary transfer design.

This validates declarations, not their truth or execution. It neither infers
runtime identity from model names nor authorizes a framework experiment. A
source-informed, outcome-reserved frame is not a blind mechanism holdout.
"""

from __future__ import annotations

from typing import Any

from aletheia_lab.evaluation.request_model_audit import digest

QUERIES = (
    "actual_used_state",
    "authorized_state",
    "numerical_correctness",
    "persistent_evidence",
    "fulfilled_audit_service",
)
SCOPES = ("U1", "U2", "U3", "U4", "U5")
VIEWS = ("V0", "V1", "V2")
DELTA_VIEWS = ("V0", "V1", "V2a", "V2b")
MODEL_INPUTS = {"historical_evidence", "official_docs", "pinned_source", "exposed_language_probes"}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _texts(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_text(item) for item in value)
        and len(value) == len(set(value))
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("design object required")
    return value


def _path_check(row: dict[str, Any], *, delta: bool = False) -> None:
    for key in (
        "id",
        "framework",
        "source_family_id",
        "implementation_group_id",
        "mechanism_group_id",
        "group_rationale",
        "contract",
        "native_entry",
    ):
        _require(_text(row.get(key)), f"path {key} required")
    _require(row.get("phase") in {"development", "validation"}, "invalid path phase")
    route_only = delta and row.get("route_change") is True and row.get("update_scopes") == []
    _require(
        route_only
        or (_texts(row.get("update_scopes")) and set(row["update_scopes"]) <= set(SCOPES)),
        "unknown or duplicate update scope",
    )
    source = _object(row.get("source"))
    _require(
        _text(source.get("url"))
        and source["url"].startswith("https://")
        and _text(source.get("revision")),
        "pinned public source declaration required",
    )
    _require(
        _texts(source.get("sha256"))
        and all(
            len(value) == 64 and set(value) <= set("0123456789abcdef") for value in source["sha256"]
        ),
        "exact source byte hashes required",
    )
    exposure = _object(row.get("exposure"))
    _require(
        exposure.get("main_outcomes_observed") is False,
        "main outcomes already observed; reserve a new frame",
    )
    _require(
        exposure.get("classification")
        in {
            "source_informed_outcome_reserved",
            "source_unread_outcome_reserved",
        },
        "exposure classification required",
    )
    _require(
        isinstance(exposure.get("docs_source_read"), bool)
        and isinstance(exposure.get("mechanism_previously_exposed"), bool),
        "explicit source and mechanism exposure required",
    )
    if exposure["docs_source_read"]:
        _require(
            exposure["classification"] == "source_informed_outcome_reserved",
            "read source cannot be called blind",
        )
    qualification = _object(row.get("qualification"))
    _require(
        qualification.get("status") in {"pending", "import_only", "control_qualified"}
        and _text(qualification.get("evidence")),
        "qualification evidence required",
    )
    _require(
        qualification.get("main_update_fault_calls") == 0
        and type(qualification["main_update_fault_calls"]) is int,
        "qualification cannot execute main update/fault cases",
    )
    reference = _object(row.get("reference"))
    _require(
        reference.get("status") in {"planned", "control_qualified"}
        and _text(reference.get("method"))
        and _texts(reference.get("privileged_fields"))
        and reference.get("excluded_from_comparators") is True
        and reference.get("identity_from_output_or_current_path") is False,
        "separate reference plan required",
    )


def _prediction_check(
    row: dict[str, Any], paths: dict[str, dict[str, Any]], views: tuple[str, ...] = VIEWS
) -> None:
    _require(_text(row.get("id")), "prediction identity required")
    _require(row.get("path_id") in paths, "prediction path absent")
    _require(row.get("query") in QUERIES, "query-relative prediction required")
    for key in ("case", "expected", "falsifier", "effective_repair", "ineffective_repair"):
        _require(_text(row.get(key)), f"prediction {key} required")
    for key in ("requirements", "assumptions", "missing_witness"):
        _require(_texts(row.get(key)), f"prediction {key} required")
    _require(
        row.get("adequacy_prediction") in {"sufficient", "insufficient", "unidentified"},
        "adequacy prediction required",
    )
    _require(
        row["adequacy_prediction"] != "sufficient"
        or row["expected"].strip().casefold()
        not in {
            "unknown",
            "conflict",
            "unidentified",
        },
        "unknown/conflict is not conclusive adequacy",
    )
    _require(row.get("view") in views, "V3 requires a later development gap decision")
    view = paths[row["path_id"]]["views"][row["view"]]
    _require(
        not (
            set(row["requirements"]) & set(paths[row["path_id"]]["reference"]["privileged_fields"])
        )
        and set(row["requirements"]) <= set(view["fields"]) | set(row["missing_witness"]),
        "reference leakage or undeclared evidence requirement",
    )
    _require(
        row["adequacy_prediction"] != "sufficient"
        or set(row["requirements"]) <= set(view["fields"]),
        "a missing required witness cannot support sufficient adequacy",
    )


def inspect_design(plan: dict[str, Any]) -> dict[str, Any]:
    """Check a CP1 proposal without imports, execution, writes or authorization.

    Even a structurally valid design with qualified controls must await human
    CP1 approval and an execution seal. Self-reported qualification is not proof.
    """
    _require(
        plan.get("schema") in {"serving-contract-design/v1", "serving-contract-design/v2"},
        "unsupported design",
    )
    delta = plan["schema"] == "serving-contract-design/v2"
    views_expected = DELTA_VIEWS if delta else VIEWS
    _require(plan.get("checkpoint") == "CP1", "CP1 design required")
    _require(
        plan.get("execution_authorized") is False and plan.get("main_outcomes_observed") is False,
        "a design cannot authorize execution or contain main outcomes",
    )
    _require(
        _texts(plan.get("model_inputs")) and set(plan["model_inputs"]) <= MODEL_INPUTS,
        "prospective model must not depend on corpus v2 labels",
    )
    _require(
        plan.get("corpus_role") == "coverage_only_no_frozen_model_revision",
        "corpus/model separation required",
    )
    raw_paths = plan.get("paths")
    if not isinstance(raw_paths, list) or not 2 <= len(raw_paths) <= 18:
        raise ValueError("bounded paths required")
    paths: dict[str, dict[str, Any]] = {}
    for raw in raw_paths:
        row = _object(raw)
        _path_check(row, delta=delta)
        _require(row["id"] not in paths, "duplicate path")
        paths[row["id"]] = row
        views = _object(row.get("views"))
        _require(
            set(views) == set(views_expected),
            "explicit ordinary baseline views required"
            if delta
            else "three ordinary baseline views required",
        )
        for view in views.values():
            view = _object(view)
            _require(
                _texts(view.get("fields"))
                and _text(view.get("acquisition"))
                and view.get("same_evidence_for_all_checkers") is True,
                "explicit fair baseline evidence rights required",
            )
            _require(
                not (set(view["fields"]) & set(row["reference"]["privileged_fields"])),
                "privileged reference cannot become baseline evidence",
            )
    _require(len({row["framework"] for row in paths.values()}) <= 6, "framework cap exceeded")
    _require(
        all(
            sum(row["framework"] == name for row in paths.values()) <= 3
            for name in {row["framework"] for row in paths.values()}
        ),
        "per-framework cap exceeded",
    )
    development = [row for row in paths.values() if row["phase"] == "development"]
    validation = [row for row in paths.values() if row["phase"] == "validation"]
    _require(bool(development) and bool(validation), "both phases required")
    for key in ("implementation_group_id", "source_family_id"):
        _require(
            not ({row[key] for row in development} & {row[key] for row in validation}),
            f"development/validation {key} overlap",
        )
    claims = _object(plan.get("transfer_claims"))
    _require(
        claims.get("level") in {"operation_level_transfer", "implementation_transfer"},
        "bounded transfer level required",
    )
    _require(claims.get("blind_mechanism_discovery") is False, "no blind mechanism claim")
    raw_predictions = plan.get("predictions")
    if not isinstance(raw_predictions, list) or not 1 <= len(raw_predictions) <= 180:
        raise ValueError("bounded prospective predictions required")
    predictions = [_object(row) for row in raw_predictions]
    for row in predictions:
        _prediction_check(row, paths, views_expected)
    _require(
        len({row["id"] for row in predictions}) == len(predictions),
        "duplicate prediction",
    )
    _require({row["path_id"] for row in predictions} == set(paths), "unforecasted path")
    if delta:
        from aletheia_lab.evaluation.serving_contract_extension import inspect_extension

        inspect_extension(plan)
    census = _object(plan.get("census"))
    _require(
        census.get("retain_failures_unknown_refusal_unattempted") is True
        and census.get("outcome_retry") == "none"
        and _text(census.get("infrastructure_retry_rule")),
        "complete census/retry contract required",
    )
    pending = [
        row["id"]
        for row in paths.values()
        if row["qualification"]["status"] != "control_qualified"
        or row["reference"]["status"] != "control_qualified"
    ]
    return {
        "status": "prospective_design_checked_awaiting_CP1",
        "design_sha256": digest(plan),
        "path_count": len(paths),
        "prediction_count": len(predictions),
        "pending_control_reference_qualification": pending,
        "mechanism_overlap": sorted(
            {row["mechanism_group_id"] for row in development}
            & {row["mechanism_group_id"] for row in validation}
        ),
        "scientific_claims_verified": False,
        "execution_authorized": False,
        "execution_ready": False,
        "required_next_decision": "Bao CP1 review; qualify controls, then seal before execution",
    }
