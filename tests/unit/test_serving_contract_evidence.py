"""Scoped join invariants; synthetic observations are not native qualification."""

from copy import deepcopy

import pytest

from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.evaluation.resident_footprint import capture_entry
from aletheia_lab.evaluation.serving_contract_evidence import resolve_queries
from aletheia_lab.evaluation.serving_contract_extension import CAPTURE_QUALIFICATIONS


def example():
    scope = {"__name__": "owned_arithmetic", "bias": 2.0}
    exec("def entry(x): return x * 3.0 + bias", scope)
    capture = capture_entry(
        scope["entry"],
        package_prefixes=("owned_arithmetic",),
        request_id="r0",
        generation_before="g0",
        generation_after="g0",
    )
    graph = capture["component_sha256"]["potential_graph"]
    qualification = {
        "scope_id": "finite-numeric-core",
        "receipt_sha256": digest("synthetic-test-receipt"),
        "checks": {key: True for key in CAPTURE_QUALIFICATIONS},
    }
    frame = {
        "request_id": "r0",
        "generation": "g0",
        "closed": True,
        "failed": False,
        "capture": capture,
        "qualification": qualification,
        "input_sha256": digest((4.0).hex()),
        "output": 14.0,
    }
    policy = {
        "identity_domain": "qualified_python_entry_graph",
        "scope_id": "finite-numeric-core",
        "qualification_receipts": [qualification["receipt_sha256"]],
        "authorized_graphs": [graph],
        "expected_input_sha256": frame["input_sha256"],
        "expected_output": 14.0,
        "atol": 0.0,
        "rtol": 0.0,
        "deadline_ns": 100,
        "service_queries": ["actual_used_state", "authorized_state", "numerical_correctness"],
    }
    recover(frame)
    frame["delivery"] = {
        "request_id": "r0",
        "accepted": True,
        "offered_ns": 20,
        "accepted_ns": 25,
        "delivered_ns": 30,
        "answers": {
            "actual_used_state": {"verdict": "identified", "state_sha256": graph},
            "authorized_state": "compliant",
            "numerical_correctness": "compliant",
        },
    }
    return frame, policy


def recover(frame):
    core = {key: value for key, value in frame.items() if key not in {"recovery", "delivery"}}
    if type(core.get("output")) is float and core["output"] != core["output"]:
        core["output"] = {"nonfinite_float": "nan"}
    event = {"kind": "serving_request", "seq": 1, "frame": deepcopy(core)}
    hashed = digest(event)
    frame["recovery"] = {
        "request_id": frame["request_id"],
        "event": event,
        "ack_sha256": hashed,
        "retrieved_sha256": hashed,
        "event_sha256": hashed,
        "ack_ns": 10,
    }


def test_same_ordinary_facts_answer_scoped_queries_without_reference():
    frame, policy = example()
    result = resolve_queries(frame, policy)
    assert result["answers"] == {
        "actual_used_state": "identified",
        "authorized_state": "compliant",
        "numerical_correctness": "compliant",
        "persistent_evidence": "compliant",
        "fulfilled_audit_service": "compliant",
    }
    assert result["actual_state_sha256"] == policy["authorized_graphs"][0]
    assert result["accepted_but_unserved"] is False
    assert result["host_or_hook_attested"] is False
    assert result["universal_native_state_or_use_proof"] is False


@pytest.mark.parametrize("field", ["unresolved_edges", "unknown_components"])
def test_gaps_override_an_incorrect_complete_flag_but_not_sibling_queries(field):
    frame, policy = example()
    frame["capture"][field] = {"edge": "dynamic"}
    recover(frame)
    answers = resolve_queries(frame, policy)["answers"]
    assert answers["actual_used_state"] == answers["authorized_state"] == "unknown"
    assert answers["numerical_correctness"] == answers["persistent_evidence"] == "compliant"
    assert answers["fulfilled_audit_service"] == "unknown"


@pytest.mark.parametrize("check", sorted(CAPTURE_QUALIFICATIONS))
def test_each_missing_native_qualification_blocks_identity(check):
    frame, policy = example()
    frame["qualification"]["checks"][check] = False
    recover(frame)
    assert resolve_queries(frame, policy)["answers"]["actual_used_state"] == "unknown"


def test_receipt_hash_scope_and_rule_cannot_be_inferred_from_graph():
    frame, policy = example()
    for key, value in (("receipt_sha256", digest("not-whitelisted")), ("scope_id", "other")):
        changed = deepcopy(frame)
        changed["qualification"][key] = value
        assert resolve_queries(changed, policy)["answers"]["actual_used_state"] == "unknown"
    frame["capture"]["rule"] = "made-up"
    assert resolve_queries(frame, policy)["answers"]["actual_used_state"] == "unknown"


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "other"),
        ("generation_before", "g1"),
        ("generation_after", "g1"),
        ("generation_stable", False),
    ],
)
def test_capture_association_conflict_is_not_authorized(field, value):
    frame, policy = example()
    frame["capture"][field] = value
    assert resolve_queries(frame, policy)["answers"]["authorized_state"] == "conflict"


