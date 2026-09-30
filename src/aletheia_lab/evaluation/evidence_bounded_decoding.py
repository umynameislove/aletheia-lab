"""Narrow semantic decoding of bounded non-answers, with strict scoring retained.

The decoder reads response fields only, not evidence, policy arm or hidden truth.
It never removes a unique candidate or a definitive causal claim. The original
action, full candidate list and proposed check remain in the decoding audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aletheia_lab.evaluation.evidence_bounded_policy import CAUSES, PolicyDecision, assess_decision
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evidence.schema import sha256_text

DECODING_POLICY = {
    "schema_version": "bounded-nonanswer-decoding/v1",
    "scope": "supplementary development interpretation; historical strict results unchanged",
    "eligible_actions": ["abstain", "next_measurement"],
    "eligible_candidates": "empty or the complete two-cause set, with no duplicates",
    "required_causal_claims": [],
    "required_reason": "insufficient",
    "projection": {
        "candidates": "empty for the strict non-answer scoring projection only",
        "measurement": "none for abstain; original proposed measurement retained separately",
    },
    "unchanged": ["action", "causal_claims", "reason", "cited_fields"],
    "forbidden_inputs": ["policy", "context", "reference", "truth", "condition", "source"],
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate JSON field")
    return value


@dataclass(frozen=True)
class DecodedDecision:
    decision: PolicyDecision | None
    strict_valid: bool
    projected_fields: tuple[str, ...] = ()
    original_candidates: tuple[str, ...] = ()
    original_measurement: str | None = None

    def audit(self, call: DevelopmentCall) -> dict[str, Any]:
        return {
            "response_sha256": sha256_text(call.payload_json)
            if call.payload_json is not None
            else None,
            "response_utf8_bytes": len(call.payload_json.encode())
            if call.payload_json is not None
            else 0,
            "strict_valid": self.strict_valid,
            "semantic_assessable": self.decision is not None,
            "projected_fields": list(self.projected_fields),
            "original_action": self.decision.action if self.decision else None,
            "original_candidates": list(self.original_candidates),
            "original_measurement": self.original_measurement,
            "causal_assertions_removed": False,
            "raw_response_rewritten": False,
        }

    def assessment(self, context: dict[str, Any]) -> dict[str, Any]:
        result = assess_decision(context, self.decision)
        if self.decision is not None:
            result["claimed_candidates"] = list(self.original_candidates)
            result["proposed_measurement"] = self.original_measurement != "none"
            result["redundant_measurement"] = (
                result["proposed_measurement"] and result["reference"]["status"] == "resolved"
            )
        return result


def decode_policy_call(call: DevelopmentCall) -> DecodedDecision:
    """Do not infer intent from an unsafe, malformed or incomplete response."""
    invalid = DecodedDecision(None, False)
    if call.status != "completed" or call.payload_json is None:
        return invalid
    try:
        strict = PolicyDecision.model_validate_json(call.payload_json)
    except ValueError:
        strict = None
    try:
        raw = json.loads(call.payload_json, object_pairs_hook=_unique_object)
        if not isinstance(raw, dict):
            return invalid
        if strict is not None:
            return DecodedDecision(strict, True, (), strict.candidates, strict.measurement)
        if (
            raw.get("action") not in ("abstain", "next_measurement")
            or raw.get("candidates") not in ([], list(CAUSES), list(reversed(CAUSES)))
            or raw.get("causal_claims") != []
            or raw.get("reason") != "insufficient"
            or raw.get("measurement") not in ("none", "column_and_target_provenance")
            or (raw["action"] == "next_measurement" and raw["measurement"] == "none")
        ):
            return invalid
        projected = {**raw, "candidates": []}
        if raw["action"] == "abstain":
            projected["measurement"] = "none"
        decision = PolicyDecision.model_validate_json(json.dumps(projected))
        changed = tuple(k for k in ("candidates", "measurement") if raw[k] != projected[k])
        return DecodedDecision(
            decision, False, changed, tuple(raw["candidates"]), raw["measurement"]
        )
    except (ValueError, TypeError):
        return DecodedDecision(None, strict is not None)
