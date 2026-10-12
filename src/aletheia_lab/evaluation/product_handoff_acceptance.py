"""Read-only acceptance of the current deterministic product projection.

The caller supplies a trusted expected view, not an unchecked copy of the
candidate. Equality verifies projection preservation, not backing-store truth,
capture completeness, scientific correctness, or an API/dashboard execution.
The historical research handoff checker retains its separate fixture contract.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from aletheia_lab.product.view import COUNTERFACTUAL_NOT_AVAILABLE, ProductView
from aletheia_lab.project.identity import content_sha256

MAX_VIEW_BYTES = 1_048_576


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("product projection nesting exceeds the acceptance allowance")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("product projection requires string JSON keys")
            _json_value(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
    elif value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("product projection must contain only JSON values")


def _canonical(value: dict[str, Any]) -> bytes:
    if type(value) is not dict:
        raise ValueError("product projection must be a JSON object")
    _json_value(value)
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    if len(raw) > MAX_VIEW_BYTES:
        raise ValueError("product projection exceeds the acceptance allowance")
    return raw


def _metric_policy(view: ProductView) -> None:
    definitions = {item.metric_name: item for item in view.snapshot.metric_definitions}
    for change in view.snapshot.metric_changes:
        if change.before is None or change.after is None:
            continue  # ProductView checks added/removed => not_applicable.
        definition = definitions[change.metric_name]
        delta = Decimal(str(change.after.value)) - Decimal(str(change.before.value))
        worsening = delta if definition.direction == "lower_is_better" else -delta
        adverse = worsening > 0 and worsening >= Decimal(str(definition.regression_threshold))
        if change.adverse_status != ("adverse" if adverse else "not_adverse"):
            raise ValueError("product metric classification differs from its declared policy")


def _mock_scope(view: ProductView) -> None:
    if view.result.denominators.claims != len(view.claims):
        raise ValueError("product claim denominator differs from its visible census")
    if any(not turn.abstained for turn in view.conversation.turns):
        raise ValueError("deterministic project audit must retain causal abstention")
    if view.result.status == "complete" and view.result.disposition != "abstain":
        raise ValueError("deterministic project audit cannot establish a cause")
    allowed = {
        ("evidence_statement", "observed", "fully_supported", "observation"),
        ("uncertainty_statement", "abstained", "not_estimated", "abstain"),
    }
    for claim in view.claims:
        semantics = (claim.claim_type, claim.epistemic_state, claim.support, claim.max_strength)
        if semantics not in allowed:
            raise ValueError("claim exceeds the selected deterministic audit contract")
    if view.result.status == "complete":
        missing_sets = [turn.missing_evidence for turn in view.conversation.turns]
        missing_sets.extend(claim.missing_evidence for claim in view.claims)
        if any(COUNTERFACTUAL_NOT_AVAILABLE not in items for items in missing_sets):
            raise ValueError("deterministic audit omitted its counterfactual limitation")
    _metric_policy(view)


def check_product_handoff(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Check selected mock semantics and exact current JSON projection parity.

    Structural scope, reference closure and graph/citation parity belong to
    ProductView. Raw JSON arrays remain ordered: its field normalizers must not
    conceal a changed projection. Whitespace and object-key order are irrelevant.
    Free text and reproduction IDs are preserved against the trusted reference;
    this check does not authenticate their meaning or resolve referenced records.
    """

    reference_bytes, candidate_bytes = _canonical(reference), _canonical(candidate)
    for raw in (reference_bytes, candidate_bytes):
        view = ProductView.model_validate_json(raw)
        if view.model_dump(exclude_unset=True) != view.model_dump():
            raise ValueError("product projection must explicitly include every contract field")
        _mock_scope(view)
    if reference_bytes != candidate_bytes:
        raise ValueError("product projection differs from its trusted expected view")
    identity = content_sha256(reference_bytes)
    return {
        "status": "current_product_projection_acceptance_pass",
        "reference_sha256": identity,
        "candidate_sha256": identity,
        "projection_scope": "demo" if reference["demo_only"] else "backend_result",
        "independent_families": None,
        "independent_families_status": "not_estimated",
        "declared_external_call": False,
        "backend_execution": "not_attested_by_projection_check",
        "api_dashboard_integration": "not_executed",
        "scope": "selected deterministic mock semantics and exact JSON projection parity",
    }