def test_unenrolled_graph_can_violate_authorization_even_with_equal_output():
    frame, policy = example()
    frame["capture"]["component_sha256"]["potential_graph"] = digest("another-real-graph")
    recover(frame)
    answers = resolve_queries(frame, policy)["answers"]
    assert answers["authorized_state"] == "violation"
    assert answers["numerical_correctness"] == "compliant"


@pytest.mark.parametrize("closed,failed", [(False, False), (True, True)])
def test_native_failed_or_open_preserves_persistence_not_identity(closed, failed):
    frame, policy = example()
    frame.update(closed=closed, failed=failed, output=float("nan"))
    recover(frame)
    answers = resolve_queries(frame, policy)["answers"]
    assert (
        answers["actual_used_state"]
        == answers["authorized_state"]
        == answers["numerical_correctness"]
        == "unknown"
    )
    assert answers["persistent_evidence"] == "compliant"


def test_closed_nonfinite_output_is_numeric_violation_not_all_query_exception():
    frame, policy = example()
    frame["output"] = float("nan")
    recover(frame)
    answers = resolve_queries(frame, policy)["answers"]
    assert answers["numerical_correctness"] == "violation"
    assert answers["persistent_evidence"] == "compliant"


def test_numeric_spec_is_bound_to_actual_input_not_only_output():
    frame, policy = example()
    frame["input_sha256"] = digest((5.0).hex())
    assert resolve_queries(frame, policy)["answers"]["numerical_correctness"] == "conflict"


def test_recovery_checks_actual_row_frame_not_just_equal_hash_strings():
    frame, policy = example()
    frame["output"] = 999.0
    assert resolve_queries(frame, policy)["answers"]["persistent_evidence"] == "conflict"
    frame["output"] = 14.0
    frame["recovery"]["event"]["seq"] = 2
    assert resolve_queries(frame, policy)["answers"]["persistent_evidence"] == "conflict"


@pytest.mark.parametrize("field", ["ack_sha256", "retrieved_sha256", "event_sha256", "event"])
def test_missing_commit_or_recovery_cannot_fulfil_even_numeric_only_service(field):
    frame, policy = example()
    frame["recovery"][field] = None
    policy["service_queries"] = ["numerical_correctness"]
    frame["delivery"]["answers"] = {"numerical_correctness": "compliant"}
    result = resolve_queries(frame, policy)
    assert result["answers"]["fulfilled_audit_service"] == "unknown"
    assert result["accepted_but_unserved"] is True


def test_status_only_identity_delivery_does_not_deliver_actual_state():
    frame, policy = example()
    frame["delivery"]["answers"]["actual_used_state"] = "identified"
    assert resolve_queries(frame, policy)["answers"]["fulfilled_audit_service"] == "conflict"


@pytest.mark.parametrize("field", ["offered_ns", "accepted_ns", "delivered_ns"])
def test_missing_delivery_timestamp_is_local_service_unknown(field):
    frame, policy = example()
    frame["delivery"].pop(field)
    answers = resolve_queries(frame, policy)["answers"]
    assert answers["fulfilled_audit_service"] == "unknown"
    assert answers["authorized_state"] == answers["persistent_evidence"] == "compliant"


def test_late_wrong_scope_unserved_and_refused_are_not_fulfilled():
    frame, policy = example()
    frame["delivery"]["delivered_ns"] = 101
    assert resolve_queries(frame, policy)["answers"]["fulfilled_audit_service"] == "late"
    frame["delivery"]["delivered_ns"] = None
    assert resolve_queries(frame, policy)["accepted_but_unserved"] is True
    frame["delivery"].update(accepted=False, request_id="other")
    assert resolve_queries(frame, policy)["answers"]["fulfilled_audit_service"] == "conflict"
    frame["delivery"]["request_id"] = "r0"
    result = resolve_queries(frame, policy)
    assert result["answers"]["fulfilled_audit_service"] == "refused"
    assert result["accepted_but_unserved"] is False


def test_ack_after_admission_and_reversed_clocks_are_conflicts():
    frame, policy = example()
    frame["recovery"]["ack_ns"] = 26
    assert resolve_queries(frame, policy)["answers"]["fulfilled_audit_service"] == "conflict"
    frame["recovery"]["ack_ns"] = 10
    frame["delivery"]["accepted_ns"] = 31
    assert resolve_queries(frame, policy)["answers"]["fulfilled_audit_service"] == "conflict"


@pytest.mark.parametrize(
    "key,value",
    [
        ("identity_domain", "all-native-state"),
        ("atol", -1.0),
        ("expected_output", float("nan")),
        ("deadline_ns", True),
        ("service_queries", ["fulfilled_audit_service"]),
        ("expected_input_sha256", "not-hash"),
    ],
)
def test_invalid_public_policy_rejected(key, value):
    frame, policy = example()
    policy[key] = value
    with pytest.raises(ValueError):
        resolve_queries(frame, policy)


def test_reference_payload_cannot_enter_ordinary_frame():
    frame, policy = example()
    frame["_reference_used_tuple"] = [3.0, 2.0]
    with pytest.raises(ValueError, match="reference"):
        resolve_queries(frame, policy)
