"""Synthetic progress contracts; no Pipeline fit, prediction or worker execution."""

import hashlib
import json
from collections import Counter
from copy import deepcopy

import pytest

from aletheia_lab.evaluation import audit_stage_journal as journal
from aletheia_lab.evaluation import pipeline_audit_progress as progress
from aletheia_lab.evaluation.calibration_audit_archive import FullCalibrationArchive

QUESTIONS = ("authorized_state", "disjoint_membership", "sigmoid_arithmetic")
SERVICE_FIELDS = (
    "offered",
    "accepted",
    "refused",
    "admission_indeterminate",
    "complete",
    "unknown",
    "late",
    "wrong",
    "accepted_but_unserved",
    "accepted_unverified",
    "accepted_service_indeterminate",
    "accepted_without_proven_service",
    "audit_unobserved",
    "native_completed",
    "native_failed",
    "retention_acknowledged",
    "accepted_retention_overruns",
    "native_indeterminate",
    "not_started",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def identity(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def config(mode="whole", count=1):
    return {"mode": mode, "count": count, "quota": 100000, "bound": 32768, "deadline_ms": 100}


def native_evidence(token="call-000", *, failed=False):
    # Zero linear margin and zero sigmoid intercept independently imply p=1/2.
    state = {
        "class": "Pipeline",
        "steps": ["scale", "classifier"],
        "memory": None,
        "preprocessing": {
            "class": "StandardScaler",
            "params": {"copy": True, "with_mean": True, "with_std": True},
            "mean": [0.0],
            "scale": [1.0],
            "features": 1,
        },
        "classifier": {
            "class": "LogisticRegression",
            "params": {},
            "coef": [[0.0]],
            "intercept": [0.0],
            "classes": [0, 1],
            "features": 1,
        },
    }
    ledger = [
        {
            "api": "pipeline.fit",
            "members": [0],
            "input": [[0.0]],
            "labels": [0],
            "returned_state": state,
        },
        {
            "api": "calibrator.fit",
            "members": [1],
            "input": [[1.0]],
            "labels": [1],
            "returned_sigmoid": {"a": -1.0, "b": 0.0},
        },
    ]
    packet = {
        "schema": "pipeline-native-evidence/v1",
        "token": token,
        "approved_state": deepcopy(state),
        "membership": {
            "base": [{"id": 0, "sha256": identity([[0.0], 0])}],
            "calibration": [{"id": 1, "sha256": identity([[1.0], 1])}],
        },
        "call": {
            "token": token,
            "input": [[0.0]],
            "actual_state": deepcopy(state),
            "sigmoid": {"a": -1.0, "b": 0.0},
            "held_base_is_enrolled": True,
            "state_stable": True,
            "closed": True,
            "failed": failed,
            "output": None if failed else [[0.5, 0.5]],
            "transform_return": None
            if failed
            else {
                "token": token,
                "step": "scale",
                "status": "returned",
                "input": [[0.0]],
                "output": [[0.0]],
                "held_scaler_is_enrolled": True,
            },
        },
    }
    return packet, ledger


class Trace:
    """Author observations directly, independently of the worker/journal writer."""

    def __init__(self, specification):
        self.ms = 0
        self.records = []
        self.append(
            "header",
            "complete",
            None,
            {
                "config": specification,
                "tokens": [f"call-{i:03d}" for i in range(specification["count"])],
            },
        )

    def append(self, stage, event, token, payload):
        self.records.append(
            {
                "seq": len(self.records),
                "previous": "",
                "stage": stage,
                "event": event,
                "token": token,
                "elapsed_ns": self.ms * 1000000,
                "journal_write_ns_before_record": 50 if self.records else 0,
                "payload": deepcopy(payload),
            }
        )

    def operation(self, stage, token, payload, *, event="complete", end_ms=None):
        self.ms += 1
        self.append(stage, "start", token, {})
        start = self.ms
        self.ms = end_ms if end_ms is not None else start + 2
        result = payload(start, self.ms) if callable(payload) else deepcopy(payload)
        self.append(stage, event, token, {"duration_ns": 100, **result})
        return self.records[-1]["payload"]


def write_trace(path, records, *, tail=b""):
    previous, lines = "0" * 64, []
    for original in records:
        record = deepcopy(original)
        record["previous"] = previous
        previous = identity(record)
        lines.append(canonical({**record, "sha256": previous}) + b"\n")
    path.write_bytes(b"".join(lines) + tail)


def expected_service(**values):
    return {field: values.get(field, 0) for field in SERVICE_FIELDS}


def timers(**values):
    fields = (
        "native_prediction_ns",
        "instrumented_prediction_ns",
        "capture_ns",
        "reserve_ack_ns",
        "write_ack_ns",
        "query_verify_ns",
        "research_witness_ns",
        "drain_ns",
        "hash_ns",
    )
    return {field: values.get(field, 0) for field in fields}


def completed_trace(directory, kind="complete"):
    specification = config()
    if kind == "refused":
        specification.update(quota=32768, bound=65536)
    elif kind == "overrun":
        specification["bound"] = 512
    trace = Trace(specification)
    packet, ledger = native_evidence(failed=kind == "native_failed")
    trace.operation("setup", None, {"fit_call_ledger": ledger})
    owner = FullCalibrationArchive(directory / "evidence.sqlite", "lru", specification["quota"])
    try:

        def reserve(start, end):
            accepted = owner.reserve(
                packet["token"], now=start, until=start + 100, bound=specification["bound"]
            )
            assert accepted is (kind != "refused")
            return {
                "accepted": accepted,
                "offered_ms": start,
                "returned_ms": end,
                "until_ms": start + 100,
            }

        decision = trace.operation("reserve", packet["token"], reserve)
        trace.operation(
            "native", packet["token"], {"packet": packet, "prediction_ns": 60, "hash_ns": 10}
        )

        def retain(start, end):
            ack = owner.put_evidence(packet, now=end)
            assert ack is (kind != "overrun")
            return {"retained_ack": ack, "ack_ms": end}

        trace.operation("retain", packet["token"], retain)
        if kind == "after_ack":
            return specification, trace.records
        if kind == "audit_indeterminate":
            trace.ms += 1
            trace.append("audit", "start", packet["token"], {})
            return specification, trace.records
        answer = dict.fromkeys(
            QUESTIONS, "unknown" if kind in {"native_failed", "overrun"} else "compliant"
        )
        if kind == "wrong_unverified":
            answer["authorized_state"] = "violation"
        trace.operation(
            "audit",
            packet["token"],
            lambda start, end: {"answer": answer, "finished_ms": end},
            end_ms=decision["until_ms"] + 1 if kind == "late" else None,
        )
        if kind in {"unverified", "wrong_unverified"}:
            trace.ms += 1
            trace.append("witness", "start", packet["token"], {})
            return specification, trace.records
        trace.operation("witness", packet["token"], {"witness": owner.witness([packet["token"]])})
        trace.operation("drain", packet["token"], lambda start, end: {"sampled_ms": end})
        measured = timers(
            instrumented_prediction_ns=60,
            hash_ns=10,
            capture_ns=30,
            reserve_ack_ns=100,
            write_ack_ns=100,
            query_verify_ns=100,
            research_witness_ns=100,
            drain_ns=100,
        )
        trace.ms += 1
        trace.append(
            "terminal",
            "complete",
            None,
            {
                "provider_calls": 0,
                "workload_ns": 1000000000,
                "timers": {**measured, "progress_journal_ns": 50},
                "archive_metrics": owner.snapshot(),
                "final_witness": owner.witness([packet["token"]]),
            },
        )
        return specification, trace.records
    finally:
        owner.close()


@pytest.mark.parametrize(
    "kind,expected",
    [
        (
            "complete",
            expected_service(
                offered=1, accepted=1, complete=1, native_completed=1, retention_acknowledged=1
            ),
        ),
        (
            "refused",
            expected_service(offered=1, refused=1, native_completed=1, retention_acknowledged=1),
        ),
        (
            "native_failed",
            expected_service(
                offered=1,
                accepted=1,
                native_completed=1,
                native_failed=1,
                retention_acknowledged=1,
                unknown=1,
                accepted_but_unserved=1,
                accepted_without_proven_service=1,
            ),
        ),
        (
            "overrun",
            expected_service(
                offered=1,
                accepted=1,
                native_completed=1,
                accepted_retention_overruns=1,
                unknown=1,
                accepted_but_unserved=1,
                accepted_without_proven_service=1,
            ),
        ),
        (
            "late",
            expected_service(
                offered=1,
                accepted=1,
                native_completed=1,
                retention_acknowledged=1,
                late=1,
                accepted_but_unserved=1,
                accepted_without_proven_service=1,
            ),
        ),
        (
            "after_ack",
            expected_service(
                offered=1,
                accepted=1,
                native_completed=1,
                retention_acknowledged=1,
                audit_unobserved=1,
                accepted_but_unserved=1,
                accepted_without_proven_service=1,
            ),
        ),
        (
            "audit_indeterminate",
            expected_service(
                offered=1,
                accepted=1,
                native_completed=1,
                retention_acknowledged=1,
                audit_unobserved=1,
                accepted_service_indeterminate=1,
                accepted_without_proven_service=1,
            ),
        ),
        (
            "unverified",
            expected_service(
                offered=1,
                accepted=1,
                native_completed=1,
                retention_acknowledged=1,
                accepted_unverified=1,
                accepted_without_proven_service=1,
            ),
        ),
        (
            "wrong_unverified",
            expected_service(
                offered=1,
                accepted=1,
                native_completed=1,
                retention_acknowledged=1,
                wrong=1,
                accepted_but_unserved=1,
                accepted_without_proven_service=1,
            ),
        ),
    ],
)
def test_reconstruction_keeps_ack_refusal_unknown_late_and_unproven_reports_in_census(
    tmp_path, kind, expected
):
    specification, records = completed_trace(tmp_path, kind)
    write_trace(tmp_path / "progress.jsonl", records)
    interrupted = kind in {"after_ack", "audit_indeterminate", "unverified", "wrong_unverified"}
    result = progress.rebuild(tmp_path, specification, interrupted=interrupted)
    assert result["service"] == expected
    assert result["record_count"] == len(records)
    assert result["journal_bytes"] == (tmp_path / "progress.jsonl").stat().st_size
    assert result["failed_operation_ns"] == 0
    assert result["trailing_bytes"] == 0
    assert result["trailing_sha256"] == identity("")
    assert result["token_census"][0]["accepted"] is bool(expected["accepted"])
    assert result["token_census"][0]["complete"] is bool(expected["complete"])
    if not interrupted:
        assert result["record_count"] == 16
        assert result["completed_stage_timers"] == timers(
            instrumented_prediction_ns=60,
            hash_ns=10,
            capture_ns=30,
            reserve_ack_ns=100,
            write_ack_ns=100,
            query_verify_ns=100,
            research_witness_ns=100,
            drain_ns=100,
        )


@pytest.mark.parametrize("failed_stage", ["reserve", "native"])
def test_error_preserves_offered_accepted_and_never_started_tokens(tmp_path, failed_stage):
    specification = config(count=2)
    trace = Trace(specification)
    trace.operation("setup", None, {})
    if failed_stage == "native":
        trace.operation(
            "reserve",
            "call-000",
            lambda start, end: {
                "accepted": True,
                "offered_ms": start,
                "returned_ms": end,
                "until_ms": start + 100,
            },
        )
    trace.operation(
        failed_stage, "call-000", {"error_type": "SyntheticBoundaryError"}, event="error"
    )
    write_trace(tmp_path / "progress.jsonl", trace.records)
    result = progress.rebuild(tmp_path, specification, interrupted=True)
    expected = expected_service(offered=1, audit_unobserved=1, not_started=1)
    if failed_stage == "native":
        expected.update(
            accepted=1,
            native_indeterminate=1,
            accepted_but_unserved=1,
            accepted_without_proven_service=1,
        )
    else:
        expected["admission_indeterminate"] = 1
    assert result["service"] == expected
    assert result["failed_operation_ns"] == 100
    assert result["completed_stage_timers"] == timers(
        reserve_ack_ns=100 if failed_stage == "native" else 0
    )
    assert result["terminal"] is None
    assert result["token_census"][0]["stages"][failed_stage] == "error"
    assert set(result["token_census"][1]["stages"].values()) == {"not_started"}


def test_no_journal_never_means_completed_or_excludes_unstarted_tokens(tmp_path):
    result = progress.rebuild(tmp_path, config(count=2), interrupted=True)
    assert result["service"] == expected_service(not_started=2)
    assert result["record_count"] == result["journal_bytes"] == result["failed_operation_ns"] == 0
    with pytest.raises(ValueError, match="no progress journal"):
        progress.rebuild(tmp_path, config(count=2), interrupted=False)


@pytest.mark.parametrize("mode", ["native", "hash"])
def test_native_only_observations_are_not_evidence_backed_service(tmp_path, mode):
    specification = config(mode, count=2)
    trace = Trace(specification)
    trace.operation("setup", None, {})
    for token in ("call-000", "call-001"):
        trace.operation(
            "native", token, {"prediction_ns": 60, **({"hash_ns": 10} if mode == "hash" else {})}
        )
    expected_timers = timers(
        native_prediction_ns=120,
        hash_ns=20 if mode == "hash" else 0,
        capture_ns=60 if mode == "hash" else 80,
    )
    trace.ms += 1
    trace.append(
        "terminal",
        "complete",
        None,
        {
            "timers": {**expected_timers, "progress_journal_ns": 50},
            "workload_ns": 1000000000,
            "provider_calls": 0,
            "archive_metrics": {},
            "final_witness": None,
        },
    )
    write_trace(tmp_path / "progress.jsonl", trace.records)
    result = progress.rebuild(tmp_path, specification, interrupted=False)
    assert result["service"] == expected_service(native_completed=2)
    assert result["completed_stage_timers"] == expected_timers
    assert result["record_count"] == 8


@pytest.mark.parametrize(
    "change,match",
    [
        ("accepted_type", "admission clock"),
        ("deadline", "admission clock"),
        ("offer_order", "admission clock"),
        ("retention_type", "retention ACK"),
        ("packet_token", "fit/token association"),
        ("question", "question census"),
        ("retained_packet", "retained audit proof"),
        ("false_ack", "retained audit proof"),
        ("answer", "retained frontier"),
        ("provider", "provider boundary"),
        ("terminal_admission", "durable admission census"),
        ("terminal_packet", "terminal retained native capsule"),
        ("negative_timer", "negative measured stage"),
        ("overlapping_timer", "negative measured stage"),
        ("timer_reduction", "timers differ"),
        ("nested_timer", "nested predictor/hash timer"),
    ],
)
def test_resealed_progress_cannot_change_identity_admission_proof_or_accounting(
    tmp_path, change, match
):
    specification, records = completed_trace(tmp_path)
    outcomes = {record["stage"]: record for record in records if record["event"] == "complete"}
    if change == "accepted_type":
        outcomes["reserve"]["payload"]["accepted"] = 1
    elif change == "deadline":
        outcomes["reserve"]["payload"]["until_ms"] += 1
    elif change == "offer_order":
        outcomes["reserve"]["payload"].update(offered_ms=5, returned_ms=4, until_ms=105)
    elif change == "retention_type":
        outcomes["retain"]["payload"]["retained_ack"] = 1
    elif change == "packet_token":
        outcomes["native"]["payload"]["packet"]["token"] = "foreign"
    elif change == "question":
        outcomes["audit"]["payload"]["answer"].pop(QUESTIONS[0])
    elif change in {"retained_packet", "terminal_packet"}:
        packet = outcomes["native"]["payload"]["packet"]
        packet["call"]["input"] = [[2.0]]
        packet["call"]["transform_return"].update(input=[[2.0]], output=[[2.0]])
        if change == "terminal_packet":
            rows = journal.token_progress(records, specification)
            with pytest.raises(ValueError, match=match):
                progress._terminal(
                    specification,
                    outcomes["terminal"]["payload"],
                    Counter(accepted=1, refused=0),
                    rows,
                )
            return
    elif change == "false_ack":
        outcomes["retain"]["payload"]["retained_ack"] = False
    elif change == "answer":
        outcomes["audit"]["payload"]["answer"][QUESTIONS[0]] = "violation"
    elif change == "provider":
        outcomes["terminal"]["payload"]["provider_calls"] = 1
    elif change == "terminal_admission":
        outcomes["terminal"]["payload"]["archive_metrics"]["accepted"] = 0
    elif change == "negative_timer":
        outcomes["terminal"]["payload"]["timers"]["capture_ns"] = -1
    elif change == "overlapping_timer":
        outcomes["terminal"]["payload"]["workload_ns"] = 1
    elif change == "timer_reduction":
        outcomes["terminal"]["payload"]["timers"]["query_verify_ns"] = 0
    else:
        outcomes["native"]["payload"]["prediction_ns"] = 101
    write_trace(tmp_path / "progress.jsonl", records)
    with pytest.raises(ValueError, match=match):
        progress.rebuild(tmp_path, specification, interrupted=False)


def test_admitted_packet_requires_setup_and_audit_requires_returned_retention(tmp_path):
    specification, records = completed_trace(tmp_path)
    row = journal.token_progress(records, specification)[0]
    decision = row["stages"]["reserve"]["payload"]
    with pytest.raises(ValueError, match="fit/token association"):
        progress._native_audit(specification, row, None, decision, Counter())
    packet = row["stages"]["native"]["payload"]["packet"]
    row["stages"]["audit"]["payload"]["finished_ms"] = 11
    with pytest.raises(ValueError, match="precedes retention"):
        progress._audit(
            specification, row, decision, packet, dict.fromkeys(QUESTIONS, "compliant"), Counter()
        )
    row["stages"]["retain"]["payload"]["ack_ms"] = 5
    with pytest.raises(ValueError, match="retention ACK"):
        progress._native_audit(specification, row, records[2]["payload"], decision, Counter())


@pytest.mark.parametrize(
    "change,match",
    [
        ("header", "planned census"),
        ("backward", "monotonic clock"),
        ("clock_type", "monotonic clock"),
        ("duration", "duration exceeds"),
        ("negative_duration", "duration missing"),
        ("clock_future", "clock outside"),
        ("clock_bool", "clock outside"),
        ("return_before_start", "return clock"),
        ("unstarted", "unstarted operation"),
        ("stage_order", "stage/token order"),
        ("premature_terminal", "premature completed"),
        ("after_terminal", "after terminal"),
        ("error_type", "error disposition"),
        ("sequence", "prefix identity"),
    ],
)
def test_resealed_journal_rejects_malformed_chronology_and_operation_identity(
    tmp_path, change, match
):
    specification, records = completed_trace(tmp_path)
    interrupted = False
    if change == "header":
        records[0]["payload"]["tokens"] = ["foreign"]
    elif change == "backward":
        records[3]["elapsed_ns"] = 0
    elif change == "clock_type":
        records[3]["elapsed_ns"] = True
    elif change == "duration":
        records[4]["payload"]["duration_ns"] = 2000001
    elif change == "negative_duration":
        records[4]["payload"]["duration_ns"] = -1
    elif change == "clock_future":
        records[4]["payload"]["returned_ms"] = 7
    elif change == "clock_bool":
        records[4]["payload"]["returned_ms"] = True
    elif change == "return_before_start":
        records[4]["payload"]["returned_ms"] = 3
    elif change == "unstarted":
        records[1].update(event="complete", payload={"duration_ns": 0})
    elif change == "stage_order":
        records[3]["token"] = "call-001"
    elif change == "premature_terminal":
        records = records[:3] + [records[-1]]
        records[-1]["seq"] = 3
    elif change == "after_terminal":
        records.append(deepcopy(records[-1]))
        records[-1]["seq"] = len(records) - 1
    elif change == "error_type":
        records = records[:5]
        records[-1]["event"] = "error"
        interrupted = True
    else:
        records[2]["seq"] = 99
    write_trace(tmp_path / "progress.jsonl", records)
    with pytest.raises(ValueError, match=match):
        journal.read_progress(tmp_path / "progress.jsonl", specification, interrupted=interrupted)


def test_noncanonical_json_and_incomplete_final_records_are_not_silent_success(tmp_path):
    specification, records = completed_trace(tmp_path, "after_ack")
    path = tmp_path / "progress.jsonl"
    write_trace(path, records, tail=b'{"seq":')
    rebuilt = progress.rebuild(tmp_path, specification, interrupted=True)
    assert rebuilt["trailing_bytes"] == 7
    assert rebuilt["trailing_sha256"] == identity(b'{"seq":'.hex())
    assert rebuilt["service"]["accepted_but_unserved"] == 1
    with pytest.raises(ValueError, match="unfinished record"):
        journal.read_progress(path, specification, interrupted=False)
    write_trace(path, records)
    first, *rest = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(
        json.dumps(json.loads(first), separators=(", ", ": ")).encode() + b"\n" + b"".join(rest)
    )
    with pytest.raises(ValueError, match="prefix identity/encoding"):
        journal.read_progress(path, specification, interrupted=True)
