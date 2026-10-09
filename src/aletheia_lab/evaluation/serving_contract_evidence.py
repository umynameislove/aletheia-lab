"""Query-relative ordinary joins over scoped, qualified serving observations.

The caller owns native acquisition/qualification and authorized enrollment.
This consumer checks consistency, not the truth of trusted host observations.
A graph digest identifies only the qualified Python-entry scope, not arbitrary
framework/native state. No fault labels or evaluator reference enter this API.
"""

from __future__ import annotations

import math
from typing import Any

from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.evaluation.serving_contract_design import QUERIES
from aletheia_lab.evaluation.serving_contract_extension import CAPTURE_QUALIFICATIONS


def _hash(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


def _text(value: Any) -> bool:
    return type(value) is str and 0 < len(value) <= 256


def _finite(value: Any) -> bool:
    if type(value) is int:
        return value.bit_length() <= 1023
    return type(value) is float and math.isfinite(value)


def _timestamp(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("nonnegative same-clock offer/delivery/deadline required")
    return int(value)


def _state(frame: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str]:
    capture = frame.get("capture")
    qualification = frame.get("qualification")
    if type(capture) is not dict or type(qualification) is not dict:
        return "unknown", "unknown"
    qualified = (
        capture.get("schema") == "declared-resident-capture/v1"
        and capture.get("rule") == "python-entry-bounded-graph/v1"
        and qualification.get("scope_id") == policy["scope_id"]
        and _hash(qualification.get("receipt_sha256"))
        and qualification["receipt_sha256"] in policy["qualification_receipts"]
        and type(qualification.get("checks")) is dict
        and set(qualification["checks"]) == CAPTURE_QUALIFICATIONS
        and all(value is True for value in qualification["checks"].values())
    )
    if not qualified or capture.get("projection_complete") is not True:
        return "unknown", "unknown"
    if (
        type(capture.get("unresolved_edges")) is not dict
        or type(capture.get("unknown_components")) is not dict
        or capture["unresolved_edges"]
        or capture["unknown_components"]
    ):
        return "unknown", "unknown"
    hashes = capture.get("component_sha256")
    if type(hashes) is not dict:
        return "unknown", "unknown"
    observed = hashes.get("potential_graph")
    if not _hash(observed):
        return "unknown", "unknown"
    if (
        capture.get("request_id") != frame["request_id"]
        or capture.get("generation_before") != frame["generation"]
        or capture.get("generation_after") != frame["generation"]
        or capture.get("generation_stable") is not True
    ):
        return "conflict", "conflict"
    if capture.get("status") != "captured_declared_components":
        return "unknown", "unknown"
    # Authorization is a public contract; the reference's consumed tuple is absent.
    authorized = observed in policy["authorized_graphs"]
    return "identified", "compliant" if authorized else "violation"


def _numerical(frame: dict[str, Any], policy: dict[str, Any]) -> str:
    output, expected = frame.get("output"), policy.get("expected_output")
    if output is None or expected is None:
        return "unknown"
    operand = frame.get("input_sha256")
    if operand is None:
        return "unknown"
    if not _hash(operand):
        raise ValueError("actual numeric operand identity required")
    if operand != policy["expected_input_sha256"]:
        return "conflict"
    if type(output) is float and not math.isfinite(output):
        return "violation"
    if not _finite(output):
        return "unknown"
    return (
        "compliant"
        if math.isclose(output, expected, abs_tol=policy["atol"], rel_tol=policy["rtol"])
        else "violation"
    )


def _persistent(frame: dict[str, Any]) -> str:
    recovery = frame.get("recovery")
    if type(recovery) is not dict:
        return "unknown"
    # Compare actual ACK and recovered row, not journal-mode or enabled flags.
    if recovery.get("request_id") != frame["request_id"]:
        return "conflict"
    ack, retrieved = recovery.get("ack_sha256"), recovery.get("retrieved_sha256")
    if ack is None or retrieved is None or recovery.get("event_sha256") is None:
        return "unknown"
    if not _hash(ack) or not _hash(retrieved) or not _hash(recovery.get("event_sha256")):
        raise ValueError("actual committed/retrieved event digests required")
    event = recovery.get("event")
    if type(event) is not dict or event.get("kind") != "serving_request":
        return "unknown"
    if event.get("frame") != _event_frame(frame):
        return "conflict"
    return (
        "compliant" if ack == retrieved == recovery["event_sha256"] == digest(event) else "conflict"
    )


def _event_frame(frame: dict[str, Any]) -> dict[str, Any]:
    canonical = {key: value for key, value in frame.items() if key not in {"recovery", "delivery"}}
    if type(canonical.get("output")) is float and not math.isfinite(canonical["output"]):
        canonical["output"] = {"nonfinite_float": canonical["output"].hex()}
    return canonical


def _delivery_timing(delivery: dict[str, Any], recovery: dict[str, Any], deadline_ns: int) -> str:
    timing = (
        delivery.get("offered_ns"),
        delivery.get("accepted_ns"),
        delivery.get("delivered_ns"),
        deadline_ns,
    )
    if any(value is None for value in timing):
        return "unknown"
    offered, accepted, delivered, deadline = (_timestamp(value) for value in timing)
    ack_ns = recovery.get("ack_ns")
    if type(ack_ns) is not int or ack_ns < 0:
        return "unknown"
    if not offered <= accepted <= delivered or ack_ns > accepted:
        return "conflict"
    if delivered > deadline:
        return "late"
    return "timely"


def _service(
    frame: dict[str, Any], policy: dict[str, Any], answers: dict[str, str], state_hash: str | None
) -> str:
    delivery = frame.get("delivery")
    if type(delivery) is not dict:
        return "unknown"
    if type(delivery.get("accepted")) is not bool:
        raise ValueError("explicit admission decision required")
    if delivery.get("request_id") != frame["request_id"]:
        return "conflict"
    if not delivery["accepted"]:
        return "refused"
    if answers["persistent_evidence"] != "compliant":
        return "unknown"
    timing_status = _delivery_timing(delivery, frame["recovery"], policy["deadline_ns"])
    if timing_status != "timely":
        return timing_status
    required = policy["service_queries"]
    if any(answers[query] not in {"identified", "compliant", "violation"} for query in required):
        return "unknown"
    expected_delivery: dict[str, Any] = {query: answers[query] for query in required}
    if "actual_used_state" in required:
        expected_delivery["actual_used_state"] = {
            "verdict": answers["actual_used_state"],
            "state_sha256": state_hash,
        }
    return "compliant" if delivery.get("answers") == expected_delivery else "conflict"


def _enrolled_hashes(policy: dict[str, Any]) -> None:
    for key in ("qualification_receipts", "authorized_graphs"):
        hashes = policy.get(key)
        if (
            type(hashes) is not list
            or not 0 < len(hashes) <= 128
            or any(not _hash(item) for item in hashes)
            or len(set(hashes)) != len(hashes)
        ):
            raise ValueError("bounded unique enrolled receipt/state hashes required")


def _validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("identity_domain") != "qualified_python_entry_graph":
        raise ValueError("explicit bounded Python-entry identity domain required")
    if not _text(policy.get("scope_id")):
        raise ValueError("bounded query/capture scope required")
    if not _hash(policy.get("expected_input_sha256")):
        raise ValueError("declared numeric operand identity required")
    _enrolled_hashes(policy)
    for key in ("atol", "rtol"):
        if not _finite(policy.get(key)) or policy[key] < 0:
            raise ValueError("prospectively declared nonnegative tolerance required")
    if policy.get("expected_output") is not None and not _finite(policy["expected_output"]):
        raise ValueError("finite declared numeric specification required")
    if type(policy.get("deadline_ns")) is not int or policy["deadline_ns"] < 0:
        raise ValueError("declared same-clock audit deadline required")
    queries = policy.get("service_queries")
    if (
        type(queries) is not list
        or not queries
        or any(type(query) is not str or query not in QUERIES[:-1] for query in queries)
        or len(queries) != len(set(queries))
    ):
        raise ValueError("explicit unique nonrecursive service query set required")


def resolve_queries(frame: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Ordinary conditional joins; privilege/reference fields are not consumed.

    Qualification receipt hashes must name artifacts verified by the native
    runner, not booleans invented from projection_complete. Scoped findings are
    conditional on those acquisition/source/use assumptions and trusted host.
    """
    _validate_policy(policy)
    if set(frame) - {
        "request_id",
        "generation",
        "closed",
        "failed",
        "capture",
        "qualification",
        "input_sha256",
        "output",
        "recovery",
        "delivery",
    }:
        raise ValueError("undeclared or reference fields cannot enter ordinary evidence")
    if not _text(frame.get("request_id")) or not _text(frame.get("generation")):
        raise ValueError("actual selected request/generation association required")
    if type(frame.get("closed")) is not bool or type(frame.get("failed")) is not bool:
        raise ValueError("explicit native request terminal state required")
    actual, authorized = _state(frame, policy)
    numerical = _numerical(frame, policy) if frame["closed"] and not frame["failed"] else "unknown"
    if not frame["closed"] or frame["failed"]:
        actual = authorized = numerical = "unknown"
    answers = {
        "actual_used_state": actual,
        "authorized_state": authorized,
        "numerical_correctness": numerical,
        "persistent_evidence": _persistent(frame),
    }
    state_hash = (
        frame["capture"]["component_sha256"]["potential_graph"] if actual == "identified" else None
    )
    answers["fulfilled_audit_service"] = _service(frame, policy, answers, state_hash)
    delivery = frame.get("delivery")
    accepted = type(delivery) is dict and delivery.get("accepted") is True
    canonical = dict(frame)
    if type(frame.get("output")) is float and not math.isfinite(frame["output"]):
        canonical["output"] = {"nonfinite_float": frame["output"].hex()}
    return {
        "schema": "scoped-serving-query-findings/v1",
        "request_id": frame["request_id"],
        "scope_id": policy["scope_id"],
        "answers": answers,
        "actual_state_sha256": state_hash,
        "accepted_but_unserved": accepted and answers["fulfilled_audit_service"] != "compliant",
        "ordinary_join": True,
        "scientific_reference_consumed": False,
        "identity_domain": policy["identity_domain"],
        "universal_native_state_or_use_proof": False,
        "host_or_hook_attested": False,
        "frame_sha256": digest(canonical),
    }
